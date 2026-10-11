# 注册管理员板块

独立的教务员可视化工作台，复用项目现有的 MySQL `course_registration` 数据库和只读课程目录 API。默认监听 `127.0.0.1:8040`。

## 启动

先启动 MySQL Server，确认数据库表结构和演示数据已准备好，并安装项目依赖：

```powershell
py -3 -m pip install -r .\requirements-mysql.txt
```

在第一个 PowerShell 窗口启动课程目录 API：

```powershell
py -3 .\app.py
```

保持该窗口运行，在第二个 PowerShell 窗口启动注册管理员网页：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\administer\start.ps1
```

脚本会安全提示输入 MySQL 应用账号密码。打开 <http://127.0.0.1:8040>。

## 演示账号

数据库导入演示数据后，可使用教务员账号登录：

| 角色 | 用户名 | 密码 |
| --- | --- | --- |
| 注册管理员 | `registrar.demo` | `RegistrarDemo1!` |

## 功能

- 只允许 `REGISTRAR` 角色登录和访问管理 API；所有写请求校验会话与 CSRF 令牌。
- 查看学生、教师档案，新增档案、修改身份信息与登录账户、停用账户。
- 新增档案时自动生成唯一编号，并同时创建对应的登录用户；初始密码在创建时设置。
- 社会安全号码可选录入。配置密钥后服务端以 AES-GCM 加密写入现有 `ssn_encrypted` 字段，列表和接口不会回传明文；编辑时留空保留旧值，可选勾选清除。
- 停用不物理删除数据库记录：账号不能登录，教师授课选择会撤回，课程、成绩、账单与审计关联保留。
- 关闭开放中的注册期时检查授课教师、人数和容量，按顺序尝试备选课，并核对先修课程与时间冲突；不满足最低人数（3 人）或未分配教师的开课会取消，仅将各课表中对应的注册记录标为取消，保留其他有效课程，并按保留课程生成账单。整张课表不会因此取消；没有有效课程的课表不生成账单。
- 补位前先移除无有效授课教师、已关闭或取消、或已从当前学期目录移除的开课，再按备选偏好顺序选择符合条件的课程；已注册且只是变为满员的有效课程仍保留。成功注册的主选和补位课程保持 `ENROLLED` 状态。
- 注册关闭成功后，按每学分 150 元的演示费率生成 `PENDING` 账单队列记录，支持幂等，关闭操作写入 `audit_logs`。

## 社会安全号码加密密钥（可选）

不录入这类号码时无需设置密钥。若需要录入，在启动服务的同一个 PowerShell 窗口生成一个随机密钥并保存在当前终端环境中：

```powershell
$env:SSN_ENCRYPTION_KEY = py -3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

不要把密钥提交到仓库或发给协作人员。已有密文只能用生成该密文时相同的密钥解密；密钥遗失后不能恢复原号码。当前页面不提供解密或查看明文的入口。

## 账单队列

项目尚未提供外部账单系统地址。默认只写入数据库中的 `billing_transactions` 队列并保持 `PENDING`，不会伪标为已发送。若课程项目的账单接收端提供 HTTP JSON 接口，可在启动前配置：

```powershell
$env:BILLING_API_URL = 'http://账单服务地址/接收路径'
```

服务会每 30 秒扫描 PENDING/RETRY 记录，以 POST JSON 和 `Idempotency-Key` 请求头发送；2xx 响应标记为 `SENT`，其他响应会保留错误并在后续轮询重试。请先确认接收端支持该请求格式和幂等键。演示费率可用 `BILLING_AMOUNT_CENTS_PER_CREDIT` 覆盖，单位为“分/学分”，默认 15000（即每学分 ¥150）。

此模块面向课程设计演示。默认绑定本机，不含 HTTPS、集中式会话存储或生产部署防护。
