# newdatabaseTest1.0 数据库测试网页

这是一个独立运行的 MySQL 只读检查网页，用来确认课程注册系统的 MySQL 数据库能够连接、表里有记录，并预览迁移后的演示数据。网页不会写入或删除数据，只展示白名单中的非敏感字段。

## 启动

先确认 MySQL Server 正在运行，并且已安装项目依赖。在项目根目录打开 PowerShell，执行：

```powershell
py -3 -m pip install -r .\requirements-mysql.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\newdatabaseTest1.0\start.ps1
```

启动脚本会提示输入 `course_app` 的 MySQL 密码。默认连接参数为 `127.0.0.1:3306` 和 `course_registration`。若已在当前 PowerShell 设置 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_DATABASE`，脚本会沿用这些值。

网页启动后打开 <http://127.0.0.1:8090>。按 `Ctrl+C` 停止服务。

## 页面功能

- 检查 MySQL 连接，并显示数据库名、MySQL 版本和当前连接账号。
- 查询 13 张业务表的记录数，查看迁移数据是否存在。
- 选择业务表预览最多 20 条记录。
- 仅绑定到 `127.0.0.1`，供当前电脑本机查看。
- 只读取固定表和固定字段，不提供任意 SQL、增删改操作。

若页面显示连接失败，请检查 MySQL Server 是否运行、`course_app` 是否有 `course_registration` 的 `SELECT` 权限、密码是否正确，以及 Python 驱动是否安装成功。
