#!/usr/bin/python3

from datetime import datetime
from mysql.connector import errorcode
from pathlib import Path
import argparse
import configparser
import logging
import logging.handlers
import mysql.connector
import os
import pwd
import grp
import re
import shutil
import subprocess
import time
import traceback


def die(message):
    logging.critical(message)
    raise ValueError(message)


class Backup:
    exclude_databases = ['information_schema', 'performance_schema', 'sys', 'mysql']
    inline_sql = "FIELDS TERMINATED BY ';' OPTIONALLY ENCLOSED BY '\"' LINES TERMINATED BY '\\n'"
    nice = 'nice -n 15 ionice -c2 -n5'
    backup_dir = Path('/srv/backups')
    db_config = {}
    weekday_limit = 10
    sunday_limit = 4
    mysql_config_file = Path("~/.my.cnf").expanduser()
    SecureFilePriv = Path('/home')

    def __init__(self, **kwargs):
        self.rocksdb = kwargs.get('rocksdb')
        self.debug = kwargs.get('debug')
        self.lock = kwargs.get('lock')
        self.as_csv = kwargs.get('as_csv')
        self.db_names = kwargs.get('db_names')
        self.log = kwargs.get('log')
        self.config_file_path = Path(kwargs.get('config') or self.mysql_config_file)
        self.read_config_file()
        logging.debug(self.connection_settings())
        if not self.db_config:
            die("MySQL configuration not found")

    def __enter__(self):
        self.conn = mysql.connector.connect(**self.db_config)
        self.cursor = self.conn.cursor()
        self.sql("SET SESSION wait_timeout = 28800")
        self.sql("SHOW VARIABLES like 'secure_file_priv'")
        mysql_secure_file_priv = self.cursor.fetchone()[1]
        if not mysql_secure_file_priv:
            die("`secure_file_priv` is not configured in mysql config file.")
        if Path(mysql_secure_file_priv) != self.SecureFilePriv:
            die("set `secure_file_priv` in [backup] section of config file or use /home as default")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()

    def print(self, **kwargs):
        if not self.log:
            print(**kwargs)

    def execute(self, command):
        logging.debug(f"Executing command: {command}")
        try:
            subprocess.run(command, check=True, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            logging.critical(traceback.format_exc())
            die(f"Error running command '{command}': {e}")

    def sql(self, query):
        logging.debug(f'SQL: {query}')
        try:
            self.cursor.execute(query)
        except mysql.connector.Error as err:
            logging.critical(f'\nSQL: {query}')
            die(err)

    def read_config_file(self):
        if self.config_file_path.is_file():
            logging.debug(f'Reading config file {self.config_file_path}')
            config = configparser.ConfigParser()
            config.read(self.config_file_path)
            if 'client' in config:
                client = config['client']
                if 'user' in client:
                    self.db_config['user'] = client['user']
                if 'password' in client:
                    self.db_config['password'] = client['password']
                if 'socket' in client:
                    self.db_config['unix_socket'] = client['socket']
                elif 'host' in client:
                    self.db_config['host'] = client['host']
                elif 'port' in client:
                    self.db_config['port'] = client['port']
            if 'backup' in config:
                backup = config['backup']
                if 'exclude' in backup:
                    self.exclude_databases = re.split('[,;\s]+', backup['exclude'])
                if 'nice' in backup:
                    self.nice = backup['nice']
                if 'weekday_limit' in backup:
                    self.weekday_limit = int(backup['weekday_limit'])
                if 'sunday_limit' in backup:
                    self.sunday_limit = int(backup['sunday_limit'])
                if 'backup_dir' in backup:
                    self.backup_dir = Path(backup['backup_dir'])
                if 'secure_file_priv' in backup:
                    self.SecureFilePriv = Path(backup['secure_file_priv'])

    def connection_settings(self):
        message = 'Connection settings: '
        connection_settings = {key: (value if key != 'password' else '*' * 8) for key, value in self.db_config.items()}
        for key, value in connection_settings.items():
            message += f"\t{key}: {str(value)}"
        return message

    def get_databases(self, exclude_dbs):
        self.sql("SHOW DATABASES")
        # generate exclude patterns
        exclude_patterns = [f"^{pattern.replace('*', '.*')}$" if '*' in pattern else f"^{pattern}$" for pattern in exclude_dbs]
        return [db[0] for db in self.cursor if not any(re.match(pattern, db[0]) for pattern in exclude_patterns)]

    def has_rocksdb_tables(self, db_name):
        self.sql(f"SELECT count(*) FROM information_schema.tables WHERE table_schema = '{db_name}' and `engine` = 'ROCKSDB'")
        return self.cursor.fetchone()[0] > 0

    def process(self):
        databases = self.db_names.split(',') if self.db_names else self.get_databases(self.exclude_databases)
        for db_name in databases:
            try:
                rocksdb = self.rocksdb or self.has_rocksdb_tables(db_name)
                if not self.log and not self.debug:
                    print(f"Backing up database: {db_name} ".ljust(60, '.'), flush=True, end='')
                else:
                    logging.info(f"Backing up '{db_name}'")
                start_time = time.time()

                self.cleanup_output_folder(db_name)
                tables = self.get_db_tables(db_name)
                import_sql = f'CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n'
                import_sql += f' USE `{db_name}`;\n'
                # backup database structure
                tables_structures = {
                    table_name: self.get_table_structure(db_name, table_name, rocksdb) for table_name in tables
                }
                if rocksdb:
                    import_sql += 'SET session sql_log_bin=0;\n'
                    import_sql += 'SET session rocksdb_bulk_load=1;\n\n'
                if self.lock:
                    self.sql("LOCK TABLES " + ", ".join([f"`{table}` READ" for table in tables_structures.keys()]))
                else:
                    self.sql("START TRANSACTION WITH CONSISTENT SNAPSHOT;")
                for table_name in tables:
                    structure, indexes, primary_key = tables_structures[table_name]
                    charset_pattern = r"CHARSET=(\w+)(?:\s+COLLATE=\w+)?"
                    match = re.search(charset_pattern, structure)
                    charset = ('CHARACTER SET %s' % match.group(1)) if match else ''
                    import_sql += f' {table_name} '.center(60, '#') + '\n'
                    import_sql += f'DROP TABLE IF EXISTS `{table_name}`;\n'
                    import_sql += f'{structure};\n'
                    self.export_table_data(db_name, table_name, primary_key)
                    ext = 'csv' if self.as_csv else 'data'
                    csv_sql = self.inline_sql if self.as_csv else ''
                    sql = f'{charset} {csv_sql}'
                    table_file = self.SecureFilePriv / db_name / f'{table_name}.{ext}'
                    import_sql += f"\nLOAD DATA INFILE '{table_file}' INTO TABLE `{table_name}` {sql};\n\n"
                    if indexes:
                        import_sql += f'{indexes}\n'
                    import_sql += '\n'
                if rocksdb:
                    import_sql += 'SET session rocksdb_bulk_load=0;\n'
                with open(self.SecureFilePriv / f"{db_name}.sql", 'w') as file:
                    file.write(import_sql)
                duration = time.time() - start_time
                if not self.log and not self.debug:
                    print(f"\tok {duration:7.2f}s")
                else:
                    logging.info(f"Export duration: {duration:7.2f}s")
                self.compress(self.backup_dir / self. get_suffix() / f"{db_name}.tgz", db_name)
                self.cleanup_output_folder(db_name)
            except Exception as error:
                logging.critical(traceback.format_exc())
                die(error)
            finally:
                self.sql("UNLOCK TABLES;" if self.lock else "COMMIT;")

        self.clean_old_backups()

    def cleanup_output_folder(self, db_name):
        sql_file = (self.SecureFilePriv / f"{db_name}.sql")
        if sql_file.exists():
            logging.debug(f'Removing {sql_file}')
            sql_file.unlink()
        archive_folder = self.SecureFilePriv / db_name
        if archive_folder.exists():
            shutil.rmtree(archive_folder)

    def get_db_tables(self, db_name):
        self.sql(f"SHOW TABLES FROM `{db_name}`")
        return [t[0] for t in self.cursor.fetchall()]

    def get_table_structure(self, db_name, table_name, separate_indexes=True):
        self.sql(f"SHOW CREATE TABLE `{db_name}`.`{table_name}`")
        create_table_stmt = self.cursor.fetchone()[1]
        if separate_indexes:
            # Розділяємо CREATE TABLE на структуру та індекси
            structure_part, indexes_part, primary_key = self.separate_structure_and_indexes(create_table_stmt)
            return structure_part, indexes_part, primary_key
        return create_table_stmt, None, None

    def export_table_data(self, db_name, table_name, primary_key):
        archive_folder = self.SecureFilePriv / db_name
        if not archive_folder.exists():
            archive_folder.mkdir(parents=True, exist_ok=True)
            try:
                os.chown(archive_folder, pwd.getpwnam('mysql').pw_uid, grp.getgrnam('mysql').gr_gid)
            except Exception as error:
                logging.warning(f"Can not change owner of {archive_folder}: {error}")
                exit(1)
        sql = self.inline_sql if self.as_csv else ''
        ext = 'csv' if self.as_csv else 'data'
        sort = f'ORDER BY {primary_key}' if primary_key else ''
        sql_query = f"SELECT * INTO OUTFILE '{self.SecureFilePriv / db_name / f'{table_name}.{ext}'}' {sql} FROM `{db_name}`.`{table_name}` {sort}"
        self.sql(sql_query)

    @staticmethod
    def separate_structure_and_indexes(create_stmt):
        # Витягуємо назву таблиці, її структуру і індекси
        match = re.search(r'CREATE TABLE `(\w+)`\s*\((.*)\)\s*(ENGINE=[^\n]+)(.*?(/\*.*?\*/))?', create_stmt, re.DOTALL)
        if not match:
            die("Can not identify structure of CREATE TABLE")
        table_name = match.group(1)
        full_structure = match.group(2)
        table_settings = re.sub(r' AUTO_INCREMENT=\d+', '', match.group(3))
        comment = match.group(4)
        primary_key_match = re.search(r'PRIMARY KEY \(([^)]+)\)', full_structure)
        primary_key_name = primary_key_match.group(1) if primary_key_match else None

        # Розділяємо структуру на поля та індекси
        fields_and_indexes = full_structure.split(",\n  ")
        structure_fields = [field.strip() for field in fields_and_indexes if not re.match(r'KEY|INDEX|UNIQUE', field)]
        indexes = [field.strip() for field in fields_and_indexes if re.match(r'KEY|INDEX|UNIQUE', field) and 'PRIMARY KEY' not in field]
        allow_unsorted = False
        table_settings = re.sub(r'ENGINE=\w+', f'ENGINE=ROCKSDB', table_settings)
        if comment and 'PARTITION BY KEY' in comment:
            auto_increment_field = [field.split()[0].strip('`') for field in structure_fields if "AUTO_INCREMENT" in field]
            if auto_increment_field and not any(filter(lambda x: 'PRIMARY KEY' in x, structure_fields)):
                structure_fields.append(f'PRIMARY KEY (`{auto_increment_field[0]}`)')
                primary_key_name = auto_increment_field[0]
            else:
                allow_unsorted = True
        fields = ",\n  ".join(structure_fields)
        structure_part = f"CREATE TABLE `{table_name}` (\n{fields}\n) {table_settings}"
        indexes_part = "\n".join([f"ALTER TABLE `{table_name}` ADD {index};" for index in indexes])
        if allow_unsorted:
            index_str = (',\n' + ',\n'.join(indexes) + ')\n') if indexes else '\n'
            return f"""
                SET session rocksdb_bulk_load_allow_unsorted=1;
                CREATE TABLE `{table_name}` (\n{fields}{index_str} {table_settings};
                SET session rocksdb_bulk_load_allow_unsorted=0;""", None, None
        return structure_part, indexes_part, primary_key_name

    @staticmethod
    def get_suffix(day=7):
        today = datetime.now()
        date_str = today.strftime("%Y%m%d")
        week_day = today.isoweekday()
        return date_str if week_day == day else f"day{week_day}"

    def clean_old_backups(self):
        backup_path = Path(self.backup_dir)
        if not backup_path.is_dir():
            die(f"Folder {self.backup_dir} does not exist.")
        date_pattern = re.compile(r'\d{8}')
        all_directories = [folder for folder in backup_path.iterdir() if folder.is_dir() and date_pattern.fullmatch(folder.name)]
        weekdays_dirs = []
        sunday_dirs = []
        for folder in all_directories:
            try:
                # Перетворення назви каталогу на дату
                folder_date = datetime.strptime(folder.name, "%Y%m%d")
                if folder_date.weekday() == 6:  # неділя
                    sunday_dirs.append(folder)
                else:
                    weekdays_dirs.append(folder)
            except ValueError:
                # Ігнорування каталогів з некоректною назвою
                continue
        self.remove_old_directories(weekdays_dirs, self.weekday_limit)
        self.remove_old_directories(sunday_dirs, self.sunday_limit)

    @staticmethod
    def remove_old_directories(directories, limit):
        sorted_dirs = sorted(directories, key=os.path.getmtime)
        for dir_to_remove in sorted_dirs[:-limit]:
            logging.debug(f"Removing folder: {dir_to_remove}")
            shutil.rmtree(dir_to_remove)

    def compress(self, file_name, db_name):
        backup_dir = file_name.parent
        start_time = time.time()
        today_date = datetime.now().strftime("%Y%m%d")
        if backup_dir.exists():
            mtime = datetime.fromtimestamp(backup_dir.stat().st_mtime)
            formatted_date = mtime.strftime("%Y%m%d")
            if formatted_date != today_date:
                new_dir_name = backup_dir.parent / formatted_date
                shutil.move(str(backup_dir), str(new_dir_name))
        backup_dir.mkdir(parents=True, exist_ok=True)
        if not self.log and not self.debug:
            print(f"Compressing {file_name} ".ljust(60, '.'), flush=True, end='')
        else:
            logging.info(f"Compressing {file_name}")
        command = f'{self.nice} tar -chzf {file_name} -C /home {db_name} {db_name}.sql'
        self.execute(command)
        duration = time.time() - start_time
        if not self.log and not self.debug:
            print(f"\tok {duration:7.2f}s")
        else:
            logging.info(f"Compress duration {duration:7.2f}s")


def configure_logging(log_level=logging.INFO, log_file='/var/log/backup.log'):
    logger = logging.getLogger()
    logger.setLevel(log_level)
    if log_file and log_level != logging.DEBUG:
        formatter = logging.Formatter('%(asctime)s %(levelname)-7s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=1*1024*1024, backupCount=10)
    else:
        formatter = logging.Formatter('%(message)s')
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def main():
    parser = argparse.ArgumentParser(description="Backup MySQL databases")
    parser.add_argument("-с", "--config", help="Path to the config file", default=None)
    parser.add_argument("-d", "--databases", help="Names of the databases to backup split by ','", default=None)
    parser.add_argument("-l", "--log", help="path to log file", default=None)
    parser.add_argument("--rocksdb", help="Export for RocksDB engine", action="store_true")
    parser.add_argument("--csv", help="Use csv format", action="store_true")
    parser.add_argument("--lock", help="use LOCK TABLE READ instead of transaction ", action="store_true")
    parser.add_argument("--debug", help="Debug mode", action="store_true")
    args = parser.parse_args()
    kwargs = {
        'as_csv': args.csv,
        'lock': args.lock,
        'debug': args.debug,
        'rocksdb': args.rocksdb,
        'config_file': args.config,
        'db_names': args.databases,
        'log': args.log
    }
    log_level = logging.DEBUG if args.debug else logging.INFO
    configure_logging(log_level, log_file=args.log)
    with Backup(**kwargs) as backup:
        try:
            backup.process()
        except mysql.connector.Error as err:
            if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
                die("Something is wrong with your user name or password")
            elif err.errno == errorcode.ER_BAD_DB_ERROR:
                die("Database does not exist")
            else:
                logging.critical(traceback.format_exc())
                die(err)


if __name__ == "__main__":
    main()
