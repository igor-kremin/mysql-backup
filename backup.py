#!/usr/bin/python3.11

from datetime import datetime
from mysql.connector import errorcode
from pathlib import Path
import argparse
import configparser
import mysql.connector
import os
import re
import shutil
import subprocess
import time
import traceback


class Backup:
    exclude_databases = []
    inline_sql = "FIELDS TERMINATED BY ';' OPTIONALLY ENCLOSED BY '\"' LINES TERMINATED BY '\\n'"
    nice = 'nice -n 15 ionice -c2 -n5'
    backup_dir = Path('/srv/backup')
    db_config = {}
    weekday_limit = 10
    sunday_limit = 4
    mysql_config_file = Path("~/.my.cnf").expanduser()
    SecureFilePriv = Path('/home')

    def __init__(self, db_name=None, rocksdb=None, as_csv=False, debug=False, config_file=None, lock=None):
        self.rocksdb = rocksdb
        self.lock = lock
        self.debug = debug
        self.as_csv = as_csv
        self.db_name = db_name
        self.config_file_path = Path(config_file) if config_file else self.mysql_config_file
        if self.config_file_path.exists():
            self.read_config_file(self.config_file_path)
        if not self.db_config or self.debug:
            self.show_connection_settings()
            if not self.db_config:
                raise ValueError("MySQL configuration not found")

    def __enter__(self):
        self.conn = mysql.connector.connect(**self.db_config)
        self.cursor = self.conn.cursor()
        self.sql("SHOW VARIABLES like 'secure_file_priv'")
        mysql_secure_file_priv = self.cursor.fetchone()[1]
        if not mysql_secure_file_priv:
            raise ValueError("`secure_file_priv` is not configured in mysql config file")
        if Path(mysql_secure_file_priv) != self.SecureFilePriv:
            raise ValueError("set `secure_file_priv` in [backup] section of config file or use /home as default")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()

    def execute(self, command):
        if self.debug:
            print(f"Executing command: {command}")
        try:
            result = subprocess.run(command, check=True, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if self.debug:
                print(f"Command output: {result.stdout.decode()}")
                print(f"Command error (if any): {result.stderr.decode()}")
                print(f"Command exit code: {result.returncode}")
        except subprocess.CalledProcessError as e:
            print(f"Error running command '{command}': {e}")
            if self.debug:
                traceback.print_exc()
            raise e

    def sql(self, query):
        if self.debug:
            print(f'SQL: {query}')
        try:
            self.cursor.execute(query)
        except mysql.connector.Error as err:
            print(f'\nSQL: {query}')
            raise err

    def read_config_file(self, config_file):
        if config_file.is_file():
            if self.debug:
                print(f'Reading config file {config_file}')
            config = configparser.ConfigParser()
            config.read(config_file)
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

    def show_connection_settings(self):
        connection_settings = {key: (value if value != 'password' else '*' * 8) for key, value in self.db_config.items()}
        print('Connection settings:')
        for key, value in connection_settings.items():
            print(f"{key}: {str(value).ljust(40)}")

    def get_databases(self, exclude_dbs):
        self.sql("SHOW DATABASES")
        # generate exclude patterns
        exclude_patterns = [f"^{pattern.replace('*', '.*')}$" if '*' in pattern else f"^{pattern}$" for pattern in exclude_dbs]
        return [db[0] for db in self.cursor if not any(re.match(pattern, db[0]) for pattern in exclude_patterns)]

    def process(self):
        databases = [self.db_name] if self.db_name else self.get_databases(exclude_databases=self.exclude_databases)

        for db_name in databases:
            print(f"Backing up database: {db_name} ".ljust(60, '.'), flush=True, end='\n' if self.debug else '')
            start_time = time.time()
            self.cleanup_output_folder(db_name)
            tables = self.get_db_tables(db_name)
            import_sql = f'CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n'
            if self.rocksdb:
                import_sql += 'SET session sql_log_bin=0;\n'
                import_sql += 'SET session rocksdb_bulk_load=1;\n\n'
            # backup database structure
            tables_structures = {
                table_name: self.get_table_structure(db_name, table_name, self.rocksdb) for table_name in tables
            }
            if self.lock:
                self.sql("LOCK TABLES " + ", ".join([f"`{table}` READ" for table in tables_structures.keys()]))
            for table_name in tables:
                structure, indexes, primary_key = tables_structures[table_name]
                import_sql += f' {table_name} '.center(60, '#') + '\n'
                import_sql += f'DROP TABLE IF EXISTS `{table_name}`;\n'
                import_sql += f'{structure}\n'
                self.export_table_data(db_name, table_name, primary_key)
                ext = 'csv' if self.as_csv else 'data'
                sql = self.inline_sql if self.as_csv else ''
                table_file = self.SecureFilePriv / 'db' / f'{table_name}.{ext}'
                import_sql += f"\nLOAD DATA INFILE '{table_file}' INTO TABLE `{table_name}` {sql};\n\n"
                if indexes:
                    import_sql += f'{indexes}\n'
                import_sql += '\n'
            if self.rocksdb:
                import_sql += 'SET session rocksdb_bulk_load=0;\n'
            with open(self.SecureFilePriv / f"{db_name}.sql", 'w') as file:
                file.write(import_sql)
            if self.lock:
                self.sql("UNLOCK TABLES;")
            duration = time.time() - start_time
            if self.debug:
                print(f"Duration: {duration:.2f}s")
            else:
                print(f"\tok {duration:.2f}s")
            self.compress(self.backup_dir / self. get_suffix() / f"{db_name}.tgz", db_name)
            self.cleanup_output_folder(db_name)
        self.clean_old_backups()

    def cleanup_output_folder(self, db_name):
        sql_file = (self.SecureFilePriv / f"{db_name}.sql")
        if sql_file.exists():
            if self.debug:
                print(f'Removing {sql_file}')
            sql_file.unlink()
        [file_path.unlink() for file_path in (self.SecureFilePriv / 'db').iterdir() if file_path.is_file()]

    def get_db_tables(self, db_name):
        self.sql(f"SHOW TABLES FROM {db_name}")
        return [t[0] for t in self.cursor.fetchall()]

    def get_table_structure(self, db_name, table_name, separate_indexes=True):
        self.sql(f"SHOW CREATE TABLE {db_name}.{table_name}")
        create_table_stmt = self.cursor.fetchone()[1]
        if separate_indexes:
            # Розділяємо CREATE TABLE на структуру та індекси
            structure_part, indexes_part, primary_key = self.separate_structure_and_indexes(create_table_stmt)
            return structure_part, indexes_part, primary_key
        return create_table_stmt, None, None

    def export_table_data(self, db_name, table_name, primary_key):
        sql = self.inline_sql if self.as_csv else ''
        ext = 'csv' if self.as_csv else 'data'
        sort = f'ORDER BY {primary_key}' if primary_key else ''
        sql_query = f"SELECT * INTO OUTFILE '{self.SecureFilePriv / 'db' / f'{table_name}.{ext}'}' {sql} FROM {db_name}.{table_name} {sort}"
        self.sql(sql_query)

    def separate_structure_and_indexes(self, create_stmt):
        # Витягуємо назву таблиці, її структуру і індекси
        match = re.search(r'CREATE TABLE `(\w+)`\s*\((.*)\)\s*(ENGINE=[^\n]+)(.*?(/\*.*?\*/))?', create_stmt, re.DOTALL)
        if not match:
            raise ValueError("Can not identify structure of CREATE TABLE")
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
        if self.rocksdb:
            table_settings = re.sub(r'ENGINE=\w+', f'ENGINE=ROCKSDB', table_settings)
            if comment and 'PARTITION BY KEY' in comment:
                auto_increment_field = [field.split()[0].strip('`') for field in structure_fields if "AUTO_INCREMENT" in field]
                if auto_increment_field and not any(filter(lambda x: 'PRIMARY KEY' in x, structure_fields)):
                    structure_fields.append(f'PRIMARY KEY (`{auto_increment_field[0]}`)')
                    primary_key_name = auto_increment_field[0]
                else:
                    allow_unsorted = True
        fields = ",\n  ".join(structure_fields)
        structure_part = f"CREATE TABLE `{table_name}` (\n{fields}\n) {table_settings};"
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
            print(f"Folder {self.backup_dir} does not exist.")
            return
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
            print(f"Removing folder: {dir_to_remove}")
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
        print(f"Compressing {file_name} ".ljust(60, '.'), flush=True, end='\n' if self.debug else '')
        command = f'{self.nice} tar -chzf {file_name} -C /home db {db_name}.sql'
        self.execute(command)
        duration = time.time() - start_time
        if self.debug:
            print(f"{command} Duration {duration:.2f}s")
        else:
            print(f"\tok {duration:.2f}s")


def main():
    parser = argparse.ArgumentParser(description="Backup MySQL databases")
    parser.add_argument("-с", "--config", help="Path to the config file", default=None)
    parser.add_argument("-d", "--database", help="Name of the database to backup", default=None)
    parser.add_argument("--rocksdb", help="Export for RocksDB engine", action="store_true")
    parser.add_argument("--csv", help="Use csv format", action="store_true")
    parser.add_argument("--lock", help="Lock table READ", action="store_true")
    parser.add_argument("--debug", help="Debug mode", action="store_true")
    args = parser.parse_args()
    db_name = args.database
    as_csv = args.csv
    debug = args.debug
    lock = args.lock
    config_file = args.config

    with Backup(db_name=db_name, rocksdb=args.rocksdb, as_csv=as_csv, debug=debug, config_file=config_file, lock=lock) as backup:
        try:
            backup.process()
        except mysql.connector.Error as err:
            if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
                raise ValueError("Something is wrong with your user name or password")
            elif err.errno == errorcode.ER_BAD_DB_ERROR:
                raise ValueError("Database does not exist")
            else:
                print(err)
                traceback.print_exc()


if __name__ == "__main__":
    main()

