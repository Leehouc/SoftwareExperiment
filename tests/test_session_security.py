"""通过真实 HTTP 登录和读写请求验证会话撤销及数据库检查页权限。"""

import base64
import hashlib
import importlib.util
import json
import sqlite3
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from administer import server as admin
from student import server as student
from teacher import server as teacher
from tests.test_student_registration_closed import StudentConnection, StudentScheduleTestCase


spec = importlib.util.spec_from_file_location("database_check_server", Path(__file__).resolve().parents[1] /
                                             "newdatabaseTest1.0" / "server.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def encoded_password(password, salt=b"test-salt"):
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 10_000)
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
    return f"pbkdf2_sha256$10000${encode(salt)}${encode(digest)}"


class SessionSecurityTests(StudentScheduleTestCase):
    def setUp(self):
        super().setUp()
        # HTTP 服务在线程中执行，仍使用与数据库回归相同的真实 SQL 适配器。
        db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.backup(db)
        db.row_factory = sqlite3.Row
        self.db = db
        self.connection = StudentConnection(db)
        self.addCleanup(db.close)
        self.password = "OriginalPassword1!"
        self.db.execute("UPDATE app_users SET password_hash = ?", (encoded_password(self.password),))
        self.db.execute("INSERT INTO app_users (user_id, username, password_hash, role) "
                        "VALUES ('R', 'registrar', ?, 'REGISTRAR')", (encoded_password(self.password),))
        self.db.execute("INSERT INTO registrars (registrar_id, user_id, full_name) VALUES ('R', 'R', 'Registrar')")
        self.db.commit()
        mock = patch.object(admin, "transaction", student.transaction)
        mock.start()
        self.addCleanup(mock.stop)
        self.services = {"student": student, "teacher": teacher, "admin": admin, "checker": checker}
        self.urls = {}
        handlers = [student.StudentHandler, teacher.TeacherHandler, admin.RegistrarHandler, checker.TestPageHandler]
        for (name, module), handler in zip(self.services.items(), handlers):
            module._sessions.clear()
            self.addCleanup(module._sessions.clear)
            mock = patch.object(module, "get_connection", lambda: self.connection)
            mock.start()
            self.addCleanup(mock.stop)
            quiet = type(f"Quiet{handler.__name__}", (handler,), {"log_message": lambda *args: None})
            http = ThreadingHTTPServer(("127.0.0.1", 0), quiet)
            thread = threading.Thread(target=lambda server=http: server.serve_forever(poll_interval=.01), daemon=True)
            thread.start()
            self.urls[name] = f"http://127.0.0.1:{http.server_port}"
            self.addCleanup(http.server_close)
            self.addCleanup(thread.join, 2)
            self.addCleanup(http.shutdown)
        status = patch.object(checker, "fetch_status", return_value={"ok": True, "tables": []})
        self.status_mock = status.start()
        self.addCleanup(status.stop)

    def http(self, service, path, body=None, cookie=None, csrf=None, method=None):
        headers = {}
        if cookie:
            headers["Cookie"] = cookie
        if csrf is not None:
            headers["X-CSRF-Token"] = csrf
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = Request(self.urls[service] + path, data=payload, headers=headers, method=method)
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read()), response.headers

    def login(self, service, username=None, password=None):
        username = username or {"student": "student", "teacher": "professor", "admin": "registrar", "checker": "registrar"}[service]
        status, payload, headers = self.http(service, "/api/login", {"username": username, "password": password or self.password})
        self.assertEqual(status, 200, payload)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        status, session, _ = self.http(service, "/api/session", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(session["authenticated"])
        self.assertNotIn("password", json.dumps(payload).lower())
        self.assertNotIn("password", json.dumps(session).lower())
        return cookie, session["csrfToken"]

    def test_admin_deactivation_invalidates_student_read_and_write_before_business_logic(self):
        cookie, csrf = self.login("student")
        admin._deactivate_profile("student", "S", {"user": {"userId": "R"}})
        before = list(self.db.iterdump())
        for path, body in (("/api/student/transcript", None), ("/api/student/data?semester=TERM", None),
                           ("/api/student/schedule/save", {"semesterId": "TERM", "primary": ["O1"], "alternate": []}),
                           ("/api/student/schedule/submit", {"semesterId": "TERM", "primary": ["O1"], "alternate": []}),
                           ("/api/student/schedule/delete", {"semesterId": "TERM", "confirm": True})):
            with self.subTest(path=path):
                status, _, _ = self.http("student", path, body, cookie, csrf)
                self.assertEqual(status, 401)
        self.assertEqual(list(self.db.iterdump()), before)
        self.assertEqual(student._sessions, {})
        self.assertFalse(self.http("student", "/api/session", cookie=cookie)[1]["authenticated"])

    def test_real_admin_password_reset_revokes_all_old_sessions_and_allows_new_password(self):
        old = [self.login("student")[0] for _ in range(2)]
        new_password = "ResetPassword2!"
        admin._update_profile("student", "S", {
            "fullName": "Student", "dateOfBirth": "2000-01-01", "username": "student", "password": new_password,
        }, {"user": {"userId": "R"}}, self.catalog)
        for cookie in old:
            self.assertEqual(self.http("student", "/api/student/transcript", cookie=cookie)[0], 401)
        self.assertEqual(self.http("student", "/api/login", {"username": "student", "password": self.password})[0], 401)
        cookie, _ = self.login("student", password=new_password)
        self.assertEqual(self.http("student", "/api/student/transcript", cookie=cookie)[0], 200)

    def test_unchanged_accounts_remain_logged_in_but_expired_and_unknown_tokens_fail(self):
        for name, module in self.services.items():
            with self.subTest(service=name):
                cookie, _ = self.login(name)
                token = cookie.split("=", 1)[1]
                module._sessions[token]["expires_at"] = time.time() - 1
                self.assertFalse(self.http(name, "/api/session", cookie=cookie)[1]["authenticated"])
                self.assertNotIn(token, module._sessions)
                self.assertFalse(self.http(name, "/api/session", cookie=f"{module.SESSION_COOKIE}=unknown")[1]["authenticated"])

    def test_profile_status_change_and_graduation_invalidate_student_without_account_flag_change(self):
        for status in ("INACTIVE", "GRADUATED"):
            self.db.execute("UPDATE students SET status = 'ACTIVE'")
            cookie, _ = self.login("student")
            self.db.execute("UPDATE students SET status = ?", (status,))
            self.assertEqual(self.http("student", "/api/student/transcript", cookie=cookie)[0], 401)
            self.assertEqual(self.db.execute("SELECT is_active FROM app_users WHERE user_id = 'S'").fetchone()[0], 1)

    def test_teacher_and_both_registrar_services_recheck_disable_role_and_password(self):
        for name, user_id, role in (("teacher", "P", "PROFESSOR"), ("admin", "R", "REGISTRAR"), ("checker", "R", "REGISTRAR")):
            for change in ("disable", "role", "password"):
                with self.subTest(service=name, change=change):
                    self.db.execute("UPDATE app_users SET role = ?, is_active = 1, password_hash = ? WHERE user_id = ?",
                                    (role, encoded_password(self.password), user_id))
                    cookie, _ = self.login(name)
                    if change == "disable":
                        self.db.execute("UPDATE app_users SET is_active = 0 WHERE user_id = ?", (user_id,))
                    elif change == "role":
                        self.db.execute("UPDATE app_users SET role = 'STUDENT' WHERE user_id = ?", (user_id,))
                    else:
                        # 即使重置为相同的明文密码，盐变化也会撤销会话。
                        self.db.execute("UPDATE app_users SET password_hash = ? WHERE user_id = ?",
                                        (encoded_password(self.password, b"new-salt"), user_id))
                    self.assertFalse(self.http(name, "/api/session", cookie=cookie)[1]["authenticated"])

    def test_missing_and_reassigned_profiles_and_inactive_teacher_are_rejected(self):
        cookie, _ = self.login("teacher")
        self.db.execute("UPDATE professors SET status = 'INACTIVE'")
        self.assertFalse(self.http("teacher", "/api/session", cookie=cookie)[1]["authenticated"])
        cookie, _ = self.login("checker")
        self.db.execute("UPDATE registrars SET registrar_id = 'NEW'")
        self.assertEqual(self.http("checker", "/api/status", cookie=cookie)[0], 401)
        self.db.execute("UPDATE registrars SET registrar_id = 'R'")
        cookie, _ = self.login("checker")
        self.db.execute("DELETE FROM registrars")
        self.assertEqual(self.http("checker", "/api/status", cookie=cookie)[0], 401)

    def test_database_outage_fails_closed_and_does_not_destroy_valid_session(self):
        for name, module in self.services.items():
            cookie, _ = self.login(name)
            with patch.object(module, "get_connection", side_effect=RuntimeError("offline")):
                self.assertEqual(self.http(name, "/api/session", cookie=cookie)[0], 503)
            self.assertTrue(self.http(name, "/api/session", cookie=cookie)[1]["authenticated"])

    def test_database_previews_require_independent_registrar_login(self):
        for path in ("/api/status", "/api/preview?table=course_grades", "/api/preview?table=students"):
            self.assertEqual(self.http("checker", path)[0], 401)
        self.status_mock.assert_not_called()
        for username in ("student", "professor"):
            self.assertEqual(self.http("checker", "/api/login", {"username": username, "password": self.password})[0], 401)
        for service in ("student", "admin"):
            cookie, _ = self.login(service)
            self.assertEqual(self.http("checker", "/api/preview?table=course_grades", cookie=cookie)[0], 401)
        cookie, _ = self.login("checker")
        self.assertEqual(self.http("checker", "/api/status", cookie=cookie)[0], 200)
        status, data, _ = self.http("checker", "/api/preview?table=app_users", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(len(data["rows"]), 3)
        self.assertNotIn("password_hash", data["columns"])
        self.assertNotIn("password_hash", json.dumps(data))
        self.assertEqual(self.http("checker", "/api/preview?table=not_allowed", cookie=cookie)[0], 400)
        self.assertEqual(self.http("checker", "/api/preview", {}, cookie)[0], 405)

    def test_database_logout_checks_csrf_and_revokes_cookie(self):
        cookie, csrf = self.login("checker")
        self.assertEqual(self.http("checker", "/api/logout", {}, cookie)[0], 403)
        self.assertEqual(self.http("checker", "/api/status", cookie=cookie)[0], 200)
        status, _, headers = self.http("checker", "/api/logout", {}, cookie, csrf)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(self.http("checker", "/api/preview?table=course_grades", cookie=cookie)[0], 401)

    def test_database_login_rejects_bad_payload_and_credentials(self):
        for body, expected in (([], 400), ({"username": "registrar", "password": "wrong"}, 401),
                               ({"username": "registrar", "password": 123}, 401),
                               ({"username": "x" * 65, "password": self.password}, 401),
                               ({"username": "registrar", "password": "x" * 5000}, 400)):
            with self.subTest(body_type=type(body).__name__):
                self.assertEqual(self.http("checker", "/api/login", body)[0], expected)
