=======
MySQL Backup Script
=======

This script provides an automated solution for backing up MySQL databases wich based on TEXT data.
It supports

- **Enhanced Backup Flexibility**: The backup feature is designed to maximize compatibility and performance across different storage engines. By default, it separates the creation of indexes from the table creation process. This approach allows for more flexible and efficient data restoration, especially beneficial for engines like RocksDB.

- **Automatic Data Compression**: After successfully backing up the databases, the script automatically compresses the output files using `tar` gzip. This compression significantly reduces the storage space required for backups, making it easier to manage and transfer backup files. 

- **Selective Database Backup**: Tailor your backup process to your specific needs by selectively backing up only certain databases. Utilize the `-d` or `--databases` option to specify which databases to include in the backup. This feature is particularly useful for managing backups in environments with multiple databases, allowing you to focus on the most critical data and optimize storage usage.

- **Ignore Tables by Mask**: Enhance your backup strategy by excluding specific tables based on naming patterns. The `-i` or `--ignore-table-mask` option allows you to define regex patterns to skip tables that match these criteria during the backup process. This capability is ideal for omitting temporary or less important tables, thereby streamlining the backup and reducing its size.

- **Cleaning Up Old Backups**: To manage disk space efficiently, the script includes an automated cleanup feature that removes older backups beyond a configurable retention period. This ensures that your storage is not overwhelmed with outdated backup files, keeping your backup storage organized and within capacity limits.

- **RocksDB Optimization**: To enhance the import performance for RocksDB, the script automatically wraps the import process with `SET session rocksdb_bulk_load=1` at the beginning and `SET session rocksdb_bulk_load=0` at the end. This enables efficient bulk loading by minimizing the number of flushes to disk, thus speeding up the import of large datasets.

- **Flexible Export Formats**: Catering to diverse needs, the script supports exporting data in two formats: as CSV files for easy data manipulation and integration, or using MySQL's `OUTFILE` export files for a straightforward database restoration. This flexibility allows users to choose the format that best suits their post-backup processing needs.

Installation
------------

Ensure the python3 installed as default python

- ``pip3 install mysql-connector-python``       # execute with root permission
- ``cd /opt``
- ``git clone https://github.com/igor-kremin/mysql_backup.git mysql_backup``
- ``chmod 755 /opt/mysql_backup/backup.py``
- ``ln -s /opt/mysql_backup/backup.py /usr/local/bin/backup.py``

run in python 3.6
------------
- ``pip3 install mysql-connector-python==8.0.17``       # for python 3.6
- ``sed -i 's|#!/usr/bin/python3.11|#!/usr/bin/python3|' /usr/local/bin/backup.py``


Upgrade
-------

- ``cd /opt/mysql_backup``
- ``git pull``


Features

- Backup individual or all databases.
- Support exclude list with wildcards to skip databases/tables.
- Backup compression with `tar`. and gzip
- Optional conversion of tables to the RocksDB engine.
- CSV format support for table data.
- Debug mode for detailed operation logging.
- Automated cleanup of old backups.
- Easy Import of Backed-Up Data 


Configuration
-------------
The script requires a configuration file (e.g., `.my.cnf`) for MySQL authentication and a directory path for storing backup files.
`.my.cnf` must be placed in the user's home directory. The `.my.cnf` file contains sensitive information such as database credentials. Therefore, it is crucial to ensure that this file has strict file permissions to prevent unauthorized access.

1. The file should be owned by the user under which the backup script runs, typically this is your own user or a dedicated backup user.

2. Set the file permissions to 600 to allow only the owner to read and write the file. This can be done using the following command:

    chmod 600 /path/to/.my.cnf

3. Store the `.my.cnf` file in a secure location, preferably in the home directory of the user running the backup script, and reference it directly in the script or via the command line arguments.

By ensuring that your `.my.cnf` file is properly secured, you reduce the risk of sensitive information being exposed to unauthorized users.


4. MySql should be configured with `secure_file_priv`
To configure `secure_file_priv`, locate your MySQL configuration file (usually my.cnf or my.ini), and add or modify the following line under the [mysqld] section:

    secure_file_priv = /path/to/your/directory

`/home` is used in script by default, it is useful for dedicated mysql server, can be changed in 'secure_file_priv' option of script config file

.. code-block:: none

    [client]
    user=<user>
    password=<password>
    socket=/run/mysql.sock

    [backup]
    exclude=Database mysql sys temp*
    nice=nice -n 15 ionice -c2 -n5
    weekday_limit=10
    sunday_limit=4
    backup_dir=/srv/backups
    secure_file_priv=/home



if any of the followed options omitted the default value would be used:
 - `nice`  - default(nice -n 15 ionice -c2 -n5)
 - `weekday_limit` - default( 10 )
 - `sunday_limit` - default( 4 )
 - `backup_dir`  - default( /srv/backups )
 - `secure_file_priv` - default (/home)


exclude
-------------
can be configured to exclude specific databases from backups, wildcards can be used.
For example:
exclude=performance_schema information_schema mysql sys temp*

weekday_limit
-------------
The script saves backups in the directories <backup_dir>/day[1-7] cyclically,
if a week has passed since the creation of the directory,
the script renames the directory on the date of creation of the directory,
variable weekday_limit indicates how many such copies should be saved.
weekday_limit = 10
means that 7 copies ( of week: day1-day7) plus additional 10 days would be saved.

sunday_limit
-------------
The copy which made on the sunday has own limit <sunday_limit>
sunday_limit = 4 means that 4 weeks would be saved.

backup_dir
----------
Folder where compressed backups would be stored. The structure of the backup directory will typically look like this:

.. code-block:: none

    backups
    ├── 20240121
    │   ├── roundcube.tgz
    │   └── wikidb.tgz
    ├── day1
    │   ├── roundcube.tgz
    │   └── wikidb.tgz
    ├── day2
    │   ├── roundcube.tgz
    │   └── wikidb.tgz
    ├── day3
    ...

Database Connection
-------------------

To establish a connection with the MySQL database, the script utilizes the parameters defined in the `[client]` section of the `.my.cnf` configuration file:

- If both `socket` and `host` are specified, the `socket` parameter is prioritized and used for the connection.
- In the absence of the `socket` parameter, the `host` parameter is used.
- If the `port` parameter is not specified, the default MySQL port 3306 is used.

This approach ensures that the script can flexibly adapt to various MySQL server configurations while maintaining secure and efficient database connections.



Command line arguments
----------------------

The script supports the following command line arguments:

- ``-c, --config``: Path to the configuration file. Defaults to ``.my.cnf`` in the user's home directory.
- ``-n, --dry-run``: Just show the databases that will be backed up.
- ``-d, --databases``: Specify a particular databases to backup split by ",". If omitted, all databases are backed up.
- ``-s, --save``: Path where backups would be saved, default '/srv/backups'.
- ``--rocksdb``: Convert the <exported>.sql file to be allowed to be imported into the RocksDB engine during backup.
- ``--csv``: Export table data in CSV format.
- ``--ignore``: : Ignore databases. Example: 'tmp,test*'.
- ``-e, --exclude``: Ignore tables matching the mask. Example: '^test_.*|_$'.
- ``-i, --include``: Only tables matching the mask. Example: '&account.*|_user$'.
- ``-oft, --one-file-per-table``: make sql import file for each table.
- ``-nli, --no-lazy-index``: Keeps table schema and indexes creation together.
- ``-f, --fast``: For fast import: creates four sql files structure, load, index, analyze.
- ``--engine``: change ENGINE string in output sql.
- ``--debug``: Enable debug mode for detailed logging.
- ``-l, --log``: Path to log file.


Usage

.. code-block:: none
    backup.py -n
    backup.py
    backup.py --databases=mydatabase1,mydatabase2
    backup.py --databases=mydatabase --config=/path/to/.my.cnf
    backup.py --databases=mydatabase --config=/path/to/.my.cnf --rocksdb --csv
    backup.py --databases=mydatabase --config=/path/to/.my.cnf --engine InnoDB
    backup.py --databases=mydatabase --engine InnoDB --oft
    backup.py -d mydatabase --oft
    backup.py -d mydatabase --engine InnoDB --include '_$'
    backup.py -d mydatabase --engine InnoDB --exclude '^product'
    backup.py --debug

Before first run
----------------
- Make sure the storage has sufficient space to store backups
- User under which backups would be executed has permission to write to the backup_dir and secure_file_priv folders.
- Make sure the secure_file_priv

If you want to use alert to telegram you have to to create Telegram bot and configure telegram-send script.
Detalis see in https://pypi.python.org/pypi/telegram-send documentation.


Automation via cron
-------------------

You can run periodically script with help of crond:

.. code-block:: none

    00 1  *  *  * root /usr/bin/flock -w 1 /var/lock/db-backup.lock -c 'echo `date`; time /usr/local/bin/backup' &>>/var/log/db-backup.log
    00 1  *  *  * root /usr/bin/flock -w 1 /var/lock/db-backup.lock -c 'echo `date`; time /usr/local/bin/backup -d database1, database2' &>>/var/log/db-backup.log


Restoring data from a backup. 
-----------------------------

To restore data from a backup, simply extract the backup archive and import the SQL file into MySQL. 
If the `secure_file_priv` setting differs from the one on the backup host, you can adjust it using `sed`. For example:

.. code-block:: none

    # Extract the backup archive to the specified directory
    tar -xf /srv/day6/mydatabase.tgz -C /secure_file_priv/

    # Adjust the path in the SQL file if necessary
    sed -i 's|/old/secure_file_priv/path|/new/secure_file_priv/path|g' /secure_file_priv/mydatabase.sql

    # Import the SQL file into MySQL
    mysql -u user_name -p < /secure_file_priv/mydatabase.sql

If you need to extract to other database - just edit head of sql file to change the database name.

Restoring data from a backup if fast option selected. 
-----------------------------------------------------
To import data parallely, you will need to install package parallel 

Debian/Ubuntu: sudo apt-get install parallel

.. code-block:: none

   sudo apt-get install parallel

CentOS/RHEL/RockyLinux: 

.. code-block:: none

   sudo yum install parallel


1. mysql -u user_name -ppassword < 1.db_name_structure.sql;
2. cat 2.db_name_load.sql | parallel --will-cite -I% mysql -u user_name -ppassword -D db_name -e "%"
3. cat 3.db_name_index.sql | parallel --will-cite -I% mysql -u user_name -ppassword -D db_name -e "%"
4. mysql -u user_name -ppassword < 4.db_name_analyze.sql

