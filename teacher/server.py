"""教师课程注册网页：授课选择、学生名册与成绩录入 API。"""

from __future__ import annotations

import hmac
import json
import os
import sys
import threading
import time
import secrets
from datetime import date, datetime, time as datetime_time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from database.mysql_connection import get_connection, transaction  # noqa: E402
from database.session_auth import (  # noqa: E402
    checked_cookie_session, password_fingerprint, password_matches as _password_matches,
)


WEB_ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("TEACHER_HOST", "127.0.0.1")
PORT = int(os.environ.get("TEACHER_PORT", "8030"))
CATALOG_API_URL = os.environ.get("CATALOG_API_URL", "http://127.0.0.1:8000").rstrip("/")
SESSION_COOKIE = "teacher_session"
SESSION_LIFETIME_SECONDS = 4 * 60 * 60
MAX_REQUEST_BYTES = 64 * 1024
ACTIVE_ASSIGNMENT_STATES = ("SELECTED", "CONFIRMED")
ALLOWED_GRADES = {"A", "B", "C", "D", "F", "I"}

_sessions: dict[str, dict[str, Any]] = {}
_session_lock = threading.Lock()


class ApiError(Exception):
    """带有 HTTP 状态码和可显示校验信息的业务错误。"""

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


def _fetch_catalog() -> dict[str, Any]:
    request = Request(
        f"{CATALOG_API_URL}/api/v1/catalog",
        headers={"Accept": "application/json", "User-Agent": "CourseTeacherPortal/1.0"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ApiError("课程目录暂时不可用。请确认 app.py 正在运行后重试。", 503) from exc
    catalog = payload.get("data") if isinstance(payload, dict) else None
    required = ("departments", "faculty", "semesters", "courses", "courseOfferings")
    if not isinstance(catalog, dict) or any(not isinstance(catalog.get(key), list) for key in required):
        raise ApiError("课程目录接口返回的数据格式不完整。", 503)
    return catalog


def _to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _inside_window(today: date, start: Any, end: Any) -> bool:
    start_date = _to_date(start)
    end_date = _to_date(end)
    return start_date is not None and end_date is not None and start_date <= today <= end_date


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


def _conflicts(first: dict[str, Any], second: dict[str, Any]) -> bool:
    if not (_days(first) & _days(second)):
        return False
    first_start = _time_minutes(first.get("startTime"))
    first_end = _time_minutes(first.get("endTime"))
    second_start = _time_minutes(second.get("startTime"))
    second_end = _time_minutes(second.get("endTime"))
    return (
        None not in (first_start, first_end, second_start, second_end)
        and first_start < second_end
        and second_start < first_end
    )


def _assert_registration_open(connection: Any, semester_id: str) -> dict[str, Any]:
    period = _query_one(
        connection,
        "SELECT semester_id, registration_status, registration_start_at, registration_end_at "
        "FROM registration_periods WHERE semester_id = %s FOR UPDATE",
        (semester_id,),
    )
    if period is None:
        raise ApiError("该学期尚未配置注册时间。", 409)
    if (
        str(period["registration_status"]).upper() != "OPEN"
        or not _inside_window(date.today(), period["registration_start_at"], period["registration_end_at"])
    ):
        raise ApiError("注册已关闭，不能修改授课课程选择。", 409)
    return period


def _do_login(username: str, password: str) -> tuple[str, dict[str, Any]]:
    connection = get_connection()
    try:
        user = _query_one(
            connection,
            "SELECT u.user_id, u.username, u.password_hash, u.role, u.is_active, "
            "p.professor_id, p.full_name, p.status AS professor_status "
            "FROM app_users u JOIN professors p ON p.user_id = u.user_id "
            "WHERE u.username = %s LIMIT 1",
            (username,),
        )
    finally:
        connection.close()
    if (
        user is None
        or user.get("role") != "PROFESSOR"
        or not user.get("is_active")
        or user.get("professor_status") != "ACTIVE"
        or not _password_matches(password, str(user.get("password_hash", "")))
    ):
        raise ApiError("用户名或密码不正确，或教师账号暂不可用。", 401)

    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(24)
    user_info = {
        "userId": str(user["user_id"]),
        "professorId": str(user["professor_id"]),
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
            "password_fingerprint": password_fingerprint(str(user["password_hash"])),
            "csrf_token": csrf_token,
            "expires_at": now + SESSION_LIFETIME_SECONDS,
        }
    return token, {**user_info, "csrfToken": csrf_token}


def _selection_state(
    connection: Any, professor_id: str, semester_id: str, lock: bool = False
) -> list[dict[str, Any]]:
    suffix = " FOR UPDATE" if lock else ""
    return _query_all(
        connection,
        "SELECT professor_id, offering_id, semester_id, selection_status, selected_at "
        "FROM professor_offering_selections WHERE semester_id = %s "
        "ORDER BY offering_id" + suffix,
        (semester_id,),
    )


def _is_grade_term_complete(
    semester_id: str,
    completed_count: int,
    catalog: dict[str, Any],
) -> bool:
    semester = next(
        (item for item in catalog["semesters"] if str(item.get("semesterId")) == semester_id),
        None,
    )
    if semester:
        end_date = _to_date(semester.get("endDate"))
        return end_date is not None and end_date < date.today()
    # 演示库中部分历史学期早于课程目录服务的覆盖范围；已完成选课可作为学期结束标记。
    return completed_count > 0


def _grade_course_rows(connection: Any, professor_id: str) -> list[dict[str, Any]]:
    return _query_all(
        connection,
        "SELECT pos.offering_id, pos.semester_id, pos.selection_status, "
        "MAX(e.course_id) AS course_id, MAX(e.course_title_snapshot) AS course_title, "
        "COUNT(DISTINCT CASE WHEN e.enrollment_status IN ('ENROLLED', 'COMPLETED') "
        "THEN e.enrollment_id END) AS roster_count, "
        "COUNT(DISTINCT CASE WHEN e.enrollment_status = 'COMPLETED' "
        "THEN e.enrollment_id END) AS completed_count "
        "FROM professor_offering_selections pos "
        "LEFT JOIN student_schedules sch ON sch.semester_id = pos.semester_id "
        "LEFT JOIN enrollments e ON e.schedule_id = sch.schedule_id AND e.offering_id = pos.offering_id "
        "WHERE pos.professor_id = %s AND pos.selection_status IN ('SELECTED', 'CONFIRMED') "
        "GROUP BY pos.offering_id, pos.semester_id, pos.selection_status "
        "ORDER BY pos.semester_id DESC, pos.offering_id",
        (professor_id,),
    )


def _build_teacher_data(professor_id: str, requested_semester: str | None) -> dict[str, Any]:
    catalog = _fetch_catalog()
    catalog_offerings = {str(row.get("offeringId")): row for row in catalog["courseOfferings"]}
    catalog_courses = {str(row.get("courseId")): row for row in catalog["courses"]}
    catalog_semesters = {str(row.get("semesterId")): row for row in catalog["semesters"]}

    connection = get_connection()
    try:
        professor = _query_one(
            connection,
            "SELECT professor_id, full_name, status, department_id "
            "FROM professors WHERE professor_id = %s",
            (professor_id,),
        )
        if professor is None:
            raise ApiError("没有找到当前教师档案。", 404)
        period_rows = _query_all(
            connection,
            "SELECT semester_id, registration_status, registration_start_at, registration_end_at "
            "FROM registration_periods ORDER BY registration_start_at DESC",
        )
        periods = {str(row["semester_id"]): row for row in period_rows}
        available_semesters = [
            semester for semester in catalog["semesters"]
            if str(semester.get("semesterId")) in periods
        ]
        if requested_semester:
            semester_id = requested_semester
            if semester_id not in periods or semester_id not in catalog_semesters:
                raise ApiError("该学期未配置课程目录或注册时间。")
        else:
            open_period = next(
                (
                    semester for semester in available_semesters
                    if str(periods[str(semester["semesterId"])]["registration_status"]).upper() == "OPEN"
                    and _inside_window(
                        date.today(),
                        periods[str(semester["semesterId"])]["registration_start_at"],
                        periods[str(semester["semesterId"])]["registration_end_at"],
                    )
                ),
                None,
            )
            semester_id = str(
                open_period["semesterId"] if open_period else (
                    available_semesters[0]["semesterId"] if available_semesters else ""
                )
            )

        semester = catalog_semesters.get(semester_id, {})
        period = periods.get(semester_id)
        can_edit = bool(
            period
            and str(period["registration_status"]).upper() == "OPEN"
            and _inside_window(date.today(), period["registration_start_at"], period["registration_end_at"])
        )
        eligibility = _query_all(
            connection,
            "SELECT course_id FROM professor_course_eligibility WHERE professor_id = %s",
            (professor_id,),
        )
        eligible_ids = {str(row["course_id"]) for row in eligibility}
        assignments = _selection_state(connection, professor_id, semester_id) if semester_id else []
        assignment_by_offering = {str(row["offering_id"]): row for row in assignments}
        offers = []
        for offering_id, offering in catalog_offerings.items():
            if offering.get("semesterId") != semester_id:
                continue
            assignment = assignment_by_offering.get(offering_id)
            owner = str(assignment["professor_id"]) if assignment else None
            active = bool(assignment and assignment["selection_status"] in ACTIVE_ASSIGNMENT_STATES)
            owned_by_me = bool(assignment and owner == professor_id)
            qualified = offering.get("courseId") in eligible_ids
            if not qualified and not (owned_by_me and active):
                continue
            offers.append(
                {
                    **offering,
                    "course": catalog_courses.get(str(offering.get("courseId")), {}),
                    "selected": owned_by_me and active,
                    "reservedByOther": active and owner != professor_id,
                    "assignmentStatus": assignment["selection_status"] if assignment else None,
                    "retainedWithoutEligibility": not qualified,
                }
            )

        grade_rows = _grade_course_rows(connection, professor_id)
    finally:
        connection.close()

    roster_courses = []
    for row in grade_rows:
        row_semester_id = str(row["semester_id"])
        completed_count = int(row["completed_count"] or 0)
        can_edit_grades = _is_grade_term_complete(row_semester_id, completed_count, catalog)
        offering = catalog_offerings.get(str(row["offering_id"]), {})
        course = catalog_courses.get(str(row.get("course_id") or offering.get("courseId")), {})
        roster_courses.append(
            {
                "offeringId": str(row["offering_id"]),
                "semesterId": row_semester_id,
                "semesterName": catalog_semesters.get(row_semester_id, {}).get("name", row_semester_id),
                "courseId": str(row.get("course_id") or offering.get("courseId") or ""),
                "courseTitle": str(row.get("course_title") or course.get("title") or offering.get("courseId") or "课程"),
                "rosterCount": int(row["roster_count"] or 0),
                "completedCount": completed_count,
                "canEditGrades": can_edit_grades,
            }
        )

    return {
        "professor": professor,
        "semesterId": semester_id,
        "semester": semester,
        "semesters": available_semesters,
        "period": period,
        "canEditTeaching": can_edit,
        "eligibleOfferings": offers,
        "selectedOfferingIds": [item["offeringId"] for item in offers if item["selected"]],
        "rosterCourses": roster_courses,
        "gradeCourses": [course for course in roster_courses if course["canEditGrades"]],
        "catalog": catalog,
    }


def _save_teaching_selection(
    user_id: str,
    professor_id: str,
    semester_id: str,
    offering_ids: list[str],
    catalog: dict[str, Any],
) -> None:
    offerings = {str(row.get("offeringId")): row for row in catalog["courseOfferings"]}
    if len(offering_ids) != len(set(offering_ids)):
        raise ApiError("授课课程中存在重复开课。")
    if len(offering_ids) > 40:
        raise ApiError("一次最多选择 40 个开课。")
    for offering_id in offering_ids:
        if offering_id not in offerings or offerings[offering_id].get("semesterId") != semester_id:
            raise ApiError(f"开课 {offering_id} 不存在或不属于所选学期。")

    with transaction() as connection:
        professor = _query_one(
            connection,
            "SELECT professor_id FROM professors WHERE professor_id = %s AND status = 'ACTIVE' FOR UPDATE",
            (professor_id,),
        )
        if professor is None:
            raise ApiError("教师账号当前不可用。", 403)
        _assert_registration_open(connection, semester_id)
        eligible_rows = _query_all(
            connection,
            "SELECT course_id FROM professor_course_eligibility WHERE professor_id = %s",
            (professor_id,),
        )
        eligible_courses = {str(row["course_id"]) for row in eligible_rows}
        locked_rows = _selection_state(connection, professor_id, semester_id, lock=True)
        assignment_by_offering = {str(row["offering_id"]): row for row in locked_rows}
        issues = []
        selected_offerings: list[dict[str, Any]] = []
        for offering_id in offering_ids:
            offering = offerings[offering_id]
            course_id = str(offering.get("courseId", ""))
            assignment = assignment_by_offering.get(offering_id)
            retained = bool(assignment and str(assignment["professor_id"]) == professor_id
                            and assignment["selection_status"] in ACTIVE_ASSIGNMENT_STATES)
            if course_id not in eligible_courses and not retained:
                issues.append(f"你没有资格讲授课程 {course_id}。")
            if str(offering.get("status", "")).upper() not in {"OPEN", "开放", "FULL", "已满"}:
                issues.append(f"开课 {offering_id} 当前未开放。")
            selected_offerings.append(offering)

        for index, first in enumerate(selected_offerings):
            for second in selected_offerings[index + 1 :]:
                if _conflicts(first, second):
                    issues.append(
                        f"{first.get('offeringId')} 与 {second.get('offeringId')} 上课时间冲突。"
                    )

        for offering_id in sorted(offering_ids):
            row = assignment_by_offering.get(offering_id)
            if (
                row and row["selection_status"] in ACTIVE_ASSIGNMENT_STATES
                and str(row["professor_id"]) != professor_id
            ):
                issues.append(f"开课 {offering_id} 已由其他教师占用，不能修改。")
        if issues:
            raise ApiError("授课课程校验未通过，请调整选择。", 422, issues)

        cursor = connection.cursor()
        try:
            changed_ids = set()
            requested_ids = set(offering_ids)
            for row in locked_rows:
                offering_id = str(row["offering_id"])
                if (
                    str(row["professor_id"]) == professor_id
                    and row["selection_status"] in ACTIVE_ASSIGNMENT_STATES
                    and offering_id not in requested_ids
                ):
                    cursor.execute(
                        "UPDATE professor_offering_selections SET selection_status = 'WITHDRAWN' "
                        "WHERE professor_id = %s AND offering_id = %s AND semester_id = %s",
                        (professor_id, offering_id, semester_id),
                    )
                    changed_ids.add(offering_id)
            for offering_id in offering_ids:
                row = assignment_by_offering.get(offering_id)
                if row:
                    # 保留有效授课安排；撤回记录原位复用，避免 offering_id 唯一约束冲突。
                    if row["selection_status"] in ACTIVE_ASSIGNMENT_STATES:
                        continue
                    cursor.execute(
                        "UPDATE professor_offering_selections SET professor_id = %s, selection_status = 'SELECTED', "
                        "selected_at = CURRENT_TIMESTAMP(3) WHERE professor_id = %s AND offering_id = %s "
                        "AND semester_id = %s "
                        "AND selection_status = 'WITHDRAWN'",
                        (professor_id, row["professor_id"], offering_id, semester_id),
                    )
                    changed_ids.add(offering_id)
                    continue
                cursor.execute(
                    "INSERT INTO professor_offering_selections "
                    "(professor_id, offering_id, semester_id, selection_status) "
                    "VALUES (%s, %s, %s, 'SELECTED')",
                    (professor_id, offering_id, semester_id),
                )
                changed_ids.add(offering_id)

            current_rows = _selection_state(connection, professor_id, semester_id)
            current_by_offering = {str(row["offering_id"]): row for row in current_rows}

            def assignment_snapshot(row: dict[str, Any] | None) -> dict[str, Any] | None:
                if row is None:
                    return None
                return {
                    "professorId": str(row["professor_id"]),
                    "semesterId": str(row["semester_id"]),
                    "selectionStatus": row["selection_status"],
                    "selectedAt": str(row["selected_at"]),
                }

            changes = [
                {
                    "offeringId": offering_id,
                    "before": assignment_snapshot(assignment_by_offering.get(offering_id)),
                    "after": assignment_snapshot(current_by_offering.get(offering_id)),
                }
                for offering_id in sorted(changed_ids)
            ]
            cursor.execute(
                "INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, details_json) "
                "VALUES (%s, 'UPDATE_TEACHING_SELECTION', 'PROFESSOR_SCHEDULE', %s, %s)",
                (user_id, semester_id, json.dumps(
                    {"professorId": professor_id, "offeringIds": offering_ids, "assignmentChanges": changes},
                    ensure_ascii=False,
                )),
            )
        finally:
            cursor.close()


def _assert_teaching_assignment(
    connection: Any, professor_id: str, offering_id: str, lock: bool = False
) -> dict[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    assignment = _query_one(
        connection,
        "SELECT professor_id, offering_id, semester_id, selection_status "
        "FROM professor_offering_selections WHERE professor_id = %s AND offering_id = %s "
        "AND selection_status IN ('SELECTED', 'CONFIRMED')" + suffix,
        (professor_id, offering_id),
    )
    if assignment is None:
        raise ApiError("该开课不属于当前教师，无法查看名册或录入成绩。", 403)
    return assignment


def _assert_grade_period(
    connection: Any, professor_id: str, offering_id: str, catalog: dict[str, Any]
) -> dict[str, Any]:
    assignment = _assert_teaching_assignment(connection, professor_id, offering_id)
    counts = _query_one(
        connection,
        "SELECT COUNT(*) AS roster_count, "
        "SUM(CASE WHEN e.enrollment_status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed_count "
        "FROM enrollments e JOIN student_schedules sch ON sch.schedule_id = e.schedule_id "
        "WHERE e.offering_id = %s AND sch.semester_id = %s "
        "AND e.enrollment_status IN ('ENROLLED', 'COMPLETED')",
        (offering_id, assignment["semester_id"]),
    ) or {"roster_count": 0, "completed_count": 0}
    if not _is_grade_term_complete(
        str(assignment["semester_id"]), int(counts["completed_count"] or 0), catalog
    ):
        raise ApiError("成绩只能录入已结束学期的授课课程。", 409)
    return assignment


def _get_roster(professor_id: str, offering_id: str, catalog: dict[str, Any]) -> dict[str, Any]:
    connection = get_connection()
    try:
        assignment = _assert_teaching_assignment(connection, professor_id, offering_id)
        roster = _query_all(
            connection,
            "SELECT e.enrollment_id, e.course_id, e.course_title_snapshot, e.credits_snapshot, "
            "e.enrollment_status, sch.semester_id, s.student_id, s.full_name, "
            "g.letter_grade, g.graded_at "
            "FROM enrollments e JOIN student_schedules sch ON sch.schedule_id = e.schedule_id "
            "JOIN students s ON s.student_id = sch.student_id "
            "LEFT JOIN course_grades g ON g.enrollment_id = e.enrollment_id "
            "WHERE e.offering_id = %s AND sch.semester_id = %s "
            "AND e.enrollment_status IN ('ENROLLED', 'COMPLETED') "
            "ORDER BY s.student_id",
            (offering_id, assignment["semester_id"]),
        )
    finally:
        connection.close()
    can_edit_grades = _is_grade_term_complete(
        str(assignment["semester_id"]),
        sum(row["enrollment_status"] == "COMPLETED" for row in roster),
        catalog,
    )
    return {
        "offeringId": offering_id, "semesterId": assignment["semester_id"],
        "roster": roster, "canEditGrades": can_edit_grades,
    }


def _save_grades(
    user_id: str,
    professor_id: str,
    offering_id: str,
    grades: dict[str, Any],
    catalog: dict[str, Any],
) -> int:
    if len(grades) > 500:
        raise ApiError("一次最多提交 500 名学生的成绩。")
    normalized: dict[int, str] = {}
    for key, value in grades.items():
        try:
            enrollment_id = int(key)
        except (TypeError, ValueError) as exc:
            raise ApiError("成绩记录编号无效。") from exc
        if enrollment_id <= 0 or not isinstance(value, str):
            raise ApiError("成绩数据格式不正确。")
        grade = value.strip().upper()
        if grade and grade not in ALLOWED_GRADES:
            raise ApiError(f"{grade} 不是允许的成绩；可录入 A、B、C、D、F 或 I。")
        normalized[enrollment_id] = grade

    changed = 0
    with transaction() as connection:
        assignment = _assert_teaching_assignment(connection, professor_id, offering_id, lock=True)
        _assert_grade_period(connection, professor_id, offering_id, catalog)
        roster = _query_all(
            connection,
            "SELECT e.enrollment_id, e.enrollment_status FROM enrollments e "
            "JOIN student_schedules sch ON sch.schedule_id = e.schedule_id "
            "WHERE e.offering_id = %s AND sch.semester_id = %s "
            "AND e.enrollment_status IN ('ENROLLED', 'COMPLETED') FOR UPDATE",
            (offering_id, assignment["semester_id"]),
        )
        roster_by_id = {int(row["enrollment_id"]): row for row in roster}
        unknown = sorted(set(normalized) - set(roster_by_id))
        if unknown:
            raise ApiError("提交的数据中包含不属于该教师此开课名册的学生。", 403)

        cursor = connection.cursor()
        try:
            for enrollment_id, grade in normalized.items():
                if not grade:
                    continue
                cursor.execute(
                    "SELECT enrollment_id FROM course_grades WHERE enrollment_id = %s FOR UPDATE",
                    (enrollment_id,),
                )
                existing = cursor.fetchone()
                if existing:
                    cursor.execute(
                        "UPDATE course_grades SET professor_id = %s, letter_grade = %s, "
                        "graded_at = CURRENT_TIMESTAMP(3) WHERE enrollment_id = %s",
                        (professor_id, grade, enrollment_id),
                    )
                else:
                    cursor.execute(
                        "INSERT INTO course_grades (enrollment_id, professor_id, letter_grade) "
                        "VALUES (%s, %s, %s)",
                        (enrollment_id, professor_id, grade),
                    )
                # COMPLETED 表示该课程已有成绩记录；是否通过由共用先修规则检查成绩。
                # 保留 F/I 在成绩单中，避免把录入成绩等同于满足先修要求。
                cursor.execute(
                    "UPDATE enrollments SET enrollment_status = 'COMPLETED' "
                    "WHERE enrollment_id = %s AND enrollment_status = 'ENROLLED'",
                    (enrollment_id,),
                )
                changed += 1
            if changed:
                cursor.execute(
                    "INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, details_json) "
                    "VALUES (%s, 'SUBMIT_COURSE_GRADES', 'COURSE_OFFERING', %s, %s)",
                    (
                        user_id,
                        offering_id,
                        json.dumps({"semesterId": assignment["semester_id"], "recordCount": changed}),
                    ),
                )
        finally:
            cursor.close()
    return changed


class TeacherHandler(BaseHTTPRequestHandler):
    """提供教师静态网页和需要登录的后端接口。"""

    server_version = "CourseTeacherPortal/1.0"

    def _send_bytes(
        self, status: int, body: bytes, content_type: str, extra_headers: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self' data:",
        )
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
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError("请求内容不是有效的 JSON。") from exc
        if not isinstance(payload, dict):
            raise ApiError("请求内容格式不正确。")
        return payload

    def _cookie_session(self) -> tuple[str | None, dict[str, Any] | None]:
        return checked_cookie_session(
            self.headers, SESSION_COOKIE, _sessions, _session_lock, get_connection, "PROFESSOR",
        )

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
        static_files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        if request.path in static_files:
            filename, content_type = static_files[request.path]
            self._serve_file(filename, content_type)
            return
        try:
            if request.path == "/api/session":
                _, session = self._cookie_session()
                if session:
                    self._send_json(
                        200,
                        {"authenticated": True, "user": session["user"], "csrfToken": session["csrf_token"]},
                    )
                else:
                    self._send_json(200, {"authenticated": False})
                return
            if request.path == "/api/teacher/data":
                _, session = self._require_session()
                semester = parse_qs(request.query).get("semester", [None])[0]
                data = _build_teacher_data(str(session["user"]["professorId"]), semester)
                self._send_json(200, data)
                return
            if request.path == "/api/teacher/grades/roster":
                _, session = self._require_session()
                offering_id = parse_qs(request.query).get("offering", [""])[0]
                if not offering_id:
                    raise ApiError("请选择开课。")
                catalog = _fetch_catalog()
                self._send_json(
                    200,
                    _get_roster(str(session["user"]["professorId"]), offering_id, catalog),
                )
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
                    {
                        "Set-Cookie": (
                            f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; "
                            f"Max-Age={SESSION_LIFETIME_SECONDS}"
                        )
                    },
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

            user_id = str(session["user"]["userId"])
            professor_id = str(session["user"]["professorId"])
            if request.path == "/api/teacher/selections/save":
                semester_id = body.get("semesterId")
                offering_ids = body.get("offeringIds")
                if not isinstance(semester_id, str) or not semester_id:
                    raise ApiError("请选择学期。")
                if not isinstance(offering_ids, list) or any(not isinstance(item, str) for item in offering_ids):
                    raise ApiError("授课课程列表格式不正确。")
                catalog = _fetch_catalog()
                _save_teaching_selection(user_id, professor_id, semester_id, offering_ids, catalog)
                self._send_json(200, {"ok": True, "message": "授课课程选择已保存。"})
                return
            if request.path == "/api/teacher/grades/save":
                offering_id = body.get("offeringId")
                grades = body.get("grades")
                if not isinstance(offering_id, str) or not offering_id:
                    raise ApiError("请选择开课。")
                if not isinstance(grades, dict):
                    raise ApiError("成绩列表格式不正确。")
                catalog = _fetch_catalog()
                changed = _save_grades(user_id, professor_id, offering_id, grades, catalog)
                self._send_json(200, {"ok": True, "message": f"已保存 {changed} 条成绩记录。", "changed": changed})
                return
        except ApiError as exc:
            self._send_json(exc.status, {"error": str(exc), "issues": exc.issues})
            return
        except Exception as exc:
            print(f"[{self.log_date_time_string()}] POST {request.path} failed: {exc}")
            self._send_json(503, {"error": "服务暂时无法完成请求，请检查服务终端中的错误信息。"})
            return
        self._send_json(404, {"error": "找不到请求的接口。"})

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")


def main() -> None:
    if not 1 <= PORT <= 65535:
        raise SystemExit("TEACHER_PORT 必须在 1 到 65535 之间。")
    server = ThreadingHTTPServer((HOST, PORT), TeacherHandler)
    print(f"教师课程注册网页已启动：http://{HOST}:{PORT}")
    print(f"课程目录接口：{CATALOG_API_URL}/api/v1/catalog")
    print("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n教师网页服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
