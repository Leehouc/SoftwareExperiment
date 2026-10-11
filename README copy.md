# 遗留课程目录系统

这是一个只读 HTTP/JSON 适配服务，供课程注册系统读取规范化的课程目录数据。支持两种数据源：

- **JSON 虚拟数据**：默认读取 `data/catalog.json`，适合本地演示，不需要数据库。
- **Ingres 数据库**：通过学院 Unix 服务器上的 Ingres ODBC 接口，后台定时刷新目录快照。

两种模式提供相同的 API。服务仅开放 `GET`；写入请求返回 `405 READ_ONLY`。

## 启动 JSON 虚拟数据

需要 Python 3.10 或更新版本。在项目根目录运行：

```powershell
$env:CATALOG_SOURCE = "json"
python app.py
```

`CATALOG_SOURCE` 默认为 `json`，也可以直接运行 `python app.py`。默认地址为 `http://127.0.0.1:8000`，按 `Ctrl+C` 停止。

## 启动 Ingres 数据源

Ingres 模式应运行在可以访问学院数据库接口的 Unix 服务器上。服务器需要配置 Ingres ODBC 驱动、ODBC DSN 和 `pyodbc`：

```sh
python3 -m pip install pyodbc
export CATALOG_SOURCE=ingres
export CATALOG_INGRES_CONNECTION_STRING='DSN=IngresCatalog;UID=只读账号;PWD=密码'
python3 app.py
```

请通过服务器环境配置或密钥管理服务保存连接串，不要将密码写入代码或提交到版本库。数据库账号只需读取权限。

### Ingres 只读视图约定

`IngresCatalogSource` 通过下表中的视图读取规范化数据。学院可以建立这些视图映射现有 Ingres 表；如果实际字段名不同，修改 `IngresCatalogSource.load_snapshot()` 中的 SQL 和字段映射即可。

| 视图 | 字段 | 关系 |
| --- | --- | --- |
| `catalog_departments` | `department_id`, `name` | 院系 |
| `catalog_faculty` | `faculty_id`, `name` | 教师 |
| `catalog_semesters` | `semester_id`, `name`, `start_date`, `end_date` | 学期 |
| `catalog_courses` | `course_id`, `title`, `department_id`, `credits`, `description` | 课程；`department_id` 引用院系 |
| `catalog_prerequisites` | `course_id`, `prerequisite_course_id` | 先修关系；两列都引用课程 |
| `catalog_course_offerings` | `offering_id`, `course_id`, `semester_id`, `faculty_id`, `section`, `days`, `start_time`, `end_time`, `room`, `capacity`, `enrolled_count`, `status` | 开课；分别引用课程、学期、教师 |

`days` 在数据库视图中用逗号分隔，例如 `MON,WED`；API 输出为 JSON 数组。开课的三个外键值必须能在相应实体中找到。

### 延迟与快照

Ingres 模式使用后台线程定时读取上述视图，并在内存中构造完整快照。HTTP 请求只读取快照，不等待 Ingres 查询；刷新成功后整体替换快照，刷新失败时继续提供最近一次成功的数据。首次快照准备完成前，目录 API 与 `/health` 会返回 `503`。

单次刷新超时默认 9 秒，连接超时默认 3 秒，单条 SQL 超时默认 8 秒，刷新间隔默认 60 秒。超时仍取决于 ODBC 驱动是否支持取消查询，部署时应结合实际驱动和目录规模确认刷新耗时。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CATALOG_SOURCE` | `json` | `json` 或 `ingres` |
| `CATALOG_DATA_FILE` | `data/catalog.json` | JSON 文件路径 |
| `CATALOG_CACHE_TTL_SECONDS` | `30` | JSON 内存缓存秒数，设为 `0` 可关闭 |
| `CATALOG_INGRES_CONNECTION_STRING` | 无 | Ingres ODBC 连接串，Ingres 模式必填 |
| `CATALOG_INGRES_CONNECT_TIMEOUT_SECONDS` | `3` | ODBC 连接超时，允许范围 1–9 秒 |
| `CATALOG_INGRES_QUERY_TIMEOUT_SECONDS` | `8` | 单条查询超时上限，允许范围 1–9 秒 |
| `CATALOG_INGRES_REFRESH_TIMEOUT_SECONDS` | `9` | 一次快照刷新总上限，允许范围 1–9 秒 |
| `CATALOG_REFRESH_INTERVAL_SECONDS` | `60` | 快照刷新间隔 |
| `CATALOG_HOST` | `127.0.0.1` | HTTP 监听地址 |
| `CATALOG_PORT` | `8000` | HTTP 监听端口 |

## API 使用方法

启动服务后，可以在浏览器打开地址，或在 PowerShell 运行：

```powershell
# 查看服务状态
Invoke-RestMethod 'http://127.0.0.1:8000/health' | ConvertTo-Json -Depth 10

# 一次读取完整目录（所有实体数组）
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/catalog' | ConvertTo-Json -Depth 10

# 读取院系、教师、学期、课程、先修关系
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/departments' | ConvertTo-Json -Depth 10
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/faculty' | ConvertTo-Json -Depth 10
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/semesters' | ConvertTo-Json -Depth 10
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/courses?department=CS' | ConvertTo-Json -Depth 10
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/prerequisites' | ConvertTo-Json -Depth 10

# 按学期和院系读取开课记录；/api/v1/offerings 仍然兼容
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/course-offerings?semester=2026-FALL&department=Mathematics' | ConvertTo-Json -Depth 10
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/course-offerings/2026F-CS101-01' | ConvertTo-Json -Depth 10
```

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 服务状态；Ingres 模式包含最近快照状态 |
| GET | `/api/v1/catalog` | 完整目录视图，返回所有实体数组 |
| GET | `/api/v1/departments` | 院系列表 |
| GET | `/api/v1/faculty` | 教师列表 |
| GET | `/api/v1/semesters` | 学期列表 |
| GET | `/api/v1/courses?department=CS` | 课程列表，可按院系 ID 或名称筛选 |
| GET | `/api/v1/prerequisites` | 课程先修关系 |
| GET | `/api/v1/course-offerings?semester=2026-FALL&department=CS` | 开课列表，可按学期和院系筛选 |
| GET | `/api/v1/course-offerings/{offeringId}` | 查询单条开课 |
| GET | `/api/v1/offerings` | 开课列表的旧路径兼容别名 |

所有成功响应都使用 `{ "data": ... }` 包装。`/api/v1/catalog` 的 `data` 是包含实体数组的对象。

## JSON 数据格式

`data/catalog.json` 是虚拟数据源。根对象包含独立实体数组；通过 ID 形成实体关系。修改该文件后，最多等待 `CATALOG_CACHE_TTL_SECONDS` 秒即可读取新内容。

```json
{
  "source": "fictional-demo-data",
  "readOnly": true,
  "departments": [
    {"departmentId": "CS", "name": "计算机科学系"}
  ],
  "faculty": [
    {"facultyId": "P1001", "name": "陈梅雅教授"}
  ],
  "semesters": [
    {"semesterId": "2026-FALL", "name": "2026年秋季学期", "startDate": "2026-09-01", "endDate": "2026-12-18"}
  ],
  "courses": [
    {"courseId": "CS101", "title": "程序设计导论", "departmentId": "CS", "credits": 3, "description": "介绍程序设计基础、问题求解方法与软件开发流程。"}
  ],
  "prerequisites": [
    {"courseId": "CS201", "prerequisiteCourseId": "CS101"}
  ],
  "courseOfferings": [
    {"offeringId": "2026F-CS101-01", "courseId": "CS101", "semesterId": "2026-FALL", "facultyId": "P1001", "section": "01", "days": ["MON", "WED"], "startTime": "09:00", "endTime": "10:15", "room": "科学楼201", "capacity": 10, "enrolledCount": 7, "status": "开放"}
  ]
}
```

### 实体与外键

| 实体 | 关键字段 | 关联 |
| --- | --- | --- |
| `departments`（院系） | `departmentId`, `name` | 一个院系可对应多门课程 |
| `faculty`（教师） | `facultyId`, `name` | 一个教师可教授多个开课班次 |
| `courses`（课程） | `courseId`, `title`, `departmentId`, `credits`, `description` | `departmentId` 引用 `departments.departmentId` |
| `prerequisites`（先修关系） | `courseId`, `prerequisiteCourseId` | 两个字段都引用 `courses.courseId`，表示课程之间的多对多关系 |
| `semesters`（学期） | `semesterId`, `name`, `startDate`, `endDate` | 一个学期可有多个开课班次 |
| `courseOfferings`（课程开课） | `offeringId`, `courseId`, `semesterId`, `facultyId`, `section`, `days`, `startTime`, `endTime`, `room`, `capacity`, `enrolledCount`, `status` | 分别引用课程、学期、教师 |

`days` 使用 `MON`、`TUE`、`WED`、`THU`、`FRI`、`SAT`、`SUN`。虚拟数据中的 `status` 使用“开放”“已满”；Ingres 数据源会沿用数据库视图返回的状态值。课程、学期、教师等主体信息只在各自数组中维护；开课记录仅保存对应 ID 和该班次的排课、容量信息。

JSON 和 Ingres 返回相同的规范化结构。若调用端需要可读名称，可根据 `courseId`、`semesterId`、`facultyId` 等 ID 与相应实体数组关联；`/api/v1/catalog` 一次提供完整目录数据。

## 常见错误

- 不存在的路径：`404 NOT_FOUND`
- 不存在的开课 ID：`404 OFFERING_NOT_FOUND`
- 数据源不可用或 Ingres 快照尚未准备好：`503 CATALOG_UNAVAILABLE`
- 对只读服务发送 `POST`、`PUT`、`PATCH` 或 `DELETE`：`405 READ_ONLY`
