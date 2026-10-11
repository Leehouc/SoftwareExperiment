"""学生课程注册网页：登录、选课、课表与成绩单 API。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from datetime import date, datetime, time as datetime_time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from database.mysql_connection import get_connection, transaction  # noqa: E402


WEB_ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("STUDENT_HOST", "127.0.0.1")
PORT = int(os.environ.get("STUDENT_PORT", "8020"))
CATALOG_API_URL = os.environ.get("CATALOG_API_URL", "http://127.0.0.1:8000").rstrip("/")
SESSION_COOKIE = "student_session"
SESSION_LIFETIME_SECONDS = 4 * 60 * 60
MAX_REQUEST_BYTES = 64 * 1024

_sessions: dict[str, dict[str, Any]] = {}
_session_lock = threading.Lock()


class ApiError(Exception):
    """带有 HTTP 状态码的业务错误。"""

    def __init__(self, message: str, status: int = 400, issues: list[str] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.issues = issues or []


def _query_all(connection: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        cursor.close()


def _query_one(connection: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        return cursor.fetchone()
    finally:
        cursor.close()


def _password_matches(password: str, encoded: str) -> bool:
    """验证 init_database.py 使用的 PBKDF2-SHA256 密码哈希。"""
    try:
        scheme, iteration_text, salt_text, digest_text = encoded.split("$", 3)
        iterations = int(iteration_text)
        if scheme != "pbkdf2_sha256" or not 10_000 <= iterations <= 2_000_000:
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(digest_text + "=" * (-len(digest_text) % 4))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _fetch_catalog() -> dict[str, Any]:
    request = Request(
        f"{CATALOG_API_URL}/api/v1/catalog",
        headers={"Accept": "application/json", "User-Agent": "CourseStudentPortal/1.0"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ApiError("课程目录暂时不可用。请确认 app.py 正在运行后重试。", 503) from exc
    catalog = payload.get("data") if isinstance(payload, dict) else None
    required = ("departments", "faculty", "semesters", "courses", "prerequisites", "courseOfferings")
    if not isinstance(catalog, dict) or any(not isinstance(catalog.get(key), list) for key in required):
        raise ApiError("课程目录接口返回的数据格式不完整。", 503)
    return catalog


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _inside_window(today: date, start: Any, end: Any) -> bool:
    start_date = _to_date(start)
    end_date = _to_date(end)
    return start_date is not None and end_date is not None and start_date <= today <= end_date


def _schedule_from_body(body: dict[str, Any], catalog: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    semester_id = body.get("semesterId")
    primary = body.get("primary")
    alternate = body.get("alternate")
    if not isinstance(semester_id, str) or not semester_id:
        raise ApiError("请选择学期。")
    if semester_id not in {semester.get("semesterId") for semester in catalog["semesters"]}:
        raise ApiError("课程目录中没有这个学期。")
    if not isinstance(primary, list) or not isinstance(alternate, list):
        raise ApiError("课表格式不正确。")
    if any(not isinstance(item, str) for item in primary + alternate):
        raise ApiError("开课编号格式不正确。")
    if len(primary) > 4 or len(alternate) > 2:
        raise ApiError("最多选择 4 门主选课和 2 门备选课。")
    if len(set(primary)) != len(primary) or len(set(alternate)) != len(alternate):
        raise ApiError("同一开课不能重复选择。")
    if set(primary) & set(alternate):
        raise ApiError("同一门开课不能同时设为主选和备选。")

    offerings = {item.get("offeringId"): item for item in catalog["courseOfferings"]}
    selected_courses: set[str] = set()
    for offering_id in primary + alternate:
        offering = offerings.get(offering_id)
        if not offering or offering.get("semesterId") != semester_id:
            raise ApiError("所选开课不存在，或不属于所选学期。")
        course_id = str(offering.get("courseId", ""))
        if course_id in selected_courses:
            raise ApiError("同一门课程不能重复列为主选或备选。")
        selected_courses.add(course_id)
    return semester_id, primary, alternate


def _get_period(connection: Any, semester_id: str, lock: bool = False) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if lock else ""
    return _query_one(
        connection,
        "SELECT semester_id, registration_status, registration_start_at, registration_end_at, "
        "add_drop_start_at, add_drop_end_at FROM registration_periods "
        "WHERE semester_id = %s" + suffix,
        (semester_id,),
    )


def _edit_mode(period: dict[str, Any] | None, previously_submitted: bool) -> str:
    if period is None:
        raise ApiError("该学期尚未配置注册时间。", 409)
    today = date.today()
    add_drop_open = _inside_window(today, period["add_drop_start_at"], period["add_drop_end_at"])
    if previously_submitted:
        if add_drop_open:
            return "add_drop"
        raise ApiError("已提交课表只能在加退选期间修改。", 409)
    initial_window_open = (
        str(period["registration_status"]).upper() == "OPEN"
        and _inside_window(today, period["registration_start_at"], period["registration_end_at"])
    )
    if initial_window_open:
        return "registration"
    if add_drop_open:
        return "add_drop"
    raise ApiError("当前不在注册或加退选时间内，暂时不能修改课表。", 409)


def _get_or_create_schedule(
    connection: Any, student_id: str, semester_id: str
) -> dict[str, Any]:
    student = _query_one(
        connection,
        "SELECT student_id FROM students WHERE student_id = %s FOR UPDATE",
        (student_id,),
    )
    if student is None:
        raise ApiError("没有找到当前学生档案。", 404)
    row = _query_one(
        connection,
        "SELECT schedule_id, status FROM student_schedules "
        "WHERE student_id = %s AND semester_id = %s FOR UPDATE",
        (student_id, semester_id),
    )
    if row:
        return row
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO student_schedules (student_id, semester_id, status) VALUES (%s, %s, 'DRAFT')",
            (student_id, semester_id),
        )
        return {"schedule_id": cursor.lastrowid, "status": "DRAFT"}
    finally:
        cursor.close()


def _schedule_has_enrollments(connection: Any, schedule_id: int) -> bool:
    row = _query_one(
        connection,
        "SELECT 1 AS found FROM enrollments WHERE schedule_id = %s LIMIT 1",
        (schedule_id,),
    )
    return row is not None


def _write_choices(
    connection: Any,
    schedule_id: int,
    primary: list[str],
    alternate: list[str],
    enrolled_ids: set[str] | None = None,
) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("DELETE FROM schedule_choices WHERE schedule_id = %s", (schedule_id,))
        rows = [
            (
                schedule_id,
                offering_id,
                "PRIMARY",
                order,
                "ENROLLED" if enrolled_ids and offering_id in enrolled_ids else "SELECTED",
            )
            for order, offering_id in enumerate(primary, start=1)
        ]
        rows.extend(
            (schedule_id, offering_id, "ALTERNATE", order, "SELECTED")
            for order, offering_id in enumerate(alternate, start=1)
        )
        if rows:
            cursor.executemany(
                "INSERT INTO schedule_choices "
                "(schedule_id, offering_id, choice_type, preference_order, selection_status) "
                "VALUES (%s, %s, %s, %s, %s)",
                rows,
            )
    finally:
        cursor.close()


def _insert_audit(
    connection: Any, user_id: str, action: str, semester_id: str, detail: dict[str, Any]
) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, details_json) "
            "VALUES (%s, %s, 'STUDENT_SCHEDULE', %s, %s)",
            (user_id, action, semester_id, json.dumps(detail, ensure_ascii=False)),
        )
    finally:
        cursor.close()


def _days(offering: dict[str, Any]) -> set[str]:
    values = offering.get("days", [])
    if isinstance(values, str):
        values = values.split(",")
    return {str(value).strip().upper() for value in values if str(value).strip()}


def _time_minutes(value: Any) -> int | None:
    if isinstance(value, datetime_time):
        return value.hour * 60 + value.minute
    try:
        hour, minute = str(value).split(":", 1)
        return int(hour) * 60 + int(minute[:2])
    except (ValueError, AttributeError):
        return None


def _validate_submission(
    connection: Any,
    student_id: str,
    schedule_id: int,
    semester_id: str,
    primary: list[str],
    alternate: list[str],
    catalog: dict[str, Any],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    issues: list[str] = []
    offerings = {item.get("offeringId"): item for item in catalog["courseOfferings"]}
    courses = {item.get("courseId"): item for item in catalog["courses"]}
    prerequisites: dict[str, set[str]] = {}
    for item in catalog["prerequisites"]:
        prerequisites.setdefault(item.get("courseId", ""), set()).add(item.get("prerequisiteCourseId", ""))

    if len(primary) != 4:
        issues.append("提交前需要选择恰好 4 门主选课。")
    if len(alternate) != 2:
        issues.append("提交前需要选择恰好 2 门备选课。")
    if len({offerings.get(item, {}).get("courseId") for item in primary}) != len(primary):
        issues.append("主选课中不能重复选择同一门课程的不同班次。")

    completed_rows = _query_all(
        connection,
        "SELECT DISTINCT e.course_id FROM enrollments e "
        "JOIN student_schedules sch ON sch.schedule_id = e.schedule_id "
        "WHERE sch.student_id = %s AND e.enrollment_status = 'COMPLETED'",
        (student_id,),
    )
    completed = {str(row["course_id"]) for row in completed_rows}

    primary_offerings: list[dict[str, Any]] = []
    for offering_id in sorted(primary):
        offering = offerings.get(offering_id)
        if not offering:
            issues.append(f"找不到主选开课 {offering_id}。")
            continue
        primary_offerings.append(offering)
        if offering.get("semesterId") != semester_id:
            issues.append(f"开课 {offering_id} 不属于当前学期。")
        if str(offering.get("status", "")).upper() not in {"OPEN", "开放"}:
            issues.append(f"{offering_id} 当前未开放，不能提交。")
        course_id = str(offering.get("courseId", ""))
        for prerequisite_id in prerequisites.get(course_id, set()):
            if prerequisite_id and prerequisite_id not in completed:
                prerequisite = courses.get(prerequisite_id, {})
                issues.append(
                    f"{offering.get('courseId')} {offering.get('courseTitle', courses.get(course_id, {}).get('title', ''))} "
                    f"缺少先修课程 {prerequisite.get('title', prerequisite_id)}。"
                )

    for offering_id in sorted(alternate):
        offering = offerings.get(offering_id)
        if not offering:
            issues.append(f"找不到备选开课 {offering_id}。")
            continue
        if offering.get("semesterId") != semester_id:
            issues.append(f"备选开课 {offering_id} 不属于当前学期。")
        if str(offering.get("status", "")).upper() not in {"OPEN", "开放"}:
            issues.append(f"备选开课 {offering_id} 当前未开放。")
        course_id = str(offering.get("courseId", ""))
        for prerequisite_id in prerequisites.get(course_id, set()):
            if prerequisite_id and prerequisite_id not in completed:
                prerequisite = courses.get(prerequisite_id, {})
                issues.append(
                    f"备选课程 {course_id} 缺少先修课程 {prerequisite.get('title', prerequisite_id)}。"
                )

    for index, first in enumerate(primary_offerings):
        for second in primary_offerings[index + 1 :]:
            common_days = _days(first) & _days(second)
            first_start, first_end = _time_minutes(first.get("startTime")), _time_minutes(first.get("endTime"))
            second_start, second_end = _time_minutes(second.get("startTime")), _time_minutes(second.get("endTime"))
            if (
                common_days
                and None not in (first_start, first_end, second_start, second_end)
                and first_start < second_end
                and second_start < first_end
            ):
                issues.append(
                    f"{first.get('offeringId')} 与 {second.get('offeringId')} 上课时间冲突。"
                )

    selected_ids = sorted(set(primary + alternate))
    for offering_id in selected_ids:
        offering = offerings.get(offering_id)
        if not offering:
            continue
        assignment = _query_one(
            connection,
            "SELECT professor_id FROM professor_offering_selections "
            "WHERE offering_id = %s AND semester_id = %s "
            "AND selection_status IN ('SELECTED', 'CONFIRMED') FOR UPDATE",
            (offering_id, semester_id),
        )
        if assignment is None:
            issues.append(f"{offering_id} 尚未确认授课教师，暂不能作为选课或备选课程。")

        occupied_rows = _query_all(
            connection,
            "SELECT enrollment_id FROM enrollments "
            "WHERE offering_id = %s AND enrollment_status = 'ENROLLED' "
            "AND schedule_id <> %s FOR UPDATE",
            (offering_id, schedule_id),
        )
        external_count = int(offering.get("enrolledCount") or 0)
        local_count = len(occupied_rows)
        capacity = int(offering.get("capacity") or 0)
        if capacity > 0 and external_count + local_count >= capacity:
            label = "主选课" if offering_id in primary else "备选课"
            issues.append(f"{label} {offering_id} 已满，请更换后再提交。")

    return issues, offerings


def _do_login(username: str, password: str) -> tuple[str, dict[str, Any]]:
    connection = get_connection()
    try:
        user = _query_one(
            connection,
            "SELECT u.user_id, u.username, u.password_hash, u.role, u.is_active, "
            "s.student_id, s.full_name, s.status AS student_status "
            "FROM app_users u JOIN students s ON s.user_id = u.user_id "
            "WHERE u.username = %s LIMIT 1",
            (username,),
        )
    finally:
        connection.close()

    if (
        user is None
        or user.get("role") != "STUDENT"
        or not user.get("is_active")
        or user.get("student_status") != "ACTIVE"
        or not _password_matches(password, str(user.get("password_hash", "")))
    ):
        raise ApiError("用户名或密码不正确，或学生账号暂不可用。", 401)

    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(24)
    user_info = {
        "userId": str(user["user_id"]),
        "studentId": str(user["student_id"]),
        "username": str(user["username"]),
        "fullName": str(user["full_name"]),
    }
    with _session_lock:
        now = time.time()
        expired = [key for key, session in _sessions.items() if session["expires_at"] <= now]
        for key in expired:
            _sessions.pop(key, None)
        _sessions[token] = {
            "user": user_info,
            "csrf_token": csrf_token,
            "expires_at": now + SESSION_LIFETIME_SECONDS,
        }
    return token, {**user_info, "csrfToken": csrf_token}


def _build_student_data(student_id: str, requested_semester: str | None) -> dict[str, Any]:
    catalog: dict[str, Any] | None
    catalog_error: str | None = None
    try:
        catalog = _fetch_catalog()
    except ApiError as exc:
        catalog = None
        catalog_error = str(exc)

    connection = get_connection()
    try:
        student = _query_one(
            connection,
            "SELECT student_id, full_name, status, graduation_date "
            "FROM students WHERE student_id = %s",
            (student_id,),
        )
        if student is None:
            raise ApiError("没有找到当前学生档案。", 404)

        period_rows = _query_all(
            connection,
            "SELECT semester_id, registration_status, registration_start_at, registration_end_at, "
            "add_drop_start_at, add_drop_end_at FROM registration_periods "
            "ORDER BY registration_start_at DESC",
        )
        period_by_id = {str(row["semester_id"]): row for row in period_rows}
        catalog_semesters = catalog["semesters"] if catalog else []
        valid_ids = {str(item.get("semesterId")) for item in catalog_semesters}
        matching_periods = [row for row in period_rows if str(row["semester_id"]) in valid_ids]
        if requested_semester:
            semester_id = requested_semester
            if semester_id not in valid_ids:
                raise ApiError("课程目录中没有这个学期。")
        else:
            open_period = next(
                (row for row in matching_periods if str(row["registration_status"]).upper() == "OPEN"),
                None,
            )
            semester_id = str(open_period["semester_id"]) if open_period else (
                str(matching_periods[0]["semester_id"]) if matching_periods else ""
            )

        period = period_by_id.get(semester_id)
        schedule = _query_one(
            connection,
            "SELECT schedule_id, status, created_at, updated_at FROM student_schedules "
            "WHERE student_id = %s AND semester_id = %s",
            (student_id, semester_id),
        ) if semester_id else None

        choices: list[dict[str, Any]] = []
        enrollments: list[dict[str, Any]] = []
        if schedule:
            choices = _query_all(
                connection,
                "SELECT offering_id, choice_type, preference_order, selection_status "
                "FROM schedule_choices WHERE schedule_id = %s "
                "ORDER BY choice_type, preference_order",
                (schedule["schedule_id"],),
            )
            enrollments = _query_all(
                connection,
                "SELECT enrollment_id, offering_id, course_id, course_title_snapshot, credits_snapshot, "
                "enrollment_status, enrolled_at FROM enrollments WHERE schedule_id = %s "
                "ORDER BY enrollment_status, course_title_snapshot",
                (schedule["schedule_id"],),
            )

        transcript = _query_all(
            connection,
            "SELECT semester_id, offering_id, course_id, course_title_snapshot, credits_snapshot, "
            "letter_grade, professor_id, graded_at FROM v_student_report_cards "
            "WHERE student_id = %s ORDER BY graded_at DESC, course_title_snapshot",
            (student_id,),
        )
        count_rows = _query_all(
            connection,
            "SELECT offering_id, COUNT(*) AS total FROM enrollments "
            "WHERE enrollment_status = 'ENROLLED' GROUP BY offering_id",
        )
    finally:
        connection.close()

    matching_semesters = [
        item for item in catalog_semesters
        if str(item.get("semesterId")) in period_by_id
    ]
    today = date.today()
    registration_open = bool(
        period
        and str(period["registration_status"]).upper() == "OPEN"
        and _inside_window(today, period["registration_start_at"], period["registration_end_at"])
    )
    add_drop_open = bool(
        period and _inside_window(today, period["add_drop_start_at"], period["add_drop_end_at"])
    )
    previously_submitted = bool(
        schedule
        and (
            str(schedule.get("status", "")).upper() in {"SUBMITTED", "FINALIZED"}
            or bool(enrollments)
        )
    )
    can_edit = add_drop_open if previously_submitted else (registration_open or add_drop_open)
    return {
        "student": student,
        "semesterId": semester_id,
        "semesters": matching_semesters,
        "period": period,
        "schedule": schedule,
        "choices": choices,
        "enrollments": enrollments,
        "transcript": transcript,
        "offeringCounts": {str(row["offering_id"]): int(row["total"]) for row in count_rows},
        "catalog": catalog,
        "catalogError": catalog_error,
        "canEdit": can_edit,
        "registrationOpen": registration_open,
        "addDropOpen": add_drop_open,
        "previouslySubmitted": previously_submitted,
    }


class StudentHandler(BaseHTTPRequestHandler):
    """为学生网页提供静态资源和受登录保护的 API。"""

    server_version = "CourseStudentPortal/1.0"

    def _send_bytes(
        self, status: int, body: bytes, content_type: str, extra_headers: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self' data:")
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(
        self, status: int, payload: dict[str, Any], extra_headers: dict[str, str] | None = None
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8", extra_headers)

    def _json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError("请求长度无效。") from exc
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise ApiError("请求内容为空或过大。")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError("请求内容不是有效的 JSON。") from exc
        if not isinstance(data, dict):
            raise ApiError("请求内容格式不正确。")
        return data

    def _cookie_session(self) -> tuple[str | None, dict[str, Any] | None]:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None, None
        morsel = cookie.get(SESSION_COOKIE)
        token = morsel.value if morsel else None
        if not token:
            return None, None
        with _session_lock:
            session = _sessions.get(token)
            if session and session["expires_at"] <= time.time():
                _sessions.pop(token, None)
                session = None
        return token, session

    def _require_session(self) -> tuple[str, dict[str, Any]]:
        token, session = self._cookie_session()
        if not token or not session:
            raise ApiError("登录状态已失效，请重新登录。", 401)
        return token, session

    def _require_csrf(self, session: dict[str, Any]) -> None:
        supplied = self.headers.get("X-CSRF-Token", "")
        if not supplied or not hmac.compare_digest(supplied, str(session["csrf_token"])):
            raise ApiError("请求校验失败，请刷新页面后重试。", 403)

    def _serve_file(self, filename: str, content_type: str) -> None:
        try:
            body = (WEB_ROOT / filename).read_bytes()
        except OSError:
            self._send_json(500, {"error": "网页文件读取失败。"})
            return
        self._send_bytes(200, body, content_type)

    def do_GET(self) -> None:  # noqa: N802
        request = urlparse(self.path)
        if request.path == "/":
            self._serve_file("index.html", "text/html; charset=utf-8")
            return
        if request.path == "/styles.css":
            self._serve_file("styles.css", "text/css; charset=utf-8")
            return
        if request.path == "/app.js":
            self._serve_file("app.js", "text/javascript; charset=utf-8")
            return
        try:
            if request.path == "/api/session":
                _, session = self._cookie_session()
                if session:
                    self._send_json(200, {"authenticated": True, "user": session["user"], "csrfToken": session["csrf_token"]})
                else:
                    self._send_json(200, {"authenticated": False})
                return
            if request.path == "/api/student/data":
                _, session = self._require_session()
                semester = parse_qs(request.query).get("semester", [None])[0]
                data = _build_student_data(str(session["user"]["studentId"]), semester)
                self._send_json(200, data)
                return
        except ApiError as exc:
            self._send_json(exc.status, {"error": str(exc), "issues": exc.issues})
            return
        except Exception as exc:
            print(f"[{self.log_date_time_string()}] GET {request.path} failed: {exc}")
            self._send_json(503, {"error": "服务暂时无法完成请求，请检查服务终端中的错误信息。"})
            return
        self._send_json(404, {"error": "找不到请求的页面或接口。"})

    def do_POST(self) -> None:  # noqa: N802
        request = urlparse(self.path)
        try:
            body = self._json_body()
            if request.path == "/api/login":
                username = str(body.get("username", "")).strip()
                password = body.get("password", "")
                if not username or not isinstance(password, str) or not password:
                    raise ApiError("请输入用户名和密码。", 401)
                if len(username) > 64 or len(password) > 256:
                    raise ApiError("用户名或密码长度不正确。", 401)
                token, user = _do_login(username, password)
                self._send_json(
                    200,
                    {"ok": True, "user": user},
                    {"Set-Cookie": f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_LIFETIME_SECONDS}"},
                )
                return

            token, session = self._require_session()
            self._require_csrf(session)
            if request.path == "/api/logout":
                with _session_lock:
                    _sessions.pop(token, None)
                self._send_json(
                    200,
                    {"ok": True},
                    {"Set-Cookie": f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"},
                )
                return

            student_id = str(session["user"]["studentId"])
            user_id = str(session["user"]["userId"])
            if request.path in ("/api/student/schedule/save", "/api/student/schedule/submit"):
                catalog = _fetch_catalog()
                semester_id, primary, alternate = _schedule_from_body(body, catalog)
                self._save_or_submit(
                    request.path.endswith("/submit"),
                    user_id,
                    student_id,
                    semester_id,
                    primary,
                    alternate,
                    catalog,
                )
                self._send_json(200, {"ok": True, "message": "课表已保存。" if request.path.endswith("/save") else "课表已提交。"})
                return
            if request.path == "/api/student/schedule/delete":
                semester_id = body.get("semesterId")
                if not isinstance(semester_id, str) or not semester_id:
                    raise ApiError("请选择学期。")
                if body.get("confirm") is not True:
                    raise ApiError("请确认删除课表。")
                self._delete_schedule(user_id, student_id, semester_id)
                self._send_json(200, {"ok": True, "message": "课表已取消。"})
                return
        except ApiError as exc:
            self._send_json(exc.status, {"error": str(exc), "issues": exc.issues})
            return
        except Exception as exc:
            print(f"[{self.log_date_time_string()}] POST {request.path} failed: {exc}")
            self._send_json(503, {"error": "服务暂时无法完成请求，请检查服务终端中的错误信息。"})
            return
        self._send_json(404, {"error": "找不到请求的接口。"})

    def _save_or_submit(
        self,
        submit: bool,
        user_id: str,
        student_id: str,
        semester_id: str,
        primary: list[str],
        alternate: list[str],
        catalog: dict[str, Any],
    ) -> None:
        with transaction() as connection:
            schedule = _get_or_create_schedule(connection, student_id, semester_id)
            schedule_id = int(schedule["schedule_id"])
            previously_submitted = _schedule_has_enrollments(connection, schedule_id)
            period = _get_period(connection, semester_id)
            mode = _edit_mode(period, previously_submitted)

            if not submit:
                _write_choices(connection, schedule_id, primary, alternate)
                cursor = connection.cursor()
                try:
                    cursor.execute(
                        "UPDATE student_schedules SET status = 'DRAFT' WHERE schedule_id = %s",
                        (schedule_id,),
                    )
                finally:
                    cursor.close()
                _insert_audit(
                    connection,
                    user_id,
                    "SAVE_STUDENT_SCHEDULE",
                    semester_id,
                    {"primary": primary, "alternate": alternate, "mode": mode},
                )
                return

            issues, offerings = _validate_submission(
                connection, student_id, schedule_id, semester_id, primary, alternate, catalog
            )
            if issues:
                raise ApiError("课表校验未通过，请根据提示调整。", 422, issues)

            enrolled_ids = set(primary)
            _write_choices(connection, schedule_id, primary, alternate, enrolled_ids)
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    "UPDATE enrollments SET enrollment_status = 'DROPPED' "
                    "WHERE schedule_id = %s AND enrollment_status = 'ENROLLED' "
                    "AND offering_id NOT IN (" + ",".join(["%s"] * len(primary)) + ")",
                    (schedule_id, *primary),
                )
                for offering_id in primary:
                    offering = offerings[offering_id]
                    course = next(
                        (item for item in catalog["courses"] if item.get("courseId") == offering.get("courseId")),
                        {},
                    )
                    cursor.execute(
                        "SELECT enrollment_id FROM enrollments WHERE schedule_id = %s AND offering_id = %s",
                        (schedule_id, offering_id),
                    )
                    existing = cursor.fetchone()
                    values = (
                        str(offering.get("courseId", "")),
                        str(course.get("title", offering.get("courseId", ""))),
                        int(course.get("credits") or 1),
                    )
                    if existing:
                        cursor.execute(
                            "UPDATE enrollments SET course_id = %s, course_title_snapshot = %s, "
                            "credits_snapshot = %s, enrollment_status = 'ENROLLED', enrolled_at = CURRENT_TIMESTAMP(3) "
                            "WHERE enrollment_id = %s",
                            (*values, existing["enrollment_id"]),
                        )
                    else:
                        cursor.execute(
                            "INSERT INTO enrollments "
                            "(schedule_id, offering_id, course_id, course_title_snapshot, credits_snapshot, enrollment_status) "
                            "VALUES (%s, %s, %s, %s, %s, 'ENROLLED')",
                            (schedule_id, offering_id, *values),
                        )
                cursor.execute(
                    "UPDATE student_schedules SET status = 'SUBMITTED' WHERE schedule_id = %s",
                    (schedule_id,),
                )
            finally:
                cursor.close()
            _insert_audit(
                connection,
                user_id,
                "SUBMIT_STUDENT_SCHEDULE" if not previously_submitted else "UPDATE_STUDENT_SCHEDULE",
                semester_id,
                {"primary": primary, "alternate": alternate, "mode": mode},
            )

    def _delete_schedule(self, user_id: str, student_id: str, semester_id: str) -> None:
        with transaction() as connection:
            schedule = _query_one(
                connection,
                "SELECT schedule_id FROM student_schedules "
                "WHERE student_id = %s AND semester_id = %s FOR UPDATE",
                (student_id, semester_id),
            )
            if schedule is None:
                raise ApiError("当前学期还没有课表。", 404)
            schedule_id = int(schedule["schedule_id"])
            submitted = _schedule_has_enrollments(connection, schedule_id)
            period = _get_period(connection, semester_id)
            _edit_mode(period, submitted)
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "UPDATE enrollments SET enrollment_status = 'DROPPED' "
                    "WHERE schedule_id = %s AND enrollment_status = 'ENROLLED'",
                    (schedule_id,),
                )
                cursor.execute(
                    "UPDATE schedule_choices SET selection_status = 'CANCELLED' WHERE schedule_id = %s",
                    (schedule_id,),
                )
                cursor.execute(
                    "UPDATE student_schedules SET status = 'CANCELLED' WHERE schedule_id = %s",
                    (schedule_id,),
                )
            finally:
                cursor.close()
            _insert_audit(
                connection,
                user_id,
                "CANCEL_STUDENT_SCHEDULE",
                semester_id,
                {"scheduleId": schedule_id},
            )

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")


def main() -> None:
    if not 1 <= PORT <= 65535:
        raise SystemExit("STUDENT_PORT 必须在 1 到 65535 之间。")
    server = ThreadingHTTPServer((HOST, PORT), StudentHandler)
    print(f"学生课程注册网页已启动：http://{HOST}:{PORT}")
    print(f"课程目录接口：{CATALOG_API_URL}/api/v1/catalog")
    print("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n学生网页服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
