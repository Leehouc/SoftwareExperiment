# newdatabaseTest1.0 数据库测试网页

这是一个独立运行的 MySQL 只读检查网页，用来确认课程注册系统的 MySQL 数据库能够连接、表里有记录，并预览迁移后的演示数据。网页不会写入或删除业务数据，只展示白名单字段；课表、成绩和学生信息仅对已登录的有效教务员开放。

## 启动

先确认 MySQL Server 正在运行，并且已安装项目依赖。在项目根目录打开 PowerShell，执行：

```powershell
py -3 -m pip install -r .\requirements-mysql.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\newdatabaseTest1.0\start.ps1
```

启动脚本会提示输入 `course_app` 的 MySQL 密码。默认连接参数为 `127.0.0.1:3306` 和 `course_registration`。若已在当前 PowerShell 设置 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_DATABASE`，脚本会沿用这些值。

网页启动后打开 <http://127.0.0.1:8090>，使用课程注册系统的教务员账号登录（演示库账号见 [教务员门户说明](../administer/README.md)）。这里输入的是应用账号密码，不是启动脚本中的 MySQL 连接密码。检查页使用独立的 1 小时会话，学生、教师或其他门户的 Cookie 不能直接访问检查接口。按 `Ctrl+C` 停止服务。

## 页面功能

- 检查 MySQL 连接，并显示数据库名、MySQL 版本和当前连接账号。
- 查询 13 张业务表的记录数，查看迁移数据是否存在。
- 选择业务表预览最多 20 条记录。
- 仅绑定到 `127.0.0.1`，供当前电脑本机查看。
- 只读取固定表和固定字段，不提供任意 SQL、增删改操作。
- `/api/status` 与 `/api/preview` 均要求有效教务员登录；直接请求接口或绕过网页仍会校验权限。登录会话 Cookie 为 `HttpOnly`、`SameSite=Strict`，退出须校验 CSRF。
- 每次检查数据库或预览前复查账号有效性、角色、教务员档案及密码指纹。停用或密码重置后旧会话在下一次请求时被拒绝；权限查询失败返回 503，不以旧内存会话放行。前端返回登录页并清空已显示的预览。

若页面显示连接失败，请检查 MySQL Server 是否运行、`course_app` 是否有 `course_registration` 的 `SELECT` 权限、密码是否正确，以及 Python 驱动是否安装成功。
