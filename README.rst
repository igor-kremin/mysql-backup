=======
MySQL Backup Script
=======

This script provides an automated solution for backing up MySQL databases wich based on TEXT data.
It supports
 - **Enhanced Backup Flexibility**: The backup feature is designed to maximize compatibility and performance across different storage engines. By default, it separates the creation of indexes from the table creation process. This approach allows for more flexible and efficient data restoration, especially beneficial for engines like RocksDB.
 - **Automatic Data Compression**: After successfully backing up the databases, the script automatically compresses the output files using `tar -z`. This compression significantly reduces the storage space required for backups, making it easier to manage and transfer backup files. The use of `tar -z` ensures a widely compatible format that can be easily decompressed on any system.
 - **Selective Database Backup**: Tailor your backup process to your specific needs by selectively backing up only certain databases. Utilize the `-d` or `--databases` option to specify which databases to include in the backup. This feature is particularly useful for managing backups in environments with multiple databases, allowing you to focus on the most critical data and optimize storage usage.
 - **Ignore Tables by Mask**: Enhance your backup strategy by excluding specific tables based on naming patterns. The `-i` or `--ignore-table-mask` option allows you to define regex patterns to skip tables that match these criteria during the backup process. This capability is ideal for omitting temporary or less important tables, thereby streamlining the backup and reducing its size.
 - **Cleaning Up Old Backups**: To manage disk space efficiently, the script includes an automated cleanup feature that removes older backups beyond a configurable retention period. This ensures that your storage is not overwhelmed with outdated backup files, keeping your backup storage organized and within capacity limits.
 - **RocksDB Optimization**: To enhance the import performance for RocksDB, the script automatically wraps the import process with `SET session rocksdb_bulk_load=1` at the beginning and `SET session rocksdb_bulk_load=0` at the end. This enables efficient bulk loading by minimizing the number of flushes to disk, thus speeding up the import of large datasets.
 - **Flexible Export Formats**: Catering to diverse needs, the script supports exporting data in two formats: as CSV files for easy data manipulation and integration, or using MySQL's `OUTFILE` export files for a straightforward database restoration. This flexibility allows users to choose the format that best suits their post-backup processing needs.
 - **Lock Feature for MyISAM Tables**: By using the `--lock` flag, the script can lock MyISAM tables during the backup process to ensure data consistency without requiring a global read lock. This feature is particularly useful for databases using the MyISAM storage engine, providing a reliable backup without interrupting database operations.


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
- ``--lock``: Lock tables of the database during backup.
- ``-i, --ignore-table-mask``: Ignore tables matching the mask. Example: '^test_.*|_$'.
- ``-oft, --one-file-per-table``: make sql import file for each table.
- ``-nli, --no-lazy-index``: Keeps table schema and indexes creation together.
- ``--engine``: change ENGINE string in output sql.
- ``--debug``: Enable debug mode for detailed logging.
- ``-l, --log``: Path to log file.


Usage

.. code-block:: none
    backup.py -n
    backup.py
    backup.py --databases=mydatabase1,mydatabase2
    backup.py --databases=mydatabase --config=/path/to/.my.cnf
    backup.py --databases=mydatabase --config=/path/to/.my.cnf --rocksdb
    backup.py --databases=mydatabase --config=/path/to/.my.cnf --rocksdb --csv
    backup.py --databases=mydatabase --config=/path/to/.my.cnf --engine InnoDB
    backup.py --databases=mydatabase --engine InnoDB --oft
    backup.py -d mydatabase --oft
    backup.py -d mydatabase --engine InnoDB --ignore-table-mask '_$'
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


Warning: Blocking Backup Operations
-------------------
The lock option in the MySQL Backup Script ensures data consistency during the backup of a database. It locks each table for reading before backup and releases it immediately after, thus preventing any modifications during the backup process.

Data Consistency: Locks tables to prevent changes during the backup, ensuring a consistent data snapshot.

Selective Locking: Locks are applied only to the tables of the specified database, reducing the overall impact on the database server.

In summary, the lock option is a balance between maintaining data integrity and minimizing operational impact during backups. It's recommended to use it during low-activity periods for the best efficiency.

Please be aware that during the backup process of a database, write operations to tables within that database will be temporarily suspended. This suspension is necessary to ensure data consistency and integrity of the backup.

It's crucial to plan the backup during periods of low activity or outside of peak hours to minimize the impact on regular database operations.

Warning: Non-Blocking Backup Operations
-------------------
Please be aware that the backup script performs non-blocking operations. This means that the backup is executed without pausing or locking the entire database. While this approach ensures continuous access to the database during the backup process, it also has important implications, especially in environments with high transaction volumes or frequent data modifications.

Data Inconsistency Risks: As the script backs up each table individually, other tables may be updated or changed during this process. This can lead to potential data inconsistencies in the backup. For instance, if Table A is backed up at time T1 and Table B is backed up later at time T2, any interrelated changes made to these tables between T1 and T2 will not be consistently reflected in the backup.

Considerations for High-Volume Environments: In databases with high transaction volumes or frequent updates, consider the potential impact of these non-blocking backups. The backup script is well-suited for environments where data consistency requirements are not extremely strict, or where database changes are relatively infrequent.

Alternative Strategies for Critical Data: For databases where data consistency is crucial (e.g., financial systems), you might need to explore alternative backup strategies. These might include database snapshots, point-in-time backups, or brief periods of read-only access to ensure data consistency.

Regular Monitoring and Verification: Regularly monitor your backup processes and periodically verify the integrity and consistency of the backed-up data. This practice is essential to ensure that your backups meet your recovery objectives and data integrity requirements.

By understanding these aspects of the backup script's operation, you can better align its use with your organization's data integrity policies and recovery objectives.


