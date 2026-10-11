# 注册关闭账单 HTTP 契约 v1

注册管理员在关闭注册的同一数据库事务中，冻结最终有效注册课程、金额与课程快照，写入 `billing_transactions`。提交成功后，发送器通过 `POST BILLING_API_URL` 交给独立接收端。接收端据此登记账单及最终课表，无需再次查询课程目录。没有有效课程的课表不产生账单。

本仓库提供 `billing.receiver` 作为独立 HTTP 参考接收端。它持久化接收账本并实现幂等，用于课程设计联调；登记账单不等于真实扣款。本地联调已使用真实 HTTP 请求验证。真实外部计费服务的地址、认证方式与契约适配尚未提供，不能据此宣称已经完成该服务的集成。

## 请求

- 路径：参考接收端为 `POST /api/bills`；管理员发送到配置的完整 URL。
- `Content-Type: application/json; charset=utf-8`。
- `Idempotency-Key`：必须与请求体的 `idempotencyKey` 完全一致。
- 最大请求及确认响应长度：65536 字节。
- 账单金额只支持 `CNY`，整数单位为人民币分，不使用浮点金额。默认费率是 15000 分/学分，可通过 `BILLING_AMOUNT_CENTS_PER_CREDIT` 在生成新账单前配置。

| 字段 | 类型与含义 |
| --- | --- |
| `schemaVersion` | 整数，固定为 `1` |
| `eventType` | 固定为 `REGISTRATION_FINALIZED` |
| `transactionId` | 队列中的唯一交易号；所有重试保持不变 |
| `idempotencyKey` | `REGCLOSE-{semesterId}-{studentId}`；所有重试保持不变 |
| `studentId`, `studentName` | 学生编号和关闭注册时的姓名快照 |
| `semesterId`, `semesterName` | 学期编号和名称；目录缺少名称时使用学期编号 |
| `scheduleId`, `scheduleStatus` | 课表编号（正整数），状态固定为 `FINALIZED` |
| `currency` | 固定为 `CNY` |
| `amountCents` | 总金额，非负整数，必须与数据库 `amount_cents` 完全一致 |
| `totalCredits` | 最终课程总学分，正整数 |
| `unitAmountCentsPerCredit` | 入队时冻结的费率，非负整数，单位为分/学分 |
| `enrollments` | 1–4 门最终有效课程的快照，不包含取消的课程及未采用的备选 |

每个 `enrollments` 项包含以下字段：

| 字段 | 类型与含义 |
| --- | --- |
| `offeringId`, `courseId`, `courseTitle` | 开课编号、课程编号、课程名称，非空字符串 |
| `credits`, `amountCents` | 正整数课程学分与非负整数课程金额 |
| `professorId`, `professorName` | 本地有效授课安排中的实际教师编号、姓名，不使用遗留目录教师 |
| `section`, `departmentId`, `departmentName` | 班次和院系快照 |
| `days`, `startTime`, `endTime`, `room` | 上课日期编码数组、开始时间、结束时间、教室 |

班次、院系、起止时间或教室在目录中缺失时，字段仍保留，值为 `null`；`days` 缺失时为 `[]`。接收端可明确显示“未提供”，不能将缺少的排课信息解释为已安排。其余字符串字段必须非空。同一课表不能包含重复的开课编号或课程编号。

金额满足以下规则：每门课程金额等于 `credits × unitAmountCentsPerCredit`，所有课程金额相加等于 `amountCents`，所有学分相加等于 `totalCredits`。发送器同时核对交易号、学生、学期与数据库中的队列记录。

示例（标识值用于说明，真实交易号由系统生成）：

```json
{
  "schemaVersion": 1,
  "eventType": "REGISTRATION_FINALIZED",
  "transactionId": "BILL-EXAMPLE-001",
  "idempotencyKey": "REGCLOSE-2027-SPRING-S2026001",
  "studentId": "S2026001",
  "studentName": "林晓雨",
  "semesterId": "2027-SPRING",
  "semesterName": "2027年春季学期",
  "scheduleId": 1,
  "scheduleStatus": "FINALIZED",
  "currency": "CNY",
  "amountCents": 45000,
  "totalCredits": 3,
  "unitAmountCentsPerCredit": 15000,
  "enrollments": [
    {
      "offeringId": "2027S-CS201-01",
      "courseId": "CS201",
      "courseTitle": "数据结构",
      "credits": 3,
      "amountCents": 45000,
      "section": "01",
      "departmentId": "CS",
      "departmentName": "计算机科学系",
      "professorId": "P1002",
      "professorName": "路易斯·奥尔特加教授",
      "days": ["TUE", "THU"],
      "startTime": "10:30",
      "endTime": "11:45",
      "room": "科学楼105"
    }
  ]
}
```

## 接收确认与幂等

接收端必须先可靠保存账单及幂等结果，再返回确认。首次接受返回 HTTP `201`；重复接受相同内容返回 HTTP `200`，确认 JSON 及 `receiptId` 保持不变：

```json
{
  "schemaVersion": 1,
  "status": "ACCEPTED",
  "receiptId": "RCPT-EXAMPLE-001",
  "transactionId": "BILL-EXAMPLE-001",
  "idempotencyKey": "REGCLOSE-2027-SPRING-S2026001"
}
```

发送器只有收到 HTTP `200` 或 `201`、合法的确认 JSON、非空 `receiptId`、匹配的交易号和幂等键，才把队列标为 `SENT`。`SENT` 表示接收端已接受账单，不能解释为学生已付款。空响应、错误确认或 HTTP `202` 不构成接受确认，会继续重试。

参考接收端在自己的 SQLite 账本中对幂等键和交易号设置唯一约束；相同幂等键与相同 JSON 内容（忽略对象键顺序）返回既有结果，不重复登记。相同幂等键的不同内容、同一交易号使用其他幂等键返回 HTTP `409`。此保证跨接收端重启有效，也覆盖并发重复请求。

队列采用至少一次发送：接收端已保存、确认丢失或注册端状态更新失败时，会重复发送同一交易。真实接收端也必须实现持久化幂等，才能避免重复收费。

## 失败与重试

- 未配置 `BILLING_API_URL`：保持 `PENDING`，不发送，不计尝试次数。
- 网络中断、超时、HTTP `408/425/429` 或 `5xx`：标为 `RETRY`，每 30 秒再次扫描。
- 非上述可重试状态的 HTTP 错误（例如 `400/401/403/404/409/422`）：标为 `FAILED`，保留错误，修正后人工恢复为 `RETRY`。
- 本地快照不符合 v1，或金额/交易号/学生/学期与队列记录不一致：标为 `FAILED`，不会发出 HTTP 请求。
- `attempt_count` 只累计实际发起 HTTP 请求的次数；`last_error` 保存最近错误，成功确认后清空。
- 发送时只读入队快照，绝不根据当前目录、档案或费率重新计算金额。其他待发送账单不会因为某一条契约错误而被跳过。

旧版 `PENDING/RETRY` 记录没有完整快照时不能直接发送到 v1 接收端。发送器会标为 `FAILED` 并给出迁移提示，避免以当前费率补算历史收费。需核对原最终课表及金额，补齐 v1 `payload_json`，保证与原交易号、幂等键、学生、学期和 `amount_cents` 一致，再将状态恢复为 `RETRY`。不要更换已尝试发送的幂等键；无法恢复原快照的记录需人工处理。旧的 `SENT` 记录不会被重新发送。

## 本地启动与验证

在项目根目录，以 Python 3.10+ 启动独立参考接收端（只需标准库）：

```bash
python3 -m billing.receiver --host 127.0.0.1 --port 8050 --db /tmp/course-billing-receiver.sqlite3
```

在启动注册管理员的终端中配置，再按原方式设置 MySQL 并启动管理员服务：

```bash
export BILLING_API_URL=http://127.0.0.1:8050/api/bills
python3 administer/server.py
```

Windows PowerShell 对应配置：

```powershell
$env:BILLING_API_URL = 'http://127.0.0.1:8050/api/bills'
powershell -NoProfile -ExecutionPolicy Bypass -File .\administer\start.ps1
```

关闭注册后，检查管理员账单队列由 `PENDING` 变为 `SENT`，并在接收账本查询完整请求。只有这两端实际互通，才是该配置下的联调成功。接收端账本不存放在注册业务 MySQL 中。

自动联调无需 MySQL、无真实扣款，注册侧使用测试内存数据库，接收侧使用临时持久化账本与本地 HTTP：

```bash
python3 -B -m unittest tests.test_billing_integration -v
```

覆盖成功发送、快照金额与课表、5xx 重试、确认丢失、接收端重启、注册侧状态更新失败、并发幂等、相同键不同内容、错误金额与请求头、无效确认、永久拒绝、旧版快照及未配置接口等情况。
