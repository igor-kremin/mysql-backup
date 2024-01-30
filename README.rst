=======
MySQL Backup Script
=======

This script provides an automated solution for backing up MySQL databases.
It supports
 - backup
 - compression,
 - selective database backup,
 - and cleaning up old backups.
 - prepare export for RocksDB
 - export as csv or `Exported Data Files` (MySQL OUTFILE Export Files)

Installation
------------

Ensure the python3 installed as default python

- ``pip3 install mysql-connector-python``       # run with root permission
- ``cd /opt``
- ``git clone https://github.com/igor-kremin/mysql_backup.git mysql_backup``
- ``chmod 755 /opt/mysql_backup/backup.py``
- ``ln -s /opt/mysql_backup/backup.py /usr/local/bin/backup.py``

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
    exclude=performance_schema information_schema Database mysql sys temp*
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
-------------
To connect to MySql database using the mysql-connector-python library:
and parameters defined in .my.cnf file in section [client]:
if specified `socket` and `host` the `socket` will be used, else host will be used
if `port` not specified 3306 default will be used



Command line arguments
----------------------
 -d, --database: Specify a particular database to backup. If omitted, all databases are backed up.
 -c, --config: Path to configuration file. Defaults to '.my.cnf' in user home directory.
 --rocksdb: Convert <exported>.sql file to be allowed to be imported into the RocksDB engine during backup.
 --csv: Export table data in CSV format.
 --debug: Enable debug mode for detailed logging.

Usage

.. code-block:: none
    backup.py
    backup.py --database=mydatabase
    backup.py --database=mydatabase --config=/path/to/.my.cnf
    backup.py --database=mydatabase --config=/path/to/.my.cnf --rocksdb
    backup.py --database=mydatabase --config=/path/to/.my.cnf --rocksdb --csv
    backup.py --database=mydatabase --config=/path/to/.my.cnf --rocksdb --csv
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
    00 1  *  *  * root /usr/bin/flock -w 1 /var/lock/db-backup.lock -c 'echo `date`; time /usr/local/bin/backup test' &>>/var/log/db-backup.log
