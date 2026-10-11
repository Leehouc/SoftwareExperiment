"""本地只读 MySQL 数据库检查网页服务。"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from database.mysql_connection import get_connection  # noqa: E402


WEB_ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
DEFAULT_PORT = 8090
PREVIEW_LIMIT = 20

# 只允许读取这里列出的表和字段，密码哈希、加密身份信息等字段不会展示。
TABLES: dict[str, dict[str, object]] = {
    "app_users": {
        "label": "系统用户",
        "columns": ["user_id", "username", "role", "is_active", "created_at"],
    },
    "students": {
        "label": "学生",
        "columns": ["student_id", "full_name", "date_of_birth", "status", "created_at"],
    },
    "professors": {
        "label": "教师",
        "columns": ["professor_id", "full_name", "department_id", "status", "created_at"],
    },
    "registrars": {
        "label": "教务员",
        "columns": ["registrar_id", "full_name", "created_at"],
    },
    "registration_periods": {
        "label": "注册时间段",
        "columns": ["semester_id", "registration_status", "registration_start_at", "registration_end_at"],
    },
    "student_schedules": {
        "label": "学生课表",
        "columns": ["schedule_id", "student_id", "semester_id", "status", "created_at"],
    },
    "schedule_choices": {
        "label": "选课志愿",
        "columns": ["choice_id", "schedule_id", "offering_id", "choice_type", "preference_order", "selection_status"],
    },
    "professor_course_eligibility": {
        "label": "教师授课资格",
        "columns": ["professor_id", "course_id"],
    },
    "professor_offering_selections": {
        "label": "教师开课选择",
        "columns": ["professor_id", "offering_id", "semester_id", "selection_status", "selected_at"],
    },
    "enrollments": {
        "label": "选课记录",
        "columns": ["enrollment_id", "schedule_id", "offering_id", "course_id", "course_title_snapshot", "credits_snapshot", "enrollment_status", "enrolled_at"],
    },
    "course_grades": {
        "label": "课程成绩",
        "columns": ["enrollment_id", "professor_id", "letter_grade", "graded_at"],
    },
    "billing_transactions": {
        "label": "缴费事务",
        "columns": ["transaction_id", "student_id", "semester_id", "amount_cents", "status", "attempt_count", "created_at"],
    },
    "audit_logs": {
        "label": "审计日志",
        "columns": ["audit_id", "actor_user_id", "action", "entity_type", "entity_id", "created_at"],
    },
}


def fetch_status() -> dict[str, object]:
    """读取数据库连接信息和所有业务表的记录数。"""
    connection = get_connection()
    cursor = None
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT DATABASE() AS database_name, VERSION() AS server_version, "
            "CURRENT_USER() AS mysql_account"
        )
        info = cursor.fetchone() or {}

        counts = []
        for name, definition in TABLES.items():
            cursor.execute(f"SELECT COUNT(*) AS total FROM `{name}`")
            row = cursor.fetchone() or {}
            counts.append(
                {
                    "name": name,
                    "label": definition["label"],
                    "count": int(row.get("total", 0)),
                }
            )
        return {"ok": True, **info, "tables": counts}
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


def fetch_preview(table_name: str) -> dict[str, object]:
    """读取白名单表的少量非敏感字段。"""
    definition = TABLES.get(table_name)
    if definition is None:
        raise KeyError("不支持查看这张表。")

    columns = definition["columns"]
    column_sql = ", ".join(f"`{column}`" for column in columns)
    connection = get_connection()
    cursor = None
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            f"SELECT {column_sql} FROM `{table_name}` ORDER BY 1 LIMIT %s",
            (PREVIEW_LIMIT,),
        )
        rows = cursor.fetchall()
        return {
            "ok": True,
            "name": table_name,
            "label": definition["label"],
            "columns": columns,
            "rows": rows,
            "limit": PREVIEW_LIMIT,
        }
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


class TestPageHandler(BaseHTTPRequestHandler):
    """处理网页静态资源与只读 JSON 接口。"""

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def do_GET(self) -> None:  # noqa: N802
        request = urlparse(self.path)

        if request.path == "/":
            self._serve_file("index.html", "text/html; charset=utf-8")
            return
        if request.path == "/style.css":
            self._serve_file("style.css", "text/css; charset=utf-8")
            return
        if request.path == "/app.js":
            self._serve_file("app.js", "text/javascript; charset=utf-8")
            return
        if request.path == "/api/status":
            self._handle_status()
            return
        if request.path == "/api/preview":
            table_name = parse_qs(request.query).get("table", [""])[0]
            self._handle_preview(table_name)
            return

        self._send_json({"ok": False, "error": "找不到请求的页面或接口。"}, 404)

    def _serve_file(self, filename: str, content_type: str) -> None:
        try:
            body = (WEB_ROOT / filename).read_bytes()
        except OSError:
            self._send_json({"ok": False, "error": "网页文件读取失败。"}, 500)
            return
        self._send_bytes(body, content_type)

    def _handle_status(self) -> None:
        try:
            self._send_json(fetch_status())
        except Exception as exc:
            self._send_json(
                {"ok": False, "error": str(exc) or "无法连接数据库，请检查连接配置和 MySQL 服务。"},
                503,
            )

    def _handle_preview(self, table_name: str) -> None:
        try:
            self._send_json(fetch_preview(table_name))
        except KeyError as exc:
            self._send_json({"ok": False, "error": str(exc).strip("'")}, 400)
        except Exception as exc:
            self._send_json(
                {"ok": False, "error": str(exc) or "读取数据失败，请检查数据库连接。"},
                503,
            )

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")


def main() -> None:
    try:
        port = int(os.environ.get("TEST_PAGE_PORT", str(DEFAULT_PORT)))
    except ValueError as exc:
        raise SystemExit("TEST_PAGE_PORT 必须是有效的端口号。") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("TEST_PAGE_PORT 必须在 1 到 65535 之间。")

    server = ThreadingHTTPServer((HOST, port), TestPageHandler)
    print(f"数据库测试网页已启动：http://{HOST}:{port}")
    print("此网页仅监听本机，并且只读取数据库数据。按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n测试网页已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
