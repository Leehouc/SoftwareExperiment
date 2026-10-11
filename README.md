# SoftwareExperiment 课程注册系统

本项目包含两部分：**遗留课程目录只读服务**和**课程注册新系统**。课程目录由虚拟 JSON 数据模拟；学生、教师、注册管理员网页使用 MySQL 保存新系统的档案、选课、成绩和账单数据。SQLite 脚本与文件仅用于早期本地演示数据和一次性迁移。

> `app.py` 与 `dashboard.py` 提供课程目录 API 和浏览页面。`student/`、`teacher/` 和 `administer/` 是独立的学生、教师、注册管理员网页模块，连接 MySQL 并读取课程目录 API。

## 文件与目录说明

| 文件/目录 | 作用与意义 |
| --- | --- |
| `app.py` | 遗留课程目录只读 HTTP/JSON 服务。默认从 `data/catalog.json` 读取虚拟数据；可选配置为连接 Ingres。只支持 GET，不修改课程目录。默认监听 `127.0.0.1:8000`。 |
| `dashboard.py` | 课程目录浏览网页，并将请求转发给 `app.py`。默认监听 `127.0.0.1:8001`，因此必须先启动 `app.py`。 |
| `student/` | 独立的学生注册可视化网页及后端 API。支持学生登录、选择/保存/提交课表、加退选期修改和查看成绩单；默认监听 `127.0.0.1:8020`。启动前须先启动 MySQL 和 `app.py`。 |
| `teacher/` | 独立的教师工作台及后端 API。支持教师登录、选择授课课程、查看本人班级名册和录入成绩；默认监听 `127.0.0.1:8030`。启动前须先启动 MySQL 和 `app.py`。 |
| `administer/` | 独立的注册管理员工作台及后端 API。支持学生/教师档案和账户维护、关闭注册期、查看账单队列；默认监听 `127.0.0.1:8040`。启动前须先启动 MySQL 和 `app.py`。 |
| `newdatabaseTest1.0/` | MySQL 数据库只读检查网页，可查看表记录数和演示数据预览。 |
| `data/catalog.json` | 虚拟课程目录数据，包括院系、教师、学期、课程、先修关系和开课。用于没有真实 Ingres 数据库时开发和演示。 |
| `init_database.py` | 创建并填充本地 SQLite 业务数据库；虚构学生、教师、教务员、选课、成绩等演示数据在此初始化。 |
| `data/registration.db` | `init_database.py` 生成的 SQLite 业务数据库，也是迁移到 MySQL 的演示数据来源。一般不要手工编辑此二进制文件。 |
| `database/schema.sql` | SQLite 业务数据库结构，包括账户、学生/教师档案、课表、选课、成绩、账单事务、审计记录及查询视图。 |
| `database/schema.mysql.sql` | MySQL 8 建库及建表脚本，创建 `course_registration` 数据库、业务表、外键、约束、触发器和视图。 |
| `database/migrate_sqlite_to_mysql.py` | 一次性把 `data/registration.db` 的演示业务数据复制到空的 MySQL 表中；不会删除 SQLite 源文件。 |
| `database/mysql_connection.py` | 新系统后端使用的 MySQL 连接池和事务辅助模块；从 `MYSQL_*` 环境变量读取配置。写事务成功自动提交，异常自动回滚。 |
| `database/README.md` | SQLite 表结构、演示账号和课程目录边界的详细说明。 |
| `database/README.mysql.md` | MySQL 初始化、账号权限、SQLite 数据迁移和连接层用法说明。 |
| `requirements-mysql.txt` | MySQL 连接驱动和注册管理员档案加密依赖。 |
| `README copy.md` | 课程目录 API 与 JSON 数据格式的详细说明（旧版独立说明文档）；本 README 是项目总入口。 |
| `.vscode/settings.json` | VS Code 本机终端设置，包含本机 Python 路径。其他电脑需要按本机安装路径调整或删除此路径设置。 |
| `2026年软件工程课程设计题目.pdf` | 课程设计题目与用例说明。 |
| `课程注册系统需求规格说明_中文译稿.pdf` | 课程注册系统需求规格说明的中文译稿。 |
| `2026年软件工程课程设计要求.ppt` | 课程设计过程和提交要求。 |
| `__pycache__/` | Python 自动生成的字节码缓存，不是源代码，不需要手工维护。 |

## 环境要求

- Windows 开发时建议在项目目录打开 PowerShell。
- Python 3.10 或更新版本。本文命令使用 Windows Python 启动器 `py -3`，即使终端里的 `python` 指向 MSYS2 也可以运行。
- 只运行课程目录 API 和网页时不需要安装第三方 Python 包。
- 使用 MySQL 业务数据库时需要 MySQL Server 8.0.16 或更新版本；安装 `requirements-mysql.txt` 中的 MySQL 驱动和档案加密依赖。

注意区分终端：`PS C:\...>` 是 PowerShell；`C:\...>` 通常是 CMD；`mysql>` 是 MySQL SQL 命令行。下面的 `$env:...` 命令只在 PowerShell 运行，SQL 语句只在 MySQL Workbench 的 SQL 编辑器或 `mysql>` 中运行。

## 本地课程目录演示启动顺序

### 1.（可选）创建 SQLite 业务数据库

只有需要本地 SQLite 业务数据，或要把演示数据迁移到 MySQL 时才运行。项目根目录 PowerShell：

```powershell
py -3 .\init_database.py
```

默认创建或补齐 `data/registration.db`，不会清空已有数据。只建空结构可运行 `py -3 .\init_database.py --empty`。`--reset` 会删除并重建指定 SQLite 文件，谨慎使用。

### 2. 启动遗留课程目录 API

在第一个 PowerShell 终端中，从项目根目录运行：

```powershell
py -3 .\app.py
```

默认使用 JSON 虚拟目录数据，状态地址为 <http://127.0.0.1:8000/health>，完整目录接口为 <http://127.0.0.1:8000/api/v1/catalog>。直接访问 `/` 返回 404 是正常的，因为服务没有定义首页。

### 3. 启动目录浏览网页

保持第一个终端运行，在第二个 PowerShell 终端运行：

```powershell
py -3 .\dashboard.py
```

然后打开 <http://127.0.0.1:8001>。停止任一服务时，在对应终端按 `Ctrl+C`。`dashboard.py` 依赖端口 8000 上的 `app.py`，所以启动顺序不能反过来。

## MySQL 业务数据库准备与启动顺序

MySQL 是新系统业务数据库的多电脑共享方案。执行顺序是：启动 MySQL Server → 建库建表 → 创建应用账号 → 安装 Python 驱动 →（首次部署时）迁移演示数据 → 由新系统后端导入 `database.mysql_connection` 连接数据库。

### 1. 启动 MySQL Server 并创建表结构

确认 Windows 服务中的 MySQL Server 正在运行。在 MySQL Workbench 连接到目标服务器，打开并执行 `database/schema.mysql.sql`。脚本会创建 `course_registration`，不要把业务表建到名为 `mysql` 的系统数据库下。

### 2. 创建低权限应用账号

在 Workbench 的 SQL 编辑器中执行以下 SQL。将示例密码替换为自己设置的密码：

```sql
CREATE USER IF NOT EXISTS 'course_app'@'localhost' IDENTIFIED BY '替换为自己的强密码';
ALTER USER 'course_app'@'localhost' IDENTIFIED BY '替换为自己的强密码';
GRANT SELECT, INSERT, UPDATE, DELETE ON course_registration.* TO 'course_app'@'localhost';
```

如果 MySQL 数据库部署在另一台电脑，账号的主机范围和 `MYSQL_HOST` 要按后端服务器的网络位置配置；不要把 3306 端口暴露到公网。密码不要提交到代码仓库。

### 3. 安装驱动并迁移演示数据（首次部署一次）

回到项目根目录的 PowerShell 终端运行：

```powershell
py -3 -m pip install -r .\requirements-mysql.txt
$env:MYSQL_HOST = '127.0.0.1'
$env:MYSQL_PORT = '3306'
$env:MYSQL_USER = 'course_app'
$env:MYSQL_DATABASE = 'course_registration'
$secret = Read-Host 'MySQL 密码' -AsSecureString
$env:MYSQL_PASSWORD = [System.Net.NetworkCredential]::new('', $secret).Password
py -3 .\database\migrate_sqlite_to_mysql.py
```

迁移脚本要求 MySQL 目标表为空；如果目标表已有数据，它会停止而不会覆盖数据。迁移成功后，可在 Workbench 选择 `course_registration` 并查看 `app_users`、`students` 等表，也可查询 `v_student_report_cards` 和 `v_course_rosters` 两个视图。

### 4. 在新系统后端使用 MySQL

后端进程必须在其环境中设置 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD` 和 `MYSQL_DATABASE`，再使用连接层：

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
```

每次数据库操作使用独立的池化连接；写入通过 `transaction()` 处理。客户端电脑应访问新系统后端 API，不应直接使用数据库管理员账号连接 MySQL。MySQL 详细步骤见 [database/README.mysql.md](database/README.mysql.md)。

### 5. 启动学生注册网页

先确认 MySQL 服务和演示数据已准备好，并保持课程目录 API（第 2 步的 `app.py`）运行。在项目根目录的另一个 PowerShell 终端运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\student\start.ps1
```

输入 MySQL 应用账号密码后，打开 <http://127.0.0.1:8020>。演示账号为 `student.lin` / `StudentDemo1!` 和 `student.chen` / `StudentDemo2!`。学生网页详细说明见 [student/README.md](student/README.md)。

### 6. 启动教师工作台

保持 MySQL 和课程目录 API 运行，在另一个 PowerShell 终端运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\teacher\start.ps1
```

输入 MySQL 应用账号密码后，打开 <http://127.0.0.1:8030>。演示账号包括 `prof.chen` / `ProfessorDemo1!`、`prof.ortega` / `ProfessorDemo2!` 和 `prof.wu` / `ProfessorDemo5!`。教师网页详细说明见 [teacher/README.md](teacher/README.md)。

### 7. 启动注册管理员工作台

保持 MySQL 和课程目录 API 运行，在另一个 PowerShell 终端运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\administer\start.ps1
```

输入 MySQL 应用账号密码后，打开 <http://127.0.0.1:8040>。教务员演示账号为 `registrar.demo` / `RegistrarDemo1!`。新增人员会创建对应的登录用户；停用会保留历史关联。社会安全号码是可选字段，录入前需要按 [administer/README.md](administer/README.md) 配置 AES-GCM 密钥。注册关闭时产生的账单会进入 `billing_transactions` 队列；如配置 `BILLING_API_URL`，模块会发送并重试失败账单。注册管理员详细说明见 [administer/README.md](administer/README.md)。

## 课程目录 API

目录服务默认读取 JSON 虚拟数据，也可按 [README copy.md](README%20copy.md) 中的说明配置 Ingres ODBC 数据源。常用只读接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 服务健康状态 |
| GET | `/api/v1/catalog` | 一次读取完整课程目录 |
| GET | `/api/v1/departments` | 院系列表 |
| GET | `/api/v1/faculty` | 教师目录 |
| GET | `/api/v1/semesters` | 学期列表 |
| GET | `/api/v1/courses?department=CS` | 课程列表 |
| GET | `/api/v1/prerequisites` | 课程先修关系 |
| GET | `/api/v1/course-offerings?semester=2027-SPRING` | 指定学期开课记录 |
| GET | `/api/v1/course-offerings/{offeringId}` | 单个开课记录 |

课程目录 API 是只读的。课程、院系、学期、目录教师、先修关系和开课数据由遗留目录提供；注册业务 MySQL 只保存新系统需要维护的用户档案、选择、实际注册、成绩、账单与审计记录。更多 SQLite 表说明见 [database/README.md](database/README.md)。

## 常见情况

- 浏览器访问 `http://127.0.0.1:8000/` 得到 `404 NOT_FOUND`：正常，使用 `/health` 或 `/api/v1/...` 路径。
- `app.py` 已启动但 `dashboard.py` 显示目录接口错误：确认 `app.py` 仍在运行，且地址与 `CATALOG_API_URL` 一致。
- `mysql>` 下误输入 `py -3` 或 `$env:...`：这些不是 SQL。输入 `\c` 清除未完成语句，再输入 `exit` 回到终端。
- CMD 下输入 `$env:...`：这是 PowerShell 语法。可在 VS Code 选择 PowerShell 终端，或在 CMD 运行 `powershell -NoProfile` 后再执行。
- MySQL 数据库树只显示 `mysql`：在数据库浏览器刷新连接，确认展开的是 `course_registration`，而不是系统库 `mysql`。


