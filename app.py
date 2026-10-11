"""旧课程目录系统的只读 REST 适配服务。

默认使用项目内的 JSON 虚拟数据。设置 CATALOG_SOURCE=ingres 后，服务会从
学院 Unix 服务器的 Ingres ODBC 接口定时刷新完整目录，并从内存快照响应请求。
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import closing
from datetime import date, datetime, time as datetime_time, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


DATA_FILE = Path(os.environ.get("CATALOG_DATA_FILE", Path(__file__).parent / "data" / "catalog.json"))
CACHE_TTL_SECONDS = max(0, int(os.environ.get("CATALOG_CACHE_TTL_SECONDS", "30")))
ENTITY_KEYS = ("departments", "faculty", "semesters", "courses", "prerequisites", "courseOfferings")


class CatalogUnavailableError(Exception):
    """配置的课程目录数据源无法查询时抛出。"""


class CatalogQueryMixin:
    """为不同数据源提供相同的规范化目录查询接口。"""

    def _read_catalog(self) -> dict[str, Any]:
        raise NotImplementedError

    def catalog(self) -> dict[str, Any]:
        data = self._read_catalog()
        return {key: data[key] for key in ENTITY_KEYS}

    def departments(self) -> list[dict[str, Any]]:
        return self._read_catalog()["departments"]

    def faculty(self) -> list[dict[str, Any]]:
        return self._read_catalog()["faculty"]

    def semesters(self) -> list[dict[str, Any]]:
        return self._read_catalog()["semesters"]

    def courses(self, department: str | None = None) -> list[dict[str, Any]]:
        catalog = self._read_catalog()
        courses = catalog["courses"]
        if not department:
            return courses
        key = department.casefold()
        department_names = {
            item["departmentId"].casefold(): item["name"].casefold()
            for item in catalog["departments"]
        }
        return [
            course for course in courses
            if course["departmentId"].casefold() == key
            or department_names.get(course["departmentId"].casefold()) == key
        ]

    def prerequisites(self) -> list[dict[str, Any]]:
        return self._read_catalog()["prerequisites"]

    def offerings(
        self,
        semester: str | None = None,
        department: str | None = None,
    ) -> list[dict[str, Any]]:
        catalog = self._read_catalog()
        offerings = catalog["courseOfferings"]
        if semester:
            key = semester.casefold()
            offerings = [item for item in offerings if item["semesterId"].casefold() == key]
        if department:
            key = department.casefold()
            courses_by_id = {item["courseId"].casefold(): item for item in catalog["courses"]}
            departments = {
                item["departmentId"].casefold(): item["name"].casefold()
                for item in catalog["departments"]
            }
            offerings = [
                item for item in offerings
                if (
                    courses_by_id[item["courseId"].casefold()]["departmentId"].casefold() == key
                    or departments.get(
                        courses_by_id[item["courseId"].casefold()]["departmentId"].casefold()
                    ) == key
                )
            ]
        return offerings

    def offering(self, offering_id: str) -> dict[str, Any] | None:
        key = offering_id.casefold()
        return next(
            (item for item in self.offerings() if item["offeringId"].casefold() == key),
            None,
        )


class JsonCatalogRepository(CatalogQueryMixin):
    """只读 JSON 数据仓库；数据格式与课程目录实体关系一致。"""

    def __init__(self, data_file: Path) -> None:
        self.data_file = data_file
        self._lock = threading.Lock()
        self._cached_data: dict[str, Any] | None = None
        self._cached_at = 0.0

    def _read_catalog(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if self._cached_data is not None and now - self._cached_at < CACHE_TTL_SECONDS:
                return self._cached_data
            with self.data_file.open("r", encoding="utf-8") as source:
                catalog = json.load(source)
            missing = [key for key in ENTITY_KEYS if key not in catalog]
            if missing:
                raise CatalogUnavailableError(f"JSON 目录缺少实体数组：{', '.join(missing)}")
            self._cached_data = catalog
            self._cached_at = now
            return catalog


class IngresCatalogSource:
    """通过 ODBC 批量读取规范化只读视图，构造完整目录快照。

    预期视图：catalog_departments、catalog_faculty、catalog_semesters、
    catalog_courses、catalog_prerequisites、catalog_course_offerings。
    """

    def __init__(
        self,
        connection_string: str,
        connect_timeout: int,
        query_timeout: int,
        refresh_timeout: int,
    ) -> None:
        try:
            import pyodbc
        except ImportError as exc:
            raise RuntimeError(
                "Ingres 数据源需要 pyodbc 和学院 Unix 服务器上配置好的 Ingres ODBC 驱动。"
            ) from exc
        self._pyodbc = pyodbc
        self._connection_string = connection_string
        self._connect_timeout = connect_timeout
        self._query_timeout = query_timeout
        self._refresh_timeout = refresh_timeout

    def _fetchall(self, connection: Any, sql: str, deadline: float) -> list[dict[str, Any]]:
        remaining = int(deadline - time.monotonic())
        if remaining < 1:
            raise CatalogUnavailableError("Ingres 目录刷新超过了总时间上限。")
        # 单条查询超时不能超过本次快照剩余的时间预算。
        connection.timeout = min(self._query_timeout, remaining)
        cursor = connection.cursor()
        try:
            cursor.execute(sql)
            columns = [str(column[0]).casefold() for column in cursor.description or ()]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        return value.rstrip() if isinstance(value, str) else str(value).strip()

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, datetime_time):
            return value.strftime("%H:%M")
        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
        if isinstance(value, str):
            return value.rstrip()
        return value

    @classmethod
    def _time_text(cls, value: Any) -> str:
        if isinstance(value, (datetime_time, datetime)):
            return value.strftime("%H:%M")
        text = cls._text(value)
        if len(text) >= 5 and text[2] == ":":
            return text[:5]
        return text

    @classmethod
    def _days(cls, value: Any) -> list[str]:
        if isinstance(value, (list, tuple)):
            parts = value
        else:
            parts = cls._text(value).split(",")
        return [str(day).strip().upper() for day in parts if str(day).strip()]

    def load_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """在刷新总时限内批量读取实体视图并整理字段名称。"""
        deadline = time.monotonic() + self._refresh_timeout
        try:
            connection = self._pyodbc.connect(
                self._connection_string,
                timeout=min(self._connect_timeout, self._refresh_timeout),
                autocommit=False,
            )
            with closing(connection):
                department_rows = self._fetchall(
                    connection,
                    "SELECT department_id, name FROM catalog_departments",
                    deadline,
                )
                faculty_rows = self._fetchall(
                    connection,
                    "SELECT faculty_id, name FROM catalog_faculty",
                    deadline,
                )
                semester_rows = self._fetchall(
                    connection,
                    "SELECT semester_id, name, start_date, end_date FROM catalog_semesters",
                    deadline,
                )
                course_rows = self._fetchall(
                    connection,
                    "SELECT course_id, title, department_id, credits, description FROM catalog_courses",
                    deadline,
                )
                prerequisite_rows = self._fetchall(
                    connection,
                    "SELECT course_id, prerequisite_course_id FROM catalog_prerequisites",
                    deadline,
                )
                offering_rows = self._fetchall(
                    connection,
                    "SELECT offering_id, course_id, semester_id, faculty_id, section, days, "
                    "start_time, end_time, room, capacity, enrolled_count, status "
                    "FROM catalog_course_offerings",
                    deadline,
                )
                if time.monotonic() > deadline:
                    raise CatalogUnavailableError("Ingres 目录刷新超过了总时间上限。")
                connection.commit()
        except self._pyodbc.Error as exc:
            raise CatalogUnavailableError(f"Ingres ODBC 查询失败：{exc}") from exc

        departments = [
            {"departmentId": self._text(row["department_id"]), "name": self._text(row["name"])}
            for row in department_rows
        ]
        faculty = [
            {"facultyId": self._text(row["faculty_id"]), "name": self._text(row["name"])}
            for row in faculty_rows
        ]
        semesters = [
            {
                "semesterId": self._text(row["semester_id"]),
                "name": self._text(row["name"]),
                "startDate": self._json_value(row["start_date"]),
                "endDate": self._json_value(row["end_date"]),
            }
            for row in semester_rows
        ]
        courses = [
            {
                "courseId": self._text(row["course_id"]),
                "title": self._text(row["title"]),
                "departmentId": self._text(row["department_id"]),
                "credits": self._json_value(row["credits"]),
                "description": self._text(row["description"]),
            }
            for row in course_rows
        ]
        prerequisites = [
            {
                "courseId": self._text(row["course_id"]),
                "prerequisiteCourseId": self._text(row["prerequisite_course_id"]),
            }
            for row in prerequisite_rows
        ]
        course_offerings = [
            {
                "offeringId": self._text(row["offering_id"]),
                "courseId": self._text(row["course_id"]),
                "semesterId": self._text(row["semester_id"]),
                "facultyId": self._text(row["faculty_id"]),
                "section": self._text(row["section"]),
                "days": self._days(row["days"]),
                "startTime": self._time_text(row["start_time"]),
                "endTime": self._time_text(row["end_time"]),
                "room": self._text(row["room"]),
                "capacity": self._json_value(row["capacity"]),
                "enrolledCount": self._json_value(row["enrolled_count"]),
                "status": self._text(row["status"]).upper(),
            }
            for row in offering_rows
        ]
        return {
            "departments": departments,
            "faculty": faculty,
            "semesters": semesters,
            "courses": courses,
            "prerequisites": prerequisites,
            "courseOfferings": course_offerings,
        }


class CachedCatalogRepository(CatalogQueryMixin):
    """后台刷新 Ingres 快照；HTTP 请求只读取内存中的最近成功数据。"""

    def __init__(self, source: IngresCatalogSource, refresh_interval: int) -> None:
        self._source = source
        self._refresh_interval = refresh_interval
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: dict[str, Any] | None = None
        self._last_success_monotonic: float | None = None
        self._last_success_at: str | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._refresh_loop,
            name="ingres-catalog-refresh",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def _refresh_loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                snapshot = self._source.load_snapshot()
                finished = time.monotonic()
                with self._lock:
                    self._snapshot = snapshot
                    self._last_success_monotonic = finished
                    self._last_success_at = datetime.now(timezone.utc).isoformat()
                    self._last_error = None
                print(f"Ingres 目录快照已刷新，用时 {finished - started:.2f} 秒。")
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)
                print(f"Ingres 目录快照刷新失败，继续使用最近一次成功的快照：{exc}")
            self._stop_event.wait(self._refresh_interval)

    def _read_catalog(self) -> dict[str, Any]:
        with self._lock:
            snapshot = self._snapshot
        if snapshot is None:
            raise CatalogUnavailableError("Ingres 目录快照尚未准备好，请稍后重试。")
        return snapshot

    def health(self) -> dict[str, Any]:
        with self._lock:
            ready = self._snapshot is not None
            last_success = self._last_success_at
            last_refresh_age = (
                None
                if self._last_success_monotonic is None
                else round(time.monotonic() - self._last_success_monotonic, 1)
            )
            refresh_failed = self._last_error is not None
        return {
            "ready": ready,
            "lastRefreshSucceededAt": last_success,
            "cacheAgeSeconds": last_refresh_age,
            "refreshFailed": refresh_failed,
        }


CATALOG_SOURCE = os.environ.get("CATALOG_SOURCE", "json").casefold()
if CATALOG_SOURCE == "json":
    repository: JsonCatalogRepository | CachedCatalogRepository = JsonCatalogRepository(DATA_FILE)
elif CATALOG_SOURCE == "ingres":
    connection_string = os.environ.get("CATALOG_INGRES_CONNECTION_STRING")
    if not connection_string:
        raise RuntimeError("使用 Ingres 数据源时必须设置 CATALOG_INGRES_CONNECTION_STRING。")
    connect_timeout = int(os.environ.get("CATALOG_INGRES_CONNECT_TIMEOUT_SECONDS", "3"))
    query_timeout = int(os.environ.get("CATALOG_INGRES_QUERY_TIMEOUT_SECONDS", "8"))
    refresh_timeout = int(os.environ.get("CATALOG_INGRES_REFRESH_TIMEOUT_SECONDS", "9"))
    refresh_interval = max(1, int(os.environ.get("CATALOG_REFRESH_INTERVAL_SECONDS", "60")))
    if not all(1 <= value <= 9 for value in (connect_timeout, query_timeout, refresh_timeout)):
        raise RuntimeError("Ingres 连接、查询和刷新超时必须设置为 1 到 9 秒，以低于 10 秒上限。")
    repository = CachedCatalogRepository(
        IngresCatalogSource(connection_string, connect_timeout, query_timeout, refresh_timeout),
        refresh_interval=refresh_interval,
    )
else:
    raise RuntimeError("CATALOG_SOURCE 必须设置为 'json' 或 'ingres'。")


class CatalogHandler(BaseHTTPRequestHandler):
    server_version = "LegacyCourseCatalog/1.0"

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        max_age = 0 if isinstance(repository, CachedCatalogRepository) else CACHE_TTL_SECONDS
        self.send_header("Cache-Control", f"public, max-age={max_age}")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - 按 BaseHTTPRequestHandler 的接口约定命名
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        try:
            if path == "/health":
                if isinstance(repository, CachedCatalogRepository):
                    health = repository.health()
                    status = 200 if health["ready"] else 503
                    if not health["ready"]:
                        service_status = "starting"
                    elif health["refreshFailed"]:
                        service_status = "degraded"
                    else:
                        service_status = "ok"
                    self._send_json(
                        status,
                        {"status": service_status, "service": "legacy-course-catalog", **health},
                    )
                else:
                    self._send_json(200, {"status": "ok", "service": "legacy-course-catalog"})
            elif path == "/api/v1/catalog":
                self._send_json(200, {"data": repository.catalog()})
            elif path == "/api/v1/departments":
                self._send_json(200, {"data": repository.departments()})
            elif path == "/api/v1/faculty":
                self._send_json(200, {"data": repository.faculty()})
            elif path == "/api/v1/semesters":
                self._send_json(200, {"data": repository.semesters()})
            elif path == "/api/v1/courses":
                self._send_json(200, {"data": repository.courses(_first(query, "department"))})
            elif path == "/api/v1/prerequisites":
                self._send_json(200, {"data": repository.prerequisites()})
            elif path in ("/api/v1/offerings", "/api/v1/course-offerings"):
                self._send_json(
                    200,
                    {"data": repository.offerings(_first(query, "semester"), _first(query, "department"))},
                )
            elif path.startswith("/api/v1/offerings/") or path.startswith("/api/v1/course-offerings/"):
                prefix = "/api/v1/course-offerings/" if path.startswith("/api/v1/course-offerings/") else "/api/v1/offerings/"
                offering_id = unquote(path.removeprefix(prefix))
                result = repository.offering(offering_id)
                if result is None:
                    self._send_json(
                        404,
                        {"error": {"code": "OFFERING_NOT_FOUND", "message": "Course offering not found."}},
                    )
                else:
                    self._send_json(200, {"data": result})
            else:
                self._send_json(404, {"error": {"code": "NOT_FOUND", "message": "Endpoint not found."}})
        except (OSError, json.JSONDecodeError, KeyError, CatalogUnavailableError) as exc:
            self._send_json(
                503,
                {
                    "error": {
                        "code": "CATALOG_UNAVAILABLE",
                        "message": "Course catalog is temporarily unavailable.",
                        "detail": str(exc),
                    }
                },
            )

    def do_POST(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def _method_not_allowed(self) -> None:
        self._send_json(405, {"error": {"code": "READ_ONLY", "message": "The course catalog is read-only."}})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def main() -> None:
    host = os.environ.get("CATALOG_HOST", "127.0.0.1")
    port = int(os.environ.get("CATALOG_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), CatalogHandler)
    if isinstance(repository, CachedCatalogRepository):
        repository.start()
    print(f"Course catalog API listening at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping course catalog API.")
    finally:
        if isinstance(repository, CachedCatalogRepository):
            repository.stop()
        server.server_close()


if __name__ == "__main__":
    main()
