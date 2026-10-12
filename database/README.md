# 课程注册新系统数据库

本目录说明新课程注册系统的本地业务数据库。课程目录、课程、学期、院系、教师目录、课程先修关系和课程开设仍由只读 `app.py` 服务提供；SQLite 不复制这些目录表，只在选课、授课选择、成绩等业务记录中保存外部 ID。

## 创建数据库

本项目使用 Python 自带的 SQLite，无需安装或启动独立数据库服务。在 `SoftwareExperiment` 项目根目录运行：

```powershell
python init_database.py
```

默认创建并填充 `data/registration.db`。重复运行会保留已有记录并补齐缺失的表/演示记录，不会清空文件。

```powershell
# 只创建表结构，不放演示记录
python init_database.py --empty

# 明确要求重建时，删除指定 SQLite 文件后重新创建
python init_database.py --reset
```

`--reset` 会删除目标数据库文件。可用 `--database path/to/another.db` 指定文件位置。

## 演示账号

所有密码只用于本地课设演示，数据库里保存 PBKDF2-SHA256 哈希，不保存明文。初始化脚本中的账号/密码为虚构数据：

| 角色 | 用户名 | 密码 |
| --- | --- | --- |
| 学生 | `student.lin` | `StudentDemo1!` |
| 学生 | `student.chen` | `StudentDemo2!` |
| 学生 | `student.wang` | `StudentDemo3!` |
| 学生 | `student.zhao` | `StudentDemo4!` |
| 学生 | `student.zhou` | `StudentDemo5!` |
| 学生 | `student.wu` | `StudentDemo6!` |
| 学生 | `student.liu` | `StudentDemo7!` |
| 教师 P1001-P1006 | `prof.chen`、`prof.ortega`、`prof.shah`、`prof.brooks`、`prof.wu`、`prof.johnson` | 对应编号的 `ProfessorDemo1!` 至 `ProfessorDemo6!` |
| 教务员 | `registrar.demo` | `RegistrarDemo1!` |

不要把这些演示账号用于真实用户或生产部署。

## 数据表

| 表 | 用途 | 关键字段/约束 |
| --- | --- | --- |
| `app_users` | 登录账户与角色 | 用户名唯一；角色为 `STUDENT`、`PROFESSOR`、`REGISTRAR`；密码使用 PBKDF2-SHA256 哈希 |
| `students` | 学生档案 | 学号、姓名、出生日期、在读状态、毕业日期；`ssn_encrypted` 留作应用层加密后的密文，演示数据为空 |
| `professors` | 教师档案 | 教师 ID、姓名、状态、出生日期和目录院系 ID |
| `registrars` | 教务员档案 | 与登录账户关联 |
| `registration_periods` | 注册期与加退选时间 | 学期 ID、开放状态、开始/结束时间 |
| `student_schedules` | 每名学生每学期一份课表 | 学期 ID、状态；学生和学期组合唯一 |
| `schedule_choices` | 4 个主选与 2 个备选 | 主选顺序 1-4，备选顺序 1-2；每个开课 ID 在一份课表中最多出现一次 |
| `professor_course_eligibility` | 教师可教授课程 | 教师 ID 与课程目录 course ID 的关联 |
| `professor_offering_selections` | 当前授课归属 | 每个开课保留一条唯一记录；`SELECTED`、`CONFIRMED` 占用班次，`WITHDRAWN` 可被其他合格教师接手；变更历史保存在 `audit_logs.details_json` 的 `assignmentChanges` 中 |
| `enrollments` | 学生实际注册记录 | 外部开课/课程 ID、注册状态，以及选课时的课程名称和学分快照 |
| `course_grades` | 教师成绩 | 成绩限定 A/B/C/D/F/I；数据库触发器检查该教师是否被分配到对应开课 |
| `billing_transactions` | 发往账单系统的事务队列 | 金额用整数分保存；幂等键唯一，支持失败重试 |
| `audit_logs` | 敏感操作审计记录 | 操作者、操作、实体和详情 JSON |

`v_student_report_cards` 和 `v_course_rosters` 是便于查询的只读视图。

## 与课程目录服务的边界

`course_id`、`offering_id`、`semester_id`、`department_id` 都是课程目录 API 的外部标识，不在 SQLite 中建立目录副本，也不能在 SQLite 层设置指向目录数据的外键。程序通过课程目录服务读取对应信息：

```text
GET http://127.0.0.1:8000/api/v1/catalog
GET http://127.0.0.1:8000/api/v1/course-offerings?semester=2027-SPRING
```

注册系统本地保存学生选择、教师授课选择和实际注册。`enrollments` 中的课程名称和学分是选课时快照，供历史成绩单和账单使用，并不作为课程目录的维护数据。

目录服务的 `enrolledCount` 是外部目录快照；注册系统自己的注册人数从 `enrollments` 中按开课 ID 查询。业务层在提交选课时应结合目录容量、目录已选人数与本地有效注册数，在事务中检查容量上限；目录记录本身保持只读。

## Python 连接示例

```python
import sqlite3

connection = sqlite3.connect("data/registration.db")
connection.row_factory = sqlite3.Row
connection.execute("PRAGMA foreign_keys = ON")

rows = connection.execute(
    "SELECT offering_id, COUNT(*) AS student_count "
    "FROM enrollments "
    "WHERE enrollment_status = 'ENROLLED' "
    "GROUP BY offering_id"
).fetchall()
```

SQLite 外键需要在每个连接上启用。用户名、成绩和学生档案的访问权限仍需由新系统服务层检查：学生只能读取/修改自己的课表，教师只能修改自己授课班次的成绩，只有教务员能维护学生档案。`ssn_encrypted` 不应存放明文；演示数据没有真实 SSN。

## 演示记录

数据库包含七名虚构学生（其中两名带有课表与历史成绩）、六名教师、一名教务员、教师授课选择和一笔账单记录。数据库只保存课程目录的外部课程/开课编号，不复制课程目录数据；`data/catalog.json` 现在包含与历史授课记录匹配的 2026 年春季学期及三个开课班次，便于读取历史名册和查看课程情况。历史成绩仍保留课程名称快照，避免目录内容变化影响成绩单。

## MySQL 多电脑部署

本地 SQLite 方案的 MySQL 迁移步骤、建表脚本和数据迁移方法见 [README.mysql.md](README.mysql.md)。

