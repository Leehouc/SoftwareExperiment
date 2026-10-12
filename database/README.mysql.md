# MySQL 迁移说明

当前项目用 `data/registration.db` 做本地 SQLite 演示。下面的 MySQL 版本面向多电脑部署：MySQL 文件由数据库服务器管理，新系统后端通过 Connector/Python 连接它；学生、教师电脑通过新系统的 HTTP 服务访问，不直接打开数据库文件。

## 环境要求

- MySQL Server 8.0.16 或更新版本（推荐 MySQL 8.4）；字符集 `utf8mb4`，存储引擎 InnoDB。
- Python 3.10+。
- `mysql-connector-python`（见项目根目录 `requirements-mysql.txt`）。

这台开发电脑目前没有 `mysql` 或 `mysqld` 命令，因此尚未在本机创建服务器端 MySQL 实例。Windows 上可通过 MySQL 官方安装程序配置 MySQL Server 和 Workbench；安装方式见 [MySQL Windows 安装文档](https://dev.mysql.com/doc/refman/8.4/en/windows-installation.html)。

## 转换现有 SQLite 数据

1. 在 MySQL Workbench 连接 MySQL Server，以管理员账号打开并执行 `database/schema.mysql.sql`。脚本会创建 `course_registration` 数据库、InnoDB 表、外键、约束、触发器和查询视图。
2. 创建只供新系统后端使用的低权限账号。先在 Workbench 中设置自己的强密码，再执行下面的命令：

   ```sql
   CREATE USER 'course_app'@'localhost' IDENTIFIED BY '替换成你自己的强密码';
   GRANT SELECT, INSERT, UPDATE, DELETE ON course_registration.* TO 'course_app'@'localhost';
   ```

   如果 MySQL 在另一台服务器，把 `localhost` 改为后端服务器的固定地址或受限网段。只开放 MySQL 端口给后端服务器，不要将 3306 暴露到公网。
3. 在项目根目录安装 Python 驱动：

   ```powershell
   py -3 -m pip install -r requirements-mysql.txt
   ```

4. 在 PowerShell 设置连接参数，然后运行一次迁移：

   ```powershell
   $env:MYSQL_HOST = '127.0.0.1'
   $env:MYSQL_PORT = '3306'
   $env:MYSQL_USER = 'course_app'
   $env:MYSQL_DATABASE = 'course_registration'
   $secret = Read-Host 'MySQL 密码' -AsSecureString
   $env:MYSQL_PASSWORD = [System.Net.NetworkCredential]::new('', $secret).Password
   py -3 database\migrate_sqlite_to_mysql.py
   ```

   若 MySQL 位于另一台电脑，将 `MYSQL_HOST` 设为它在局域网中的地址。迁移脚本从 `data/registration.db` 读取全部业务表，保留主键、成绩和演示账号哈希；它只接受空的 MySQL 目标表，避免覆盖已有数据。SQLite 原文件不会删除。

迁移完成后，MySQL 里应能查到学生、教师、草稿课表、历史成绩和账单记录。可在 Workbench 执行：

```sql
USE course_registration;
SELECT * FROM v_student_report_cards;
SELECT * FROM v_course_rosters;
```

## 给已有 MySQL 数据库补充学生演示账号

迁移完成后若新增演示账号，不要重新运行迁移脚本（迁移只接受空目标库）。在项目根目录 PowerShell 中运行下面的命令；脚本会安全询问 MySQL 应用账号密码。默认连接参数为 `127.0.0.1:3306`、`course_app`、`course_registration`；若使用远程 MySQL，可先设置对应的 `MYSQL_HOST` 等环境变量。

```powershell
py -3 .\database\seed_demo_students_mysql.py
```

该脚本只补充 `student.wang`、`student.zhao`、`student.zhou`、`student.wu` 和 `student.liu` 五个账号；重复运行不会覆盖已存在的账户资料或密码。演示密码见 [学生板块说明](../student/README.md)。

## 新系统连接约定

迁移只负责数据库和数据，现有 `app.py` 仍是只读课程目录服务，没有连接注册数据库。新系统的后端要读取环境变量并使用 Connector/Python 建立连接，不能把 MySQL 管理员密码写入源代码。多台电脑应访问同一个后端 API；后端再连接 MySQL。不要让各个桌面客户端直接共用管理员数据库账号。

```python
from database.mysql_connection import transaction

with transaction() as connection:
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT student_id, full_name FROM students WHERE student_id = %s",
            ("S1001",),
        )
        student = cursor.fetchone()
    finally:
        cursor.close()
# 成功时自动提交；发生异常时自动回滚；连接自动归还连接池。
```

连接池默认大小为 10，可用 `MYSQL_POOL_SIZE` 调整（范围 1 到 32）。`MYSQL_CONNECT_TIMEOUT_SECONDS` 默认 5 秒。每个工作线程/请求从连接池取得独立连接，写入操作请用 `transaction()`，避免在多台电脑并发访问时共享同一个连接。不要把连接对象跨请求长期保存。

课程、院系、学期、先修关系和课程开设仍从只读课程目录 API 获取；MySQL 只存注册系统拥有的学生/教师账户、课表、选课、成绩和账单事务。SQLite 数据库可作为迁移前的本地演示副本保留。
