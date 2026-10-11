"""各门户共用的会话账号复查；不缓存数据库中的权限状态。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from http.cookies import SimpleCookie
from typing import Any


def password_matches(password: str, encoded: str) -> bool:
    """验证项目使用的 PBKDF2-SHA256 密码哈希。"""
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


def password_fingerprint(encoded_hash: str) -> str:
    """会话只保存密码哈希的指纹，密码重置（含重新设置相同密码）使指纹改变。"""
    return hashlib.sha256(encoded_hash.encode("utf-8")).hexdigest()


def account_matches_session(connection: Any, session: dict[str, Any], role: str) -> bool:
    profiles = {
        "STUDENT": ("students", "student_id", "studentId"),
        "PROFESSOR": ("professors", "professor_id", "professorId"),
        "REGISTRAR": ("registrars", "registrar_id", "registrarId"),
    }
    table, id_column, identity_key = profiles[role]
    user = session.get("user", {})
    fingerprint = session.get("password_fingerprint")
    if not user.get("userId") or not user.get(identity_key) or not fingerprint:
        return False
    cursor = connection.cursor(dictionary=True)
    try:
        profile_status = "p.status" if role != "REGISTRAR" else "'ACTIVE'"
        cursor.execute(
            f"SELECT u.role, u.is_active, u.password_hash, p.{id_column} AS profile_id, "
            f"{profile_status} AS profile_status FROM app_users u "
            f"JOIN {table} p ON p.user_id = u.user_id WHERE u.user_id = %s",
            (str(user["userId"]),),
        )
        current = cursor.fetchone()
    finally:
        cursor.close()
    return bool(
        current and current["role"] == role and current["is_active"]
        and current["profile_status"] == "ACTIVE"
        and str(current["profile_id"]) == str(user[identity_key])
        and hmac.compare_digest(str(fingerprint), password_fingerprint(str(current["password_hash"])))
    )


def checked_cookie_session(headers, cookie_name, sessions, lock, connection_factory, role):
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("Cookie", ""))
    except Exception:
        return None, None
    morsel = cookie.get(cookie_name)
    token = morsel.value if morsel else None
    if not token:
        return None, None
    with lock:
        session = sessions.get(token)
        if session and session["expires_at"] <= time.time():
            sessions.pop(token, None)
            session = None
    if session is None:
        return token, None
    # 数据库不可用时让调用端返回 503；绝不使用内存会话放行。
    connection = connection_factory()
    try:
        valid = account_matches_session(connection, session, role)
    finally:
        connection.close()
    with lock:
        if sessions.get(token) is not session:
            return token, None
        if not valid or session["expires_at"] <= time.time():
            sessions.pop(token, None)
            return token, None
    return token, session
