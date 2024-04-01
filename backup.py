#!/usr/bin/python3
from datetime import datetime
from pathlib import Path
from mysql.connector import errors, errorcode
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

SQL_RETRY_ATTEMPTS = 5
SecureFilePriv = '/home'
retry_errors = (
    errorcode.CR_SERVER_LOST,
    errorcode.CR_SERVER_GONE_ERROR,
    errorcode.CR_CONNECTION_ERROR,
    errorcode.CR_CONN_HOST_ERROR,
    errorcode.ER_LOCK_WAIT_TIMEOUT,
    errorcode.ER_QUERY_INTERRUPTED,
)


def die(message):
    logging.critical(message)
    raise ValueError(message)


class Backup:
    ignore_databases = ['information_schema', 'performance_schema', 'sys', 'mysql']
    inline_sql = "FIELDS TERMINATED BY ';' OPTIONALLY ENCLOSED BY '\"' LINES TERMINATED BY '\\n'"
    nice = 'nice -n 15 ionice -c2 -n5'
    backup_dir = Path('/srv/backups')
    db_config = {}
    sql_retry_attempts = SQL_RETRY_ATTEMPTS
    weekday_limit = 10
    sunday_limit = 4
    mysql_config_file = Path("~/.my.cnf").expanduser()
    SecureFilePriv = Path(SecureFilePriv)
    conn = None
    cursor = None

    def __init__(self, **kwargs):
        self.config_file_path = Path(kwargs.get('config') or self.mysql_config_file)
        self.read_config_file()
        self.rocksdb = kwargs.get('rocksdb')
        self.debug = kwargs.get('debug')
        self.as_csv = kwargs.get('as_csv')
        self.db_names = kwargs.get('db_names')
        self.oft = kwargs.get('oft')
        self.fast = kwargs.get('fast')
        self.separate_index = not kwargs.get('nli')
        self.dry_run = kwargs.get('dry_run')
        self.exclude = self.set_regexp(kwargs.get('exclude'), 'exclude')
        self.include = self.set_regexp(kwargs.get('include'), 'include')
        self.log = kwargs.get('log')
        self.engine = self.change_engine(kwargs.get('engine'))
        self.output = self.test_directory(kwargs.get('output'))
        self.path = '' if self.output else self.test_directory(kwargs.get('save'))
        logging.debug(self.connection_settings())
        if not self.db_config:
            die("MySQL configuration not found")

    @staticmethod
    def test_directory(path):
        if path:
            path = Path(path)
            if not (path.is_dir() or path.parent.is_dir()):
                die(f"Folder {path} does not exist.")
            return path

    @staticmethod
    def change_engine(engine):
        if engine and not engine.upper() in ['INNODB', 'ROCKSDB', 'ROCKSDB', 'Aria', 'MyISAM', 'MRG_MyISAM']:
            print('Warning: engine not identified. Use it on your risk.')
        return engine

    @staticmethod
    def set_regexp(regexp, title):
        try:
            return re.compile(regexp) if regexp else None
        except Exception as e:
            die(f"Can no compile {title} pattern:{e}")

    def __enter__(self):
        self.connect_to_database()
        self.sql("SHOW VARIABLES like 'secure_file_priv'")
        mysql_secure_file_priv = self.cursor.fetchone()[1]
        if not mysql_secure_file_priv:
            die("`secure_file_priv` is not configured in mysql config file.")
        if Path(mysql_secure_file_priv) != self.SecureFilePriv:
            die("set `secure_file_priv` in [backup] section of config file or use /home as default")
        return self

    def connect_to_database(self):
        self.conn = mysql.connector.connect(**self.db_config)
        self.cursor = self.conn.cursor()
        self.sql("SET SESSION wait_timeout = 28800")
        self.sql("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()

    def print(self, **kwargs):
        if not self.log:
            print(**kwargs)

    @staticmethod
    def execute(command):
        logging.debug(f"Executing command: {command}")
        try:
            subprocess.run(command, check=True, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            logging.critical(traceback.format_exc())
            die(f"Error running command '{command}': {e}")

    def sql(self, query):
        logging.debug(f'SQL: {query}')
        self.cursor.execute(query)

    def reconnect(self, attempt=0):
        if self.conn:
            try:
                self.conn.close()
            except Exception as e:
                logging.warning(f'Error closing MySQL connection: {e}')
        try:
            self.conn = mysql.connector.connect(**self.db_config)
            self.cursor = self.conn.cursor()
        except mysql.connector.Error as error:
            if error.errno in retry_errors and attempt < self.sql_retry_attempts:
                logging.warning(f'MySQL server error: {error}, attempting to reconnect: attempt {attempt + 1}')
                time.sleep(2 ** attempt)
                self.reconnect(attempt + 1)
                return
            logging.critical(f'Failed to reconnect to the MySQL server: {error}')
            die(error)

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
                if 'ignore' in backup:
                    self.ignore_databases += re.split(r'[,;\s]+', backup['ignore'])
                if 'nice' in backup:
                    self.nice = backup['nice']
                if 'weekday_limit' in backup:
                    self.weekday_limit = int(backup['weekday_limit'])
                if 'sunday_limit' in backup:
                    self.sunday_limit = int(backup['sunday_limit'])
                if 'path' in backup:
                    self.backup_dir = Path(backup['path'])
                if 'secure_file_priv' in backup:
                    self.SecureFilePriv = Path(backup['secure_file_priv'])
                if 'sql_retry_attempts' in backup:
                    self.sql_retry_attempts = int(backup['sql_retry_attempts'])
                if 'fast' in backup:
                    self.fast = not backup['fast'].upper() in ('YES', 'ON')
                if 'nli' in backup:
                    self.separate_index = not backup['nli'].upper() in ('YES', 'ON')
                if 'oft' in backup:
                    self.oft = backup['oft'].upper() in ('YES', 'ON')
                if 'rocksdb' in backup:
                    self.oft = backup['rocksdb'].upper() in ('YES', 'ON')
                if 'engine' in backup:
                    self.engine = backup['engine']
                if 'include' in backup:
                    self.include = self.set_regexp(backup['include'].strip("'\""), 'include')
                if 'exclude' in backup:
                    self.exclude = self.set_regexp(backup['exclude'].strip("'\""), 'exclude')

    def connection_settings(self):
        message = 'Connection settings: '
        connection_settings = {key: (value if key != 'password' else '*' * 8) for key, value in self.db_config.items()}
        for key, value in connection_settings.items():
            message += f"\t{key}: {str(value)}"
        return message

    def get_databases(self, exclude_dbs=None):
        self.sql("SHOW DATABASES")
        exclude_dbs = exclude_dbs or []
        # generate exclude patterns
        exclude_patterns = [f"^{pattern.replace('*', '.*')}$" if '*' in pattern else f"^{pattern}$" for pattern in exclude_dbs]
        return [db[0] for db in self.cursor.fetchall() if not any(re.match(pattern, db[0]) for pattern in exclude_patterns)]

    def has_rocksdb_tables(self, db_name):
        self.sql(f"SELECT count(*) FROM information_schema.tables WHERE table_schema = '{db_name}' and `engine` = 'ROCKSDB'")
        return self.cursor.fetchone()[0] > 0

    def process(self):
        if self.db_names:
            all_database = self.get_databases(exclude_dbs=self.ignore_databases)
            databases = self.db_names.split(',')
            missing_dbs = [db for db in databases if db not in all_database]
            if missing_dbs:
                die(f"Databases absent on database server: {','.join(missing_dbs)}")
        else:
            databases = self.get_databases(self.ignore_databases)
        for db_name in databases:
            table_names = self.get_tables(db_name)
            if not table_names:
                continue
            self.process_db(db_name)
        if not self.output and not self.dry_run:
            self.clean_old_backups()

    def get_tables(self, db_name):
        self.sql(f"SHOW TABLES FROM `{db_name}`")
        return [table[0] for table in self.cursor.fetchall()]

    def table_match(self, table_name):
        if self.include:
            return self.include.search(table_name)
        elif self.exclude:
            return not self.exclude.search(table_name)
        return True

    def process_db(self, db_name, attempt=0):
        try:
            start_time = time.time()
            rocksdb = self.rocksdb or self.has_rocksdb_tables(db_name) and (not self.engine or self.engine.upper() == 'ROCKSDB')
            if rocksdb and not self.separate_index:
                logging.info('Ignoring `nli` argument as exports for RocksDB')
                self.separate_index = True
            if not self.dry_run:
                if not self.log and not self.debug:
                    print(f"Backing up database: {db_name} ".ljust(60, '.'), flush=True, end='')
                else:
                    logging.info(f"Backing up '{db_name}'")
                self.cleanup_output_folder(db_name)
            # backup database structure
            tables_structures = {
                table_name: self.get_table_structure(db_name, table_name, self.separate_index, rocksdb)
                for table_name in self.get_db_tables(db_name) if self.table_match(table_name)
            }
            tables = tables_structures.keys()
            if self.dry_run:
                print(f"Would be backed up: {db_name} : {','.join(tables)}")
                return
            self.sql("START TRANSACTION WITH CONSISTENT SNAPSHOT;")
            import_sql = ''
            index_sql = ''
            load_data_sql = ''
            files = [f"{db_name}.sql"]
            total = len(tables)
            for index, table_name in enumerate(tables):
                if index == 0 or self.oft:
                    import_sql = f'CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n'
                    import_sql += f'USE `{db_name}`;\n'
                    if rocksdb:
                        import_sql += 'SET session sql_log_bin=0;\n'
                        import_sql += 'SET session rocksdb_bulk_load=1;\n\n'
                structure, indexes, primary_key = tables_structures[table_name]
                if self.engine:
                    structure = re.sub(r"ENGINE=\w+", f"ENGINE={self.engine}", structure)
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
                load_sql = f"LOAD DATA INFILE '{table_file}' INTO TABLE `{table_name}` {sql};"
                if self.fast:
                    load_data_sql += f"{load_sql}\n"
                else:
                    import_sql += f"\n{load_sql}\n"
                if indexes:
                    if self.fast:
                        index_sql += f'{indexes}\n'
                    else:
                        import_sql += f'{indexes}\n'
                if index == (total - 1) or self.oft:
                    if rocksdb:
                        import_sql += 'SET session rocksdb_bulk_load=0;\n'
                    file_name = f"{db_name}_{table_name}" if self.oft else db_name
                    sql_file = f"{file_name}.sql"
                    if self.oft:
                        import_sql += f"\nANALYZE NO_WRITE_TO_BINLOG TABLE `{table_name}`;\n\n"
                    else:
                        tables_str = ','.join(f"`{t}`" for t in tables)
                        analyze_sql = f"\nANALYZE NO_WRITE_TO_BINLOG TABLE {tables_str};\n\n"
                        if self.fast:
                            sql_file = f"1.{file_name}_structure.sql"
                            load_sql_file = f"2.{file_name}_load.sql"
                            with open(self.SecureFilePriv / load_sql_file, 'w') as file:
                                file.write(load_data_sql)
                            index_sql_file = f"3.{file_name}_index.sql"
                            with open(self.SecureFilePriv / index_sql_file, 'w') as file:
                                file.write(index_sql)
                            analyze_sql_file = f"4.{file_name}_analyze.sql"
                            with open(self.SecureFilePriv / analyze_sql_file, 'w') as file:
                                file.write(analyze_sql)
                            files = [sql_file, load_sql_file, index_sql_file, analyze_sql_file]
                        else:
                            import_sql += analyze_sql
                    with open(self.SecureFilePriv / sql_file, 'w') as file:
                        file.write(import_sql)
            self.sql("COMMIT;")
            duration = time.time() - start_time
            if not self.log and not self.debug:
                print(f"\tok {duration:7.2f}s")
            else:
                logging.info(f"Export duration: {duration:7.2f}s")
            if self.output:
                archive_name = Path(self.output)
            else:
                path = Path(self.path) if self.path else (self.backup_dir / self. get_suffix())
                archive_name = path / f"{db_name}.tgz"
            if self.oft:
                files = [f"{db_name}_{table}.sql" for table in tables]
            self.compress(archive_name, db_name, ' '.join(map(str, files)))
            self.cleanup_output_folder(db_name)
        except mysql.connector.Error as error:
            if error.errno in retry_errors and attempt < self.sql_retry_attempts:
                logging.warning(f'MySQL server error: {error}, attempting to retry database:{db_name} (attempt {attempt + 1})')
                # у випадку коли запит переривається при SELECT * INTO OUTFILE '<file_data_path>' перезапускаємо архівування бази
                time.sleep(2 ** attempt)
                self.reconnect()
                return self.process_db(db_name, attempt + 1)
            logging.critical(f'Error during SQL query execution: {error}')
            die(error)
        except Exception as error:
            logging.critical(traceback.format_exc())
            die(error)

    def cleanup_output_folder(self, db_name):
        sql_file = (self.SecureFilePriv / f"{db_name}.sql")
        if self.oft:
            for file_path in self.SecureFilePriv.glob(f"{db_name}_*.sql"):
                try:
                    file_path.unlink()
                except Exception as e:
                    print(f"Error deleting file {file_path}: {e}")
        else:
            sql_file = (self.SecureFilePriv / f"{db_name}.sql")
        if sql_file.exists():
            logging.debug(f'Removing {sql_file}')
            sql_file.unlink()
        output_folder = self.SecureFilePriv / db_name
        if output_folder.exists():
            shutil.rmtree(output_folder)

    def get_db_tables(self, db_name):
        self.sql(f"SHOW TABLES FROM `{db_name}`")
        return [t[0] for t in self.cursor.fetchall()]

    def get_table_structure(self, db_name, table_name, separate_indexes, rocksdb):
        self.sql(f"SHOW CREATE TABLE `{db_name}`.`{table_name}`")
        create_table_stmt = self.cursor.fetchone()[1]
        if separate_indexes:
            # Розділяємо CREATE TABLE на структуру та індекси
            structure_part, indexes_part, primary_key = self.separate_structure_and_indexes(create_table_stmt, rocksdb)
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
        data_file = self.SecureFilePriv / db_name / f'{table_name}.{ext}'
        sql_query = f"SELECT * INTO OUTFILE '{data_file}' {sql} FROM `{db_name}`.`{table_name}` {sort}"
        self.sql(sql_query)

    @staticmethod
    def separate_structure_and_indexes(create_stmt, rocksdb=False):
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
        if allow_unsorted and rocksdb:
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
                # Перетворення назви directory на дату
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

    def compress(self, file_name, db_name, sql_files):
        backup_dir = file_name.parent
        start_time = time.time()
        today_date = datetime.now().strftime("%Y%m%d")
        if backup_dir.exists():
            mtime = datetime.fromtimestamp(backup_dir.stat().st_mtime)
            formatted_date = mtime.strftime("%Y%m%d")
            if formatted_date != today_date:
                new_dir_name = backup_dir.parent / formatted_date
                if not new_dir_name.exists():
                    shutil.move(str(backup_dir), str(new_dir_name))
        backup_dir.mkdir(parents=True, exist_ok=True)
        if not self.log and not self.debug:
            print(f"Compressing {file_name} ".ljust(60, '.'), flush=True, end='')
        else:
            logging.info(f"Compressing {file_name}")
        command = f'{self.nice} tar -chzf {file_name} -C {self.SecureFilePriv} {db_name} {sql_files}'
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
    parser.add_argument("-c", "--config", help="Path to the config file", default=None)
    parser.add_argument("-d", "--databases", help="Names of the databases to backup split by ','", default=None)
    parser.add_argument("-s", "--save", help="Path where backups would be saved, default '/srv/backups'", default=None)
    parser.add_argument("-oft", "--one-file-per-table", help="make sql import file for each table", action="store_true")
    parser.add_argument("-nli", "--no-lazy-index", help="Keeps table schema and indexes creation together", action="store_true")
    parser.add_argument("--engine", help="Replace ENGINE in output sql file", default=None)
    parser.add_argument("--ignore", help="Ignore databases. Example: 'tmp,test*'", default=None)
    parser.add_argument("--rocksdb", help="Export for RocksDB engine", action="store_true")
    parser.add_argument("-e", "--exclude", help="Ignore tables matching the mask. Example: '^test_|_$'", default=None)
    parser.add_argument("-i", "--include", help="Only tables matching the mask. Example: '^account_|_user$'", default=None)
    parser.add_argument("-o", "--output", help="Specify output file name", default=None)
    parser.add_argument("-f", "--fast", help="For fast import: creates four sql files structure, load, index, analyze", action="store_true")
    parser.add_argument("-n", "--dry-run", help="Just show the databases that will be backed up", action="store_true")
    parser.add_argument("--csv", help="Use csv format", action="store_true")
    parser.add_argument("--debug", help="Debug mode", action="store_true")
    parser.add_argument("-l", "--log", help="path to log file", default=None)
    args = parser.parse_args()
    kwargs = {
        'as_csv': args.csv,
        'debug': args.debug,
        'rocksdb': args.rocksdb,
        'config_file': args.config,
        'db_names': args.databases,
        'save': args.save,
        'log': args.log,
        'engine': args.engine,
        'oft': args.one_file_per_table,
        'nli': args.no_lazy_index,
        'ignore': args.ignore,
        'include': args.include,
        'exclude': args.exclude,
        'fast': args.fast,
        'dry_run': args.dry_run,
        'output': args.output,
    }
    log_level = logging.DEBUG if args.debug else logging.INFO
    configure_logging(log_level, log_file=args.log)
    if args.one_file_per_table and args.fast:
        die("--one-file-per-table and --fast can`t be combined")
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
