"""注册管理员网页服务：档案维护、注册期关闭和账单队列。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from database.mysql_connection import get_connection, transaction  # noqa: E402
from database.academic_rules import passed_courses_by_student  # noqa: E402
from database.session_auth import (  # noqa: E402
    checked_cookie_session, password_fingerprint, password_matches as _password_matches,
)
from billing.contract import (  # noqa: E402
    CURRENCY, MAX_PAYLOAD_BYTES, SCHEMA_VERSION, ContractError, validate_bill, validate_receipt,
)


WEB_ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("ADMINISTER_HOST", "127.0.0.1")
PORT = int(os.environ.get("ADMINISTER_PORT", "8040"))
CATALOG_API_URL = os.environ.get("CATALOG_API_URL", "http://127.0.0.1:8000").rstrip("/")
SESSION_COOKIE = "registrar_session"
SESSION_LIFETIME_SECONDS = 4 * 60 * 60
MAX_REQUEST_BYTES = 64 * 1024
PBKDF2_ITERATIONS = 310_000
MINIMUM_OFFERING_ENROLLMENT = 3
MAXIMUM_COURSES_PER_SCHEDULE = 4
# 演示收费标准：每学分 150 元；金额按分保存。真实费率应由账单系统配置。
AMOUNT_CENTS_PER_CREDIT = int(os.environ.get("BILLING_AMOUNT_CENTS_PER_CREDIT", "15000"))
BILLING_API_URL = os.environ.get("BILLING_API_URL", "").strip()
BILLING_RETRY_SECONDS = 30

_sessions: dict[str, dict[str, Any]] = {}
_session_lock = threading.Lock()


class ApiError(Exception):
    """带有 HTTP 状态码的业务错误。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


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


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    salt_text = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    digest_text = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt_text}${digest_text}"


def _encrypt_ssn(value: str) -> bytes | None:
    value = value.strip()
    if not value:
        return None
    normalized_ssn = value.replace("-", "").replace(" ", "")
    if not re.fullmatch(r"\d{9}", normalized_ssn):
        raise ApiError("社会安全号码需为 9 位数字，可使用 XXX-XX-XXXX 格式。")
    encoded_key = os.environ.get("SSN_ENCRYPTION_KEY", "")
    if not encoded_key:
        raise ApiError("录入证件号码前，请先配置 SSN_ENCRYPTION_KEY 加密密钥。")
    try:
        key = base64.urlsafe_b64decode(encoded_key + "=" * (-len(encoded_key) % 4))
        if len(key) != 32:
            raise ValueError("key must have 32 bytes")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except (ValueError, ImportError) as exc:
        raise ApiError("证件加密不可用，请安装 cryptography 并配置有效的 32 字节密钥。", 503) from exc
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, normalized_ssn.encode("utf-8"), b"course-registration-ssn-v1")


def _insert_audit(connection: Any, actor_user_id: str, action: str, entity_type: str,
                  entity_id: str, details: dict[str, Any]) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, details_json) "
            "VALUES (%s, %s, %s, %s, %s)",
            (actor_user_id, action, entity_type, entity_id,
             json.dumps(details, ensure_ascii=False, separators=(",", ":"))),
        )
    finally:
        cursor.close()


def _fetch_catalog() -> dict[str, Any]:
    request = Request(
        f"{CATALOG_API_URL}/api/v1/catalog",
        headers={"Accept": "application/json", "User-Agent": "CourseRegistrarPortal/1.0"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ApiError("课程目录暂时不可用。请确认 app.py 正在运行后重试。", 503) from exc
    catalog = payload.get("data") if isinstance(payload, dict) else None
    required = ("departments", "semesters", "courses", "prerequisites", "courseOfferings")
    if not isinstance(catalog, dict) or any(not isinstance(catalog.get(key), list) for key in required):
        raise ApiError("课程目录接口返回的数据格式不完整。", 503)
    return catalog


def _create_id(connection: Any, table: str, column: str, prefix: str) -> str:
    # 带有随机后缀的编号，降低多人同时新增时的冲突概率；数据库主键负责最终唯一性。
    for _ in range(8):
        value = prefix + date.today().strftime("%y") + secrets.token_hex(3).upper()
        if _query_one(connection, f"SELECT 1 AS found FROM {table} WHERE {column} = %s", (value,)) is None:
            return value
    raise ApiError("暂时无法生成唯一编号，请重试。", 503)


def _validate_profile(body: dict[str, Any], kind: str, *, creating: bool) -> dict[str, Any]:
    full_name = str(body.get("fullName", "")).strip()
    birth_date = str(body.get("dateOfBirth", "")).strip()
    status = str(body.get("status", "ACTIVE")).upper()
    username = str(body.get("username", "")).strip()
    if not full_name or len(full_name) > 100:
        raise ApiError("姓名不能为空，且不能超过 100 个字符。")
    try:
        date.fromisoformat(birth_date)
    except ValueError as exc:
        raise ApiError("请输入有效的出生日期。") from exc
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username):
        raise ApiError("用户名需为 3 到 64 位字母、数字、点、下划线或连字符。")
    allowed_statuses = {"ACTIVE", "INACTIVE", "GRADUATED"} if kind == "student" else {"ACTIVE", "INACTIVE"}
    if status not in allowed_statuses:
        raise ApiError("档案状态无效。")
    password = body.get("password", "")
    if creating and (not isinstance(password, str) or len(password) < 10 or len(password) > 256):
        raise ApiError("初始密码至少 10 位。")
    if password and (not isinstance(password, str) or len(password) < 10 or len(password) > 256):
        raise ApiError("新密码至少 10 位。")
    result: dict[str, Any] = {
        "full_name": full_name,
        "date_of_birth": birth_date,
        "status": status,
        "username": username,
        "password": password,
        "ssn": _encrypt_ssn(str(body.get("ssn", ""))),
        "clear_ssn": body.get("clearSsn") is True,
    }
    if kind == "student":
        graduation_date = str(body.get("graduationDate", "")).strip()
        if graduation_date:
            try:
                date.fromisoformat(graduation_date)
            except ValueError as exc:
                raise ApiError("请输入有效的预计毕业日期。") from exc
        result["graduation_date"] = graduation_date or None
    else:
        department_id = str(body.get("departmentId", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", department_id):
            raise ApiError("请选择有效院系。")
        result["department_id"] = department_id
    return result


def _record_data(catalog: dict[str, Any]) -> dict[str, Any]:
    connection = get_connection()
    try:
        students = _query_all(
            connection,
            "SELECT s.student_id AS id, s.user_id AS userId, u.username, s.full_name AS fullName, "
            "s.date_of_birth AS dateOfBirth, s.status, s.graduation_date AS graduationDate, "
            "u.is_active AS accountActive, s.created_at AS createdAt "
            "FROM students s JOIN app_users u ON u.user_id = s.user_id ORDER BY s.full_name",
        )
        professors = _query_all(
            connection,
            "SELECT p.professor_id AS id, p.user_id AS userId, u.username, p.full_name AS fullName, "
            "p.date_of_birth AS dateOfBirth, p.status, p.department_id AS departmentId, "
            "u.is_active AS accountActive, p.created_at AS createdAt "
            "FROM professors p JOIN app_users u ON u.user_id = p.user_id ORDER BY p.full_name",
        )
        eligibility = _query_all(
            connection, "SELECT professor_id, course_id FROM professor_course_eligibility ORDER BY course_id",
        )
        by_professor: dict[str, list[str]] = {}
        for row in eligibility:
            by_professor.setdefault(str(row["professor_id"]), []).append(str(row["course_id"]))
        for professor in professors:
            professor["eligibleCourseIds"] = by_professor.get(str(professor["id"]), [])
        periods = _query_all(
            connection,
            "SELECT semester_id AS semesterId, registration_status AS status, "
            "registration_start_at AS registrationStartAt, registration_end_at AS registrationEndAt, "
            "add_drop_start_at AS addDropStartAt, add_drop_end_at AS addDropEndAt, closed_at AS closedAt "
            "FROM registration_periods ORDER BY registration_start_at DESC",
        )
        billing = _query_all(
            connection,
            "SELECT b.transaction_id AS transactionId, b.student_id AS studentId, s.full_name AS studentName, "
            "b.semester_id AS semesterId, b.amount_cents AS amountCents, b.status, "
            "b.attempt_count AS attemptCount, b.last_error AS lastError, b.created_at AS createdAt "
            "FROM billing_transactions b JOIN students s ON s.student_id = b.student_id "
            "ORDER BY b.created_at DESC LIMIT 100",
        )
        billing_counts = _query_all(
            connection, "SELECT status, COUNT(*) AS total FROM billing_transactions GROUP BY status"
        )
    finally:
        connection.close()
    catalog_semesters = {str(item.get("semesterId")): item for item in catalog.get("semesters", [])}
    for period in periods:
        semester = catalog_semesters.get(str(period["semesterId"]), {})
        period["name"] = semester.get("name", period["semesterId"])
    return {
        "students": students,
        "professors": professors,
        "periods": periods,
        "billing": billing,
        "billingCounts": {str(row["status"]): int(row["total"]) for row in billing_counts},
        "catalog": catalog,
        "feePerCreditCents": AMOUNT_CENTS_PER_CREDIT,
        "minimumEnrollment": MINIMUM_OFFERING_ENROLLMENT,
    }


def _course_status_data(semester_id: str, catalog: dict[str, Any]) -> dict[str, Any]:
    semesters = {str(item.get("semesterId")): item for item in catalog.get("semesters", [])}
    semester = semesters.get(semester_id)
    if semester is None:
        raise ApiError("课程目录中找不到这个学期。", 404)
    offerings = [
        item for item in catalog.get("courseOfferings", [])
        if str(item.get("semesterId")) == semester_id
    ]

    connection = get_connection()
    try:
        period = _query_one(
            connection,
            "SELECT registration_status AS status FROM registration_periods WHERE semester_id = %s",
            (semester_id,),
        )
        assignment_rows = _query_all(
            connection,
            "SELECT p.offering_id AS offeringId, p.selection_status AS selectionStatus, "
            "p.professor_id AS professorId, f.full_name AS professorName, f.status AS professorStatus "
            "FROM professor_offering_selections p LEFT JOIN professors f ON f.professor_id = p.professor_id "
            "WHERE p.semester_id = %s",
            (semester_id,),
        )
        enrollment_rows = _query_all(
            connection,
            "SELECT e.offering_id AS offeringId, COUNT(DISTINCT sch.student_id) AS studentCount "
            "FROM enrollments e JOIN student_schedules sch ON sch.schedule_id = e.schedule_id "
            "JOIN students s ON s.student_id = sch.student_id "
            "WHERE sch.semester_id = %s AND s.status = 'ACTIVE' AND e.enrollment_status = 'ENROLLED' "
            "GROUP BY e.offering_id",
            (semester_id,),
        )
        choice_rows = _query_all(
            connection,
            "SELECT c.offering_id AS offeringId, COUNT(DISTINCT sch.student_id) AS studentCount "
            "FROM schedule_choices c JOIN student_schedules sch ON sch.schedule_id = c.schedule_id "
            "JOIN students s ON s.student_id = sch.student_id "
            "LEFT JOIN enrollments e ON e.schedule_id = c.schedule_id AND e.offering_id = c.offering_id "
            "AND e.enrollment_status = 'ENROLLED' "
            "WHERE sch.semester_id = %s AND s.status = 'ACTIVE' AND c.choice_type = 'PRIMARY' "
            "AND c.selection_status = 'SELECTED' AND e.enrollment_id IS NULL GROUP BY c.offering_id",
            (semester_id,),
        )
    finally:
        connection.close()

    assignments = {str(row["offeringId"]): row for row in assignment_rows}
    enrolled = {str(row["offeringId"]): int(row["studentCount"]) for row in enrollment_rows}
    selected = {str(row["offeringId"]): int(row["studentCount"]) for row in choice_rows}
    courses = {str(item.get("courseId")): item for item in catalog.get("courses", [])}
    faculty = {str(item.get("facultyId")): item for item in catalog.get("faculty", [])}
    rows: list[dict[str, Any]] = []
    for offering in offerings:
        offering_id = str(offering.get("offeringId", ""))
        course_id = str(offering.get("courseId", ""))
        assignment = assignments.get(offering_id)
        assignment_status = str(assignment.get("selectionStatus", "")).upper() if assignment else ""
        has_active_teacher = bool(
            assignment and assignment_status in {"SELECTED", "CONFIRMED"}
            and str(assignment.get("professorStatus", "")).upper() == "ACTIVE"
        )
        directory_faculty = faculty.get(str(offering.get("facultyId", "")), {})
        if has_active_teacher:
            teacher_name = assignment.get("professorName") or directory_faculty.get("name") or "已分配教师"
            teacher_source = "本地教师档案"
        elif directory_faculty:
            teacher_name = directory_faculty.get("name", "目录教师")
            teacher_source = "课程目录（未关联有效教师档案）"
        else:
            teacher_name = "未分配"
            teacher_source = ""

        capacity = max(0, int(offering.get("capacity") or 0))
        directory_count = max(0, int(offering.get("enrolledCount") or 0))
        system_count = enrolled.get(offering_id, 0)
        total_count = directory_count + system_count
        directory_status = str(offering.get("status", "未知"))
        if assignment_status == "WITHDRAWN":
            status = "教师已撤回"
        elif assignment and not has_active_teacher:
            status = "教师不可用"
        elif not assignment:
            status = "未关联教师"
        elif capacity and total_count >= capacity:
            status = "已满"
        elif directory_status.upper() not in {"开放", "OPEN", "AVAILABLE"}:
            status = directory_status
        elif not period:
            status = "未配置注册期"
        elif str(period["status"]).upper() == "CLOSED":
            status = "注册已关闭"
        elif str(period["status"]).upper() != "OPEN":
            status = "注册状态未知"
        else:
            status = "开放"
        course = courses.get(course_id, {})
        rows.append({
            "offeringId": offering_id,
            "courseId": course_id,
            "courseTitle": course.get("title", course_id),
            "credits": int(course.get("credits") or 0),
            "section": offering.get("section", ""),
            "semesterId": semester_id,
            "teacherName": teacher_name,
            "teacherSource": teacher_source,
            "professorId": assignment.get("professorId") if assignment else None,
            "assignmentStatus": assignment_status or "UNASSIGNED",
            "directoryStatus": directory_status,
            "status": status,
            "capacity": capacity,
            "studentCount": total_count,
            "directoryStudentCount": directory_count,
            "systemStudentCount": system_count,
            "selectedCount": selected.get(offering_id, 0),
            "days": offering.get("days") or [],
            "startTime": offering.get("startTime", ""),
            "endTime": offering.get("endTime", ""),
            "room": offering.get("room", ""),
        })
    rows.sort(key=lambda row: (row["courseId"], str(row["section"]), row["offeringId"]))
    return {
        "semester": {"semesterId": semester_id, "name": semester.get("name", semester_id)},
        "registrationStatus": str(period["status"]) if period else "UNKNOWN",
        "courses": rows,
    }


def _audit_actor(session: dict[str, Any]) -> str:
    return str(session["user"]["userId"])


def _eligible_courses_from_body(body: dict[str, Any], *, creating: bool) -> list[str] | None:
    if "eligibleCourseIds" not in body:
        return [] if creating else None
    ids = body["eligibleCourseIds"]
    if not isinstance(ids, list) or any(not isinstance(item, str) or not item.strip() or len(item) > 32 for item in ids):
        raise ApiError("授课资格必须是课程编号列表。")
    return sorted(set(ids))


def _replace_eligibility(connection: Any, professor_id: str, course_ids: list[str],
                         session: dict[str, Any], catalog: dict[str, Any]) -> None:
    # 调用端先锁定教师档案，与教师保存授课选择共用该行锁。
    old = sorted(str(row["course_id"]) for row in _query_all(
        connection, "SELECT course_id FROM professor_course_eligibility WHERE professor_id = %s FOR UPDATE",
        (professor_id,),
    ))
    valid_ids = {str(course["courseId"]) for course in catalog["courses"]}
    unknown = sorted(set(course_ids) - valid_ids - set(old))
    if unknown:
        raise ApiError(f"课程目录中找不到授课资格课程：{'、'.join(unknown)}。")
    if old == course_ids:
        return
    cursor = connection.cursor()
    try:
        cursor.execute("DELETE FROM professor_course_eligibility WHERE professor_id = %s", (professor_id,))
        for course_id in course_ids:
            cursor.execute("INSERT INTO professor_course_eligibility (professor_id, course_id) VALUES (%s, %s)",
                           (professor_id, course_id))
    finally:
        cursor.close()
    _insert_audit(connection, _audit_actor(session), "UPDATE_PROFESSOR_ELIGIBILITY", "PROFESSOR",
                  professor_id, {"before": old, "after": course_ids})


def _create_profile(kind: str, body: dict[str, Any], session: dict[str, Any], catalog: dict[str, Any]) -> dict[str, str]:
    values = _validate_profile(body, kind, creating=True)
    eligible_ids = _eligible_courses_from_body(body, creating=True) if kind == "professor" else None
    if kind == "professor" and values["department_id"] not in {
        str(item.get("departmentId")) for item in catalog["departments"]
    }:
        raise ApiError("课程目录中找不到所选院系。")
    with transaction() as connection:
        table = "students" if kind == "student" else "professors"
        column = "student_id" if kind == "student" else "professor_id"
        prefix = "S" if kind == "student" else "P"
        record_id = _create_id(connection, table, column, prefix)
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO app_users (user_id, username, password_hash, role, is_active) "
                "VALUES (%s, %s, %s, %s, %s)",
                (record_id, values["username"], _password_hash(values["password"]),
                 "STUDENT" if kind == "student" else "PROFESSOR",
                 1 if values["status"] == "ACTIVE" else 0),
            )
            if kind == "student":
                cursor.execute(
                    "INSERT INTO students (student_id, user_id, full_name, date_of_birth, ssn_encrypted, status, graduation_date) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (record_id, record_id, values["full_name"], values["date_of_birth"], values["ssn"],
                     values["status"], values["graduation_date"]),
                )
            else:
                cursor.execute(
                    "INSERT INTO professors (professor_id, user_id, full_name, date_of_birth, ssn_encrypted, status, department_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (record_id, record_id, values["full_name"], values["date_of_birth"], values["ssn"],
                     values["status"], values["department_id"]),
                )
        finally:
            cursor.close()
        if eligible_ids is not None:
            _replace_eligibility(connection, record_id, eligible_ids, session, catalog)
        _insert_audit(connection, _audit_actor(session), f"CREATE_{kind.upper()}_PROFILE", kind.upper(),
                      record_id, {"username": values["username"]})
    return {"id": record_id, "username": values["username"]}


def _update_profile(kind: str, record_id: str, body: dict[str, Any], session: dict[str, Any],
                    catalog: dict[str, Any]) -> None:
    values = _validate_profile(body, kind, creating=False)
    eligible_ids = _eligible_courses_from_body(body, creating=False) if kind == "professor" else None
    if kind == "professor" and values["department_id"] not in {
        str(item.get("departmentId")) for item in catalog["departments"]
    }:
        raise ApiError("课程目录中找不到所选院系。")
    table = "students" if kind == "student" else "professors"
    id_column = "student_id" if kind == "student" else "professor_id"
    with transaction() as connection:
        profile = _query_one(connection, f"SELECT user_id FROM {table} WHERE {id_column} = %s FOR UPDATE", (record_id,))
        if profile is None:
            raise ApiError("没有找到该档案。", 404)
        user_id = str(profile["user_id"])
        cursor = connection.cursor()
        try:
            cursor.execute(
                "UPDATE app_users SET username = %s, is_active = %s WHERE user_id = %s",
                (values["username"], 1 if values["status"] == "ACTIVE" else 0, user_id),
            )
            if values["password"]:
                cursor.execute("UPDATE app_users SET password_hash = %s WHERE user_id = %s",
                               (_password_hash(values["password"]), user_id))
            if kind == "student":
                cursor.execute(
                    "UPDATE students SET full_name = %s, date_of_birth = %s, status = %s, graduation_date = %s "
                    "WHERE student_id = %s",
                    (values["full_name"], values["date_of_birth"], values["status"],
                     values["graduation_date"], record_id),
                )
            else:
                cursor.execute(
                    "UPDATE professors SET full_name = %s, date_of_birth = %s, status = %s, department_id = %s "
                    "WHERE professor_id = %s",
                    (values["full_name"], values["date_of_birth"], values["status"],
                     values["department_id"], record_id),
                )
            if values["ssn"] is not None:
                cursor.execute(f"UPDATE {table} SET ssn_encrypted = %s WHERE {id_column} = %s",
                               (values["ssn"], record_id))
            elif values["clear_ssn"]:
                cursor.execute(f"UPDATE {table} SET ssn_encrypted = NULL WHERE {id_column} = %s", (record_id,))
            if kind == "professor" and values["status"] != "ACTIVE":
                cursor.execute(
                    "UPDATE professor_offering_selections SET selection_status = 'WITHDRAWN' "
                    "WHERE professor_id = %s AND selection_status IN ('SELECTED', 'CONFIRMED')",
                    (record_id,),
                )
        finally:
            cursor.close()
        if eligible_ids is not None:
            _replace_eligibility(connection, record_id, eligible_ids, session, catalog)
        _insert_audit(connection, _audit_actor(session), f"UPDATE_{kind.upper()}_PROFILE", kind.upper(),
                      record_id, {"username": values["username"], "status": values["status"]})


def _deactivate_profile(kind: str, record_id: str, session: dict[str, Any]) -> None:
    table = "students" if kind == "student" else "professors"
    id_column = "student_id" if kind == "student" else "professor_id"
    with transaction() as connection:
        profile = _query_one(connection, f"SELECT user_id FROM {table} WHERE {id_column} = %s FOR UPDATE", (record_id,))
        if profile is None:
            raise ApiError("没有找到该档案。", 404)
        cursor = connection.cursor()
        try:
            cursor.execute(f"UPDATE {table} SET status = 'INACTIVE' WHERE {id_column} = %s", (record_id,))
            cursor.execute("UPDATE app_users SET is_active = 0 WHERE user_id = %s", (profile["user_id"],))
            if kind == "professor":
                cursor.execute(
                    "UPDATE professor_offering_selections SET selection_status = 'WITHDRAWN' "
                    "WHERE professor_id = %s AND selection_status IN ('SELECTED', 'CONFIRMED')",
                    (record_id,),
                )
        finally:
            cursor.close()
        _insert_audit(connection, _audit_actor(session), f"DEACTIVATE_{kind.upper()}_PROFILE", kind.upper(),
                      record_id, {"softDelete": True})


def _send_pending_billing() -> None:
    """发送一轮冻结账单；只有接收端确认交易后才标记 SENT。"""
    if not BILLING_API_URL:
        return
    connection = get_connection()
    try:
        pending = _query_all(
            connection,
            "SELECT transaction_id AS transactionId, idempotency_key AS idempotencyKey, "
            "student_id AS studentId, semester_id AS semesterId, amount_cents AS amountCents, "
            "payload_json AS payloadJson FROM billing_transactions "
            "WHERE status IN ('PENDING', 'RETRY') ORDER BY created_at, transaction_id LIMIT 20",
        )
    finally:
        connection.close()
    for item in pending:
        status, error_text = "FAILED", None
        attempted = False
        try:
            raw_payload = item["payloadJson"]
            payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
            validate_bill(payload, str(item["idempotencyKey"]))
            for field in ("transactionId", "studentId", "semesterId", "amountCents"):
                if payload[field] != item[field]:
                    raise ContractError(f"账单 {field} 与队列数据库记录不一致")
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_PAYLOAD_BYTES:
                raise ContractError("账单 JSON 超过接口允许的 65536 字节")
        except (ContractError, ValueError, TypeError) as exc:
            error_text = str(exc)[:1000]
        else:
            request = Request(
                BILLING_API_URL, data=encoded, method="POST",
                headers={
                    "Content-Type": "application/json; charset=utf-8", "Accept": "application/json",
                    "Idempotency-Key": str(item["idempotencyKey"]),
                    "User-Agent": "CourseRegistrarBillingQueue/1.0",
                },
            )
            attempted = True
            status = "RETRY"
            try:
                with urlopen(request, timeout=8) as response:
                    if response.status not in (200, 201):
                        raise ContractError(f"账单服务返回 HTTP {response.status}，尚未确认接受账单")
                    body = response.read(MAX_PAYLOAD_BYTES + 1)
                    if len(body) > MAX_PAYLOAD_BYTES:
                        raise ContractError("账单接收确认超过接口长度限制")
                    validate_receipt(json.loads(body), str(item["transactionId"]), str(item["idempotencyKey"]))
                    status = "SENT"
            except HTTPError as exc:
                status = "RETRY" if exc.code in (408, 425, 429) or exc.code >= 500 else "FAILED"
                error_text = f"账单服务返回 HTTP {exc.code}"[:1000]
                exc.close()
            except Exception as exc:
                error_text = str(exc)[:1000]
        try:
            with transaction() as update_connection:
                cursor = update_connection.cursor()
                try:
                    cursor.execute(
                        "UPDATE billing_transactions SET status = %s, attempt_count = attempt_count + %s, "
                        "last_error = %s WHERE transaction_id = %s AND status IN ('PENDING', 'RETRY')",
                        (status, int(attempted), error_text, item["transactionId"]),
                    )
                finally:
                    cursor.close()
        except Exception as exc:
            # 接收端可能已接受；下一轮仍使用相同键和快照，依赖持久化幂等消除重复账单。
            print(f"账单队列状态更新失败：{exc}")


def _billing_worker() -> None:
    """有配置账单接口时持续扫描队列；无需重新读取目录或计算费率。"""
    if not BILLING_API_URL:
        return
    while True:
        try:
            _send_pending_billing()
        except Exception as exc:
            print(f"账单队列读取失败：{exc}")
        time.sleep(BILLING_RETRY_SECONDS)


def _time_conflicts(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_days = set(first.get("days") or [])
    second_days = set(second.get("days") or [])
    if not first_days.intersection(second_days):
        return False
    return str(first.get("startTime", "")) < str(second.get("endTime", "")) and \
        str(second.get("startTime", "")) < str(first.get("endTime", ""))


def _close_registration(semester_id: str, session: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    semester_offerings = {
        str(item.get("offeringId")): item for item in catalog["courseOfferings"]
        if str(item.get("semesterId")) == semester_id
    }
    courses = {str(item.get("courseId")): item for item in catalog["courses"]}
    external_counts = {
        offering_id: max(0, int(offering.get("enrolledCount") or 0))
        for offering_id, offering in semester_offerings.items()
    }
    prerequisites: dict[str, set[str]] = {}
    for item in catalog["prerequisites"]:
        prerequisites.setdefault(str(item.get("courseId")), set()).add(str(item.get("prerequisiteCourseId")))

    with transaction() as connection:
        period = _query_one(
            connection,
            "SELECT registration_status, registration_start_at, registration_end_at "
            "FROM registration_periods WHERE semester_id = %s FOR UPDATE",
            (semester_id,),
        )
        if period is None:
            raise ApiError("找不到所选注册期。", 404)
        if str(period["registration_status"]).upper() != "OPEN":
            raise ApiError("该注册期已经关闭或尚未开放，没有执行任何更改。", 409)
        start_date = period["registration_start_at"]
        end_date = period["registration_end_at"]
        if isinstance(start_date, datetime):
            start_date = start_date.date()
        if isinstance(end_date, datetime):
            end_date = end_date.date()
        if not isinstance(start_date, date) or not isinstance(end_date, date) or not start_date <= date.today() <= end_date:
            previous_close = _query_one(
                connection,
                "SELECT COUNT(*) AS close_count FROM audit_logs "
                "WHERE action = 'CLOSE_REGISTRATION' AND entity_type = 'SEMESTER' AND entity_id = %s",
                (semester_id,),
            )
            if not previous_close or int(previous_close.get("close_count") or 0) == 0:
                raise ApiError("当前日期不在该学期的注册窗口内，没有执行任何更改。", 409)

            # 已经结算过的注册期被手动重新开放后，只恢复关闭状态，避免重复结算历史课表。
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "UPDATE registration_periods SET registration_status = 'CLOSED', "
                    "closed_at = CURRENT_TIMESTAMP(3) WHERE semester_id = %s",
                    (semester_id,),
                )
            finally:
                cursor.close()
            _insert_audit(
                connection, _audit_actor(session), "CLOSE_REGISTRATION", "SEMESTER", semester_id,
                {"reclosedAfterPreviousClose": True, "settlementRepeated": False,
                 "cancelledOfferings": [], "cancelledSchedules": 0,
                 "billingTransactionsQueued": 0},
            )
            return {
                "cancelledOfferings": [],
                "cancelledSchedules": 0,
                "billingTransactionsQueued": 0,
                "minimumEnrollment": MINIMUM_OFFERING_ENROLLMENT,
                "alreadyFinalized": True,
            }
        assignments_rows = _query_all(
            connection,
            "SELECT p.offering_id AS offeringId, p.selection_status AS status, p.professor_id AS professorId, "
            "f.status AS professorStatus, f.full_name AS professorName FROM professor_offering_selections p "
            "JOIN professors f ON f.professor_id = p.professor_id "
            "WHERE p.semester_id = %s FOR UPDATE",
            (semester_id,),
        )
        assigned = {
            str(row["offeringId"]): row for row in assignments_rows
            if str(row["status"]).upper() in {"SELECTED", "CONFIRMED"}
            and str(row["professorStatus"]).upper() == "ACTIVE"
        }
        schedules = _query_all(
            connection,
            "SELECT sch.schedule_id AS scheduleId, sch.student_id AS studentId, s.full_name AS studentName, sch.status "
            "FROM student_schedules sch JOIN students s ON s.student_id = sch.student_id "
            "WHERE sch.semester_id = %s AND sch.status IN ('DRAFT', 'SUBMITTED', 'FINALIZED') "
            "AND s.status = 'ACTIVE' FOR UPDATE",
            (semester_id,),
        )
        schedule_by_id = {int(row["scheduleId"]): row for row in schedules}
        choice_rows = _query_all(
            connection,
            "SELECT c.schedule_id AS scheduleId, c.offering_id AS offeringId, c.choice_type AS choiceType, "
            "c.preference_order AS preferenceOrder, c.selection_status AS status "
            "FROM schedule_choices c JOIN student_schedules sch ON sch.schedule_id = c.schedule_id "
            "WHERE sch.semester_id = %s AND c.selection_status IN ('SELECTED', 'ENROLLED') "
            "ORDER BY c.schedule_id, c.choice_type, c.preference_order FOR UPDATE",
            (semester_id,),
        )
        enrollment_rows = _query_all(
            connection,
            "SELECT e.enrollment_id AS enrollmentId, e.schedule_id AS scheduleId, e.offering_id AS offeringId, "
            "e.course_id AS courseId, e.course_title_snapshot AS title, e.credits_snapshot AS credits "
            "FROM enrollments e JOIN student_schedules sch ON sch.schedule_id = e.schedule_id "
            "WHERE sch.semester_id = %s AND e.enrollment_status = 'ENROLLED' FOR UPDATE",
            (semester_id,),
        )
        passed_courses = passed_courses_by_student(connection)

        choice_map: dict[int, dict[str, list[str]]] = {}
        for row in choice_rows:
            schedule_id = int(row["scheduleId"])
            choice_map.setdefault(schedule_id, {"PRIMARY": [], "ALTERNATE": []})[
                str(row["choiceType"]).upper()
            ].append(str(row["offeringId"]))

        # 先剔除失效开课，避免它们占据主选名额、人数或阻碍备选补位。
        # 已注册的满员课程仍有效；满员只阻止新的选课，不影响原有注册。
        unavailable_offerings = {
            offering_id for offering_id, offering in semester_offerings.items()
            if offering_id not in assigned
            or str(offering.get("status", "开放")).upper()
            not in {"开放", "OPEN", "AVAILABLE", "已满", "FULL"}
        }
        referenced_offerings = {
            str(row["offeringId"]) for row in enrollment_rows + choice_rows
        }
        unavailable_offerings.update(referenced_offerings - semester_offerings.keys())

        # 为每名学生先保留可用主选，再按偏好顺序填充备选；检查教师、容量、先修课和时间冲突。
        selected: dict[int, list[str]] = {}
        offering_students: dict[str, set[str]] = {offering_id: set() for offering_id in semester_offerings}
        assigned_count = dict(external_counts)
        for row in enrollment_rows:
            schedule_id = int(row["scheduleId"])
            offering_id = str(row["offeringId"])
            if (
                schedule_id in schedule_by_id
                and offering_id in semester_offerings
                and offering_id not in unavailable_offerings
            ):
                selected.setdefault(schedule_id, []).append(offering_id)
                offering_students.setdefault(offering_id, set()).add(str(schedule_by_id[schedule_id]["studentId"]))
                assigned_count[offering_id] = assigned_count.get(offering_id, 0) + 1

        for schedule_id, schedule in schedule_by_id.items():
            if schedule_id in selected:
                selected[schedule_id] = list(dict.fromkeys(selected[schedule_id]))
            else:
                selected[schedule_id] = []
            student_id = str(schedule["studentId"])
            prior_courses = passed_courses.get(student_id, set())
            preferred = choice_map.get(schedule_id, {"PRIMARY": [], "ALTERNATE": []})
            for offering_id in preferred["PRIMARY"] + preferred["ALTERNATE"]:
                if len(selected[schedule_id]) >= MAXIMUM_COURSES_PER_SCHEDULE:
                    break
                offering = semester_offerings.get(offering_id)
                if not offering or offering_id not in assigned or offering_id in selected[schedule_id]:
                    continue
                offering_status = str(offering.get("status", "开放")).upper()
                if offering_status not in {"开放", "OPEN", "AVAILABLE"}:
                    continue
                course_id = str(offering.get("courseId", ""))
                if not prerequisites.get(course_id, set()).issubset(prior_courses):
                    continue
                capacity = int(offering.get("capacity") or 0)
                if capacity and assigned_count.get(offering_id, 0) >= capacity:
                    continue
                if any(_time_conflicts(offering, semester_offerings.get(existing, {}))
                       for existing in selected[schedule_id]):
                    continue
                selected[schedule_id].append(offering_id)
                offering_students.setdefault(offering_id, set()).add(student_id)
                assigned_count[offering_id] = assigned_count.get(offering_id, 0) + 1

        # 外部目录提供的既有名额计入开课人数；目录人数不写入新系统注册记录。
        cancelled_offerings = unavailable_offerings | {
            offering_id for offering_id in semester_offerings
            if len(offering_students.get(offering_id, set())) + external_counts.get(offering_id, 0)
            < MINIMUM_OFFERING_ENROLLMENT
        }
        cursor = connection.cursor(dictionary=True)
        billed_students = 0
        try:
            for schedule_id, schedule in schedule_by_id.items():
                student_id = str(schedule["studentId"])
                # 只从课表中移除被取消的开课，保留其他课程及其注册人数。
                chosen = [
                    offering_id for offering_id in selected.get(schedule_id, [])
                    if offering_id not in cancelled_offerings
                ]
                for offering_id in chosen:
                    offering = semester_offerings[offering_id]
                    course_id = str(offering.get("courseId", ""))
                    course = courses.get(course_id, {})
                    title = str(course.get("title", course_id))
                    credits = max(1, int(course.get("credits") or 1))
                    existing = _query_one(
                        connection,
                        "SELECT enrollment_id FROM enrollments WHERE schedule_id = %s AND offering_id = %s",
                        (schedule_id, offering_id),
                    )
                    if existing:
                        cursor.execute(
                            "UPDATE enrollments SET course_id = %s, course_title_snapshot = %s, "
                            "credits_snapshot = %s, enrollment_status = 'ENROLLED' WHERE enrollment_id = %s",
                            (course_id, title, credits, existing["enrollment_id"]),
                        )
                    else:
                        cursor.execute(
                            "INSERT INTO enrollments (schedule_id, offering_id, course_id, course_title_snapshot, "
                            "credits_snapshot, enrollment_status) VALUES (%s, %s, %s, %s, %s, 'ENROLLED')",
                            (schedule_id, offering_id, course_id, title, credits),
                        )
                cursor.execute(
                    "UPDATE schedule_choices SET selection_status = CASE "
                    "WHEN offering_id IN (" + ",".join(["%s"] * len(chosen)) + ") THEN 'ENROLLED' "
                    "ELSE 'CANCELLED' END WHERE schedule_id = %s",
                    (*chosen, schedule_id),
                ) if chosen else cursor.execute(
                    "UPDATE schedule_choices SET selection_status = 'CANCELLED' WHERE schedule_id = %s",
                    (schedule_id,),
                )
                cursor.execute("UPDATE student_schedules SET status = 'FINALIZED' WHERE schedule_id = %s",
                               (schedule_id,))
                if chosen:
                    # 在关闭注册的同一事务中冻结金额及最终课表；重试不得重新计算。
                    departments = {str(item["departmentId"]): item.get("name")
                                   for item in catalog.get("departments", [])}
                    semester = next((item for item in catalog.get("semesters", [])
                                     if str(item.get("semesterId")) == semester_id), {})
                    entries = []
                    for offering_id in chosen:
                        offering = semester_offerings[offering_id]
                        course_id = str(offering.get("courseId", ""))
                        course = courses.get(course_id, {})
                        credits = max(1, int(course.get("credits") or 1))
                        teacher = assigned[offering_id]
                        entries.append({
                            "offeringId": offering_id, "courseId": course_id,
                            "courseTitle": str(course.get("title", course_id)),
                            "credits": credits, "amountCents": credits * AMOUNT_CENTS_PER_CREDIT,
                            "section": offering.get("section"), "departmentId": course.get("departmentId"),
                            "departmentName": departments.get(str(course.get("departmentId"))),
                            "professorId": str(teacher["professorId"]), "professorName": str(teacher["professorName"]),
                            "days": list(offering.get("days") or []), "startTime": offering.get("startTime"),
                            "endTime": offering.get("endTime"), "room": offering.get("room"),
                        })
                    amount_cents = sum(item["amountCents"] for item in entries)
                    idempotency_key = f"REGCLOSE-{semester_id}-{student_id}"
                    transaction_id = "BILL-" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32].upper()
                    payload = {
                        "schemaVersion": SCHEMA_VERSION, "eventType": "REGISTRATION_FINALIZED",
                        "transactionId": transaction_id, "idempotencyKey": idempotency_key,
                        "studentId": student_id, "studentName": str(schedule["studentName"]),
                        "semesterId": semester_id, "semesterName": str(semester.get("name") or semester_id),
                        "scheduleId": schedule_id, "scheduleStatus": "FINALIZED",
                        "currency": CURRENCY, "amountCents": amount_cents,
                        "totalCredits": sum(item["credits"] for item in entries),
                        "unitAmountCentsPerCredit": AMOUNT_CENTS_PER_CREDIT, "enrollments": entries,
                    }
                    validate_bill(payload, idempotency_key)
                    cursor.execute(
                        "INSERT INTO billing_transactions (transaction_id, student_id, semester_id, idempotency_key, "
                        "amount_cents, status, payload_json) VALUES (%s, %s, %s, %s, %s, 'PENDING', %s) "
                        "ON DUPLICATE KEY UPDATE idempotency_key = VALUES(idempotency_key)",
                        (transaction_id, student_id, semester_id, idempotency_key, amount_cents,
                         json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
                    )
                    billed_students += 1
            if cancelled_offerings:
                cancelled_ids = sorted(cancelled_offerings)
                cursor.execute(
                    "UPDATE enrollments SET enrollment_status = 'CANCELLED' "
                    "WHERE offering_id IN (" + ",".join(["%s"] * len(cancelled_ids)) + ") "
                    "AND enrollment_status = 'ENROLLED' "
                    "AND schedule_id IN (SELECT schedule_id FROM student_schedules WHERE semester_id = %s)",
                    (*cancelled_ids, semester_id),
                )
                cursor.execute(
                    "UPDATE schedule_choices SET selection_status = 'CANCELLED' "
                    "WHERE offering_id IN (" + ",".join(["%s"] * len(cancelled_ids)) + ") "
                    "AND selection_status IN ('SELECTED', 'ENROLLED') "
                    "AND schedule_id IN (SELECT schedule_id FROM student_schedules WHERE semester_id = %s)",
                    (*cancelled_ids, semester_id),
                )
            cursor.execute(
                "UPDATE registration_periods SET registration_status = 'CLOSED', closed_at = CURRENT_TIMESTAMP(3) "
                "WHERE semester_id = %s",
                (semester_id,),
            )
            if cancelled_offerings:
                cancelled_ids = sorted(cancelled_offerings)
                cursor.execute(
                    "UPDATE professor_offering_selections SET selection_status = 'WITHDRAWN' "
                    "WHERE semester_id = %s AND offering_id IN (" + ",".join(["%s"] * len(cancelled_ids)) + ") "
                    "AND selection_status IN ('SELECTED', 'CONFIRMED')",
                    (semester_id, *cancelled_ids),
                )
        finally:
            cursor.close()
        _insert_audit(
            connection, _audit_actor(session), "CLOSE_REGISTRATION", "SEMESTER", semester_id,
            {"cancelledOfferings": sorted(cancelled_offerings),
             "cancelledSchedules": 0,
             "billingTransactionsQueued": billed_students,
             "minimumEnrollment": MINIMUM_OFFERING_ENROLLMENT},
        )
    return {
        "cancelledOfferings": sorted(cancelled_offerings),
        # 保留现有接口字段；关闭注册只取消开课，不取消整张课表。
        "cancelledSchedules": 0,
        "billingTransactionsQueued": billed_students,
        "minimumEnrollment": MINIMUM_OFFERING_ENROLLMENT,
        "alreadyFinalized": False,
    }


def _reopen_registration(semester_id: str, session: dict[str, Any]) -> dict[str, Any]:
    """重新开放注册状态；不回滚关闭时已完成的课表结算、课程取消或账单。"""
    with transaction() as connection:
        period = _query_one(
            connection,
            "SELECT registration_status, closed_at FROM registration_periods "
            "WHERE semester_id = %s FOR UPDATE",
            (semester_id,),
        )
        if period is None:
            raise ApiError("找不到所选注册期。", 404)
        if str(period["registration_status"]).upper() != "CLOSED":
            raise ApiError("该注册期当前并未关闭，无需重新开放。", 409)

        cursor = connection.cursor()
        try:
            cursor.execute(
                "UPDATE registration_periods SET registration_status = 'OPEN', closed_at = NULL "
                "WHERE semester_id = %s",
                (semester_id,),
            )
        finally:
            cursor.close()
        _insert_audit(
            connection, _audit_actor(session), "REOPEN_REGISTRATION", "SEMESTER", semester_id,
            {"previousStatus": "CLOSED", "status": "OPEN",
             "previousClosedAt": str(period["closed_at"]) if period["closed_at"] else None,
             "effectsReversed": False},
        )
    return {"semesterId": semester_id, "status": "OPEN"}


class RegistrarHandler(BaseHTTPRequestHandler):
    """提供静态网页资源与教务员专属 API。"""

    server_version = "CourseRegistrarPortal/1.0"

    def _send_bytes(self, status: int, body: bytes, content_type: str,
                    extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self' data:")
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, Any],
                   extra_headers: dict[str, str] | None = None) -> None:
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
            raise ApiError("请求内容不是有效 JSON。") from exc
        if not isinstance(data, dict):
            raise ApiError("请求内容格式不正确。")
        return data

    def _cookie_session(self) -> tuple[str | None, dict[str, Any] | None]:
        return checked_cookie_session(
            self.headers, SESSION_COOKIE, _sessions, _session_lock, get_connection, "REGISTRAR",
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
        path = urlparse(self.path).path
        static = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/course-status": ("course_status.html", "text/html; charset=utf-8"),
                  "/course-status.css": ("course_status.css", "text/css; charset=utf-8"),
                  "/course-status.js": ("course_status.js", "text/javascript; charset=utf-8")}
        if path in static:
            self._serve_file(*static[path])
            return
        try:
            if path == "/api/session":
                _, session = self._cookie_session()
                if session:
                    self._send_json(200, {"authenticated": True, "user": session["user"],
                                          "csrfToken": session["csrf_token"]})
                else:
                    self._send_json(200, {"authenticated": False})
                return
            if path == "/api/admin/data":
                self._require_session()
                self._send_json(200, _record_data(_fetch_catalog()))
                return
            if path == "/api/admin/course-status":
                self._require_session()
                semester_id = parse_qs(urlparse(self.path).query).get("semester", [""])[0].strip()
                if not semester_id:
                    raise ApiError("请提供学期编号。")
                self._send_json(200, _course_status_data(semester_id, _fetch_catalog()))
                return
        except ApiError as exc:
            self._send_json(exc.status, {"error": str(exc)})
            return
        except Exception as exc:
            print(f"[{self.log_date_time_string()}] GET {path} failed: {exc}")
            self._send_json(503, {"error": "服务暂时无法完成请求，请检查服务终端中的错误信息。"})
            return
        self._send_json(404, {"error": "找不到请求的页面或接口。"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._json_body()
            if path == "/api/login":
                username = str(body.get("username", "")).strip()
                password = body.get("password", "")
                if (not username or len(username) > 64 or not isinstance(password, str)
                        or not password or len(password) > 256):
                    raise ApiError("请输入用户名和密码。", 401)
                connection = get_connection()
                try:
                    user = _query_one(
                        connection,
                        "SELECT u.user_id AS userId, u.username, r.registrar_id AS registrarId, "
                        "r.full_name AS fullName, u.password_hash AS passwordHash "
                        "FROM app_users u JOIN registrars r ON r.user_id = u.user_id "
                        "WHERE u.username = %s AND u.role = 'REGISTRAR' AND u.is_active = 1",
                        (username,),
                    )
                finally:
                    connection.close()
                if not user or not _password_matches(password, str(user["passwordHash"])):
                    raise ApiError("用户名或密码不正确，或当前账号没有教务员权限。", 401)
                token = secrets.token_urlsafe(32)
                csrf = secrets.token_urlsafe(24)
                safe_user = {"userId": user["userId"], "registrarId": user["registrarId"],
                             "username": user["username"], "fullName": user["fullName"]}
                with _session_lock:
                    _sessions[token] = {"user": safe_user, "csrf_token": csrf,
                                        "password_fingerprint": password_fingerprint(str(user["passwordHash"])),
                                        "expires_at": time.time() + SESSION_LIFETIME_SECONDS}
                self._send_json(200, {"ok": True, "user": safe_user},
                                {"Set-Cookie": f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_LIFETIME_SECONDS}"})
                return

            token, session = self._require_session()
            self._require_csrf(session)
            if path == "/api/logout":
                with _session_lock:
                    _sessions.pop(token, None)
                self._send_json(200, {"ok": True},
                                {"Set-Cookie": f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
                return
            if path in {"/api/admin/students", "/api/admin/professors"}:
                kind = "student" if path.endswith("students") else "professor"
                result = _create_profile(kind, body, session, _fetch_catalog())
                self._send_json(201, {"ok": True, "record": result})
                return
            if path == "/api/admin/registration/close":
                semester_id = str(body.get("semesterId", "")).strip()
                if not semester_id or body.get("confirm") is not True:
                    raise ApiError("请确认学期并完成关闭操作。")
                result = _close_registration(semester_id, session, _fetch_catalog())
                self._send_json(200, {"ok": True, "result": result})
                return
            if path == "/api/admin/registration/reopen":
                semester_id = str(body.get("semesterId", "")).strip()
                if not semester_id or body.get("confirm") is not True:
                    raise ApiError("请确认学期并完成重新开放操作。")
                result = _reopen_registration(semester_id, session)
                self._send_json(200, {"ok": True, "result": result})
                return
        except ApiError as exc:
            self._send_json(exc.status, {"error": str(exc)})
            return
        except Exception as exc:
            print(f"[{self.log_date_time_string()}] POST {path} failed: {exc}")
            self._send_json(503, {"error": "服务暂时无法完成请求，请检查服务终端中的错误信息。"})
            return
        self._send_json(404, {"error": "找不到请求的接口。"})

    def _record_route(self, method: str) -> None:
        path = urlparse(self.path).path.rstrip("/")
        match = re.fullmatch(r"/api/admin/(students|professors)/([A-Za-z0-9_-]{1,32})", path)
        if not match:
            self._send_json(404, {"error": "找不到请求的接口。"})
            return
        try:
            _, session = self._require_session()
            self._require_csrf(session)
            kind = "student" if match.group(1) == "students" else "professor"
            record_id = match.group(2)
            if method == "DELETE":
                _deactivate_profile(kind, record_id, session)
                self._send_json(200, {"ok": True, "message": "档案已停用，历史数据仍保留。"})
                return
            body = self._json_body()
            _update_profile(kind, record_id, body, session, _fetch_catalog())
            self._send_json(200, {"ok": True, "message": "档案已保存。"})
        except ApiError as exc:
            self._send_json(exc.status, {"error": str(exc)})
        except Exception as exc:
            print(f"[{self.log_date_time_string()}] {method} {path} failed: {exc}")
            self._send_json(503, {"error": "服务暂时无法完成请求，请检查服务终端中的错误信息。"})

    def do_PUT(self) -> None:  # noqa: N802
        self._record_route("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._record_route("DELETE")

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")


def main() -> None:
    if not 1 <= PORT <= 65535:
        raise SystemExit("ADMINISTER_PORT 必须在 1 到 65535 之间。")
    if AMOUNT_CENTS_PER_CREDIT < 0:
        raise SystemExit("BILLING_AMOUNT_CENTS_PER_CREDIT 不能小于 0。")
    server = ThreadingHTTPServer((HOST, PORT), RegistrarHandler)
    if BILLING_API_URL:
        threading.Thread(target=_billing_worker, name="billing-outbox-worker", daemon=True).start()
        print(f"账单发送目标已配置：{BILLING_API_URL}；发送状态以接收端确认为准。")
    print(f"注册管理员网页已启动：http://{HOST}:{PORT}")
    print(f"课程目录接口：{CATALOG_API_URL}/api/v1/catalog")
    print("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n注册管理员网页服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
