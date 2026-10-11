# 教师课程注册板块

这是独立的教师工作台，连接已有 MySQL `course_registration` 数据库，并读取 `app.py` 提供的只读课程目录。默认监听本机 `127.0.0.1:8030`。

## 启动

确认 MySQL Server 正在运行、数据库已初始化并迁移演示数据，安装 MySQL 驱动：

```powershell
py -3 -m pip install -r .\requirements-mysql.txt
```

在项目根目录的第一个 PowerShell 终端运行课程目录服务：

```powershell
py -3 .\app.py
```

保持该服务运行，在第二个 PowerShell 终端启动教师工作台：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\teacher\start.ps1
```

输入 MySQL 应用账号密码，打开 <http://127.0.0.1:8030>。按 `Ctrl+C` 停止服务。

## 演示账号

| 教师 | 用户名 | 密码 |
| --- | --- | --- |
| 陈梅雅教授 | `prof.chen` | `ProfessorDemo1!` |
| 路易斯·奥尔特加教授 | `prof.ortega` | `ProfessorDemo2!` |
| 吴亨利教授 | `prof.wu` | `ProfessorDemo5!` |

这些账号只用于虚构演示数据。教师需有 `PROFESSOR` 角色、有效教师档案和 `ACTIVE` 状态才能登录。

## 功能与权限

- 显示当前教师具备资格的开课以及自己已有的授课选择。
- 在注册开放期间保存授课选择；服务端核验教师资格、开课归属和时间冲突。由其他教师占用的开课不可修改。
- `SELECTED`、`CONFIRMED` 占用班次，`WITHDRAWN` 释放班次；有资格的其他教师可在注册开放期间接手。接手时原位更新唯一的开课记录，无需变更数据库结构。
- `professor_offering_selections` 保存当前授课归属；撤回、重新选择和接手的历史以 `audit_logs` 中 `UPDATE_TEACHING_SELECTION` 的 `assignmentChanges` 保留，记录开课编号及变更前后教师、学期、状态、登记时间，并与授课变更同事务提交。接手不会重写已有学生注册或成绩记录；原教师失去该班次的名册和评分权限。
- 查看本人授课课程的学生名册，包括正在进行的学期；名册只包含该开课、该学期已注册或已完成的学生。
- 仅在学期结束后为名册中的学生录入或修改 `A`、`B`、`C`、`D`、`F`、`I` 成绩；留空表示跳过，不会清除已有成绩。当前学期名册可查看，成绩控件只读，服务端也会拒绝提前录入。
- 成绩录入后，符合条件的选课记录标记为已完成，成绩进入学生成绩单视图；操作写入 `audit_logs`。
- 服务端从登录会话确定教师编号，并校验每个开课、名册和成绩记录的归属，不接受网页传入的教师身份作为授权依据。

若 MySQL 已迁移过演示数据，教师授课安排和成绩沿用数据库当前状态，不会被启动脚本重置。学生名册仅来自已有的注册记录；空名册不会自动生成学生数据。

## 部署范围

这是课程设计本机演示版。默认只有运行服务的电脑能打开。若在隔离的局域网演示，可在启动前于 PowerShell 设置 `$env:TEACHER_HOST = '0.0.0.0'`，并让浏览器访问服务器的局域网 IP 和端口 `8030`。还需按需要配置 Windows 防火墙。此内置 HTTP 服务不提供 HTTPS 或生产级会话存储。
