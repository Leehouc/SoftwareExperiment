"""关闭注册后，学生课表写入及页面权限的持久化回归测试。"""

from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from student import server
from tests.test_close_registration import SqliteConnection, SqliteCursor


class StudentCursor(SqliteCursor):
    def __init__(self, connection, queries):
        super().__init__(connection)
        self.queries = queries

    def execute(self, sql, params=()):
        self.queries.append(sql)
        super().execute(sql, params)

    def executemany(self, sql, rows):
        for row in rows:
            self.execute(sql, row)

    @property
    def lastrowid(self):
        return self.cursor.lastrowid


class StudentConnection(SqliteConnection):
    def __init__(self, connection):
        super().__init__(connection)
        self.queries = []

    def cursor(self, dictionary=False):
        return StudentCursor(self.connection, self.queries)

    def close(self):
        # 读取接口归还连接时保留测试拥有的内存数据库。
        pass


class StudentRegistrationClosedTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[1] / "database" / "schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))
        self.db.executemany(
            "INSERT INTO app_users (user_id, username, password_hash, role) VALUES (?, ?, 'unused', ?)",
            [("S", "student", "STUDENT"), ("P", "professor", "PROFESSOR")],
        )
        self.db.execute("INSERT INTO students (student_id, user_id, full_name, date_of_birth) "
                        "VALUES ('S', 'S', 'Student', '2000-01-01')")
        self.db.execute("INSERT INTO professors "
                        "(professor_id, user_id, full_name, date_of_birth, department_id) "
                        "VALUES ('P', 'P', 'Professor', '1980-01-01', 'CS')")
        today = date.today()
        self.db.execute("INSERT INTO registration_periods VALUES ('TERM', 'CLOSED', ?, ?, ?, ?, NULL)",
                        ((today - timedelta(days=1)).isoformat(),
                         (today + timedelta(days=1)).isoformat()) * 2)
        self.db.execute("INSERT INTO student_schedules (schedule_id, student_id, semester_id, status) "
                        "VALUES (1, 'S', 'TERM', 'SUBMITTED')")
        self.primary = [f"O{i}" for i in range(1, 5)]
        self.alternate = ["O5", "O6"]
        self.catalog = {
            "semesters": [{"semesterId": "TERM"}],
            "courses": [{"courseId": f"C{i}", "title": f"Course {i}", "credits": 3}
                        for i in range(1, 7)],
            "courseOfferings": [{"offeringId": f"O{i}", "courseId": f"C{i}", "semesterId": "TERM",
                                 "capacity": 10, "enrolledCount": 0, "status": "OPEN", "days": []}
                                for i in range(1, 7)],
            "prerequisites": [],
        }
        for i in range(1, 7):
            self.db.execute("INSERT INTO professor_offering_selections "
                            "(professor_id, offering_id, semester_id) VALUES ('P', ?, 'TERM')", (f"O{i}",))
            self.db.execute("INSERT INTO schedule_choices "
                            "(schedule_id, offering_id, choice_type, preference_order, selection_status) "
                            "VALUES (1, ?, ?, ?, ?)",
                            (f"O{i}", "PRIMARY" if i <= 4 else "ALTERNATE", i if i <= 4 else i - 4,
                             "ENROLLED" if i <= 4 else "SELECTED"))
            if i <= 4:
                self.db.execute("INSERT INTO enrollments "
                                "(schedule_id, offering_id, course_id, course_title_snapshot, credits_snapshot) "
                                "VALUES (1, ?, ?, ?, 3)", (f"O{i}", f"C{i}", f"Course {i}"))
        self.db.commit()
        self.connection = StudentConnection(self.db)
        self.handler = object.__new__(server.StudentHandler)

        @contextmanager
        def transaction():
            with self.db:
                yield self.connection

        for target, replacement in (("transaction", transaction),
                                    ("get_connection", lambda: self.connection),
                                    ("_fetch_catalog", lambda: self.catalog)):
            mock = patch.object(server, target, replacement)
            mock.start()
            self.addCleanup(mock.stop)

    def mutate(self, operation):
        if operation == "delete":
            self.handler._delete_schedule("S", "S", "TERM")
        else:
            self.handler._save_or_submit(operation == "submit", "S", "S", "TERM",
                                         self.primary, self.alternate, self.catalog)

    def assert_rejected_without_changes(self, operation):
        before = list(self.db.iterdump())
        with self.assertRaises(server.ApiError) as error:
            self.mutate(operation)
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(list(self.db.iterdump()), before)
        return str(error.exception)

    def student_data(self):
        return server._build_student_data("S", "TERM")

    def test_closed_mutations_reject_all_schedule_states(self):
        for status in ("DRAFT", "SUBMITTED", "FINALIZED", "CANCELLED"):
            self.db.execute("UPDATE student_schedules SET status = ?", (status,))
            self.db.commit()
            for operation in ("save", "submit", "delete"):
                with self.subTest(status=status, operation=operation):
                    message = self.assert_rejected_without_changes(operation)
                    self.assertIn("注册已关闭", message)

    def test_closed_registration_does_not_create_a_schedule(self):
        self.db.execute("DELETE FROM schedule_choices")
        self.db.execute("DELETE FROM enrollments")
        self.db.execute("DELETE FROM student_schedules")
        self.db.commit()
        for operation in ("save", "submit", "delete"):
            with self.subTest(operation=operation):
                self.assert_rejected_without_changes(operation)

    def test_closed_registration_disables_all_page_window_flags(self):
        for status in ("DRAFT", "SUBMITTED", "FINALIZED"):
            self.db.execute("UPDATE student_schedules SET status = ?", (status,))
            data = self.student_data()
            with self.subTest(status=status):
                for key in ("canEdit", "registrationOpen", "addDropOpen"):
                    self.assertFalse(data[key], key)
        self.db.execute("DELETE FROM enrollments")
        self.db.execute("DELETE FROM schedule_choices")
        self.db.execute("DELETE FROM student_schedules")
        self.assertFalse(self.student_data()["canEdit"])

    def test_open_add_drop_allows_save_submit_and_delete(self):
        self.db.execute("UPDATE registration_periods SET registration_status = 'OPEN'")
        self.db.commit()
        self.assertTrue(self.student_data()["canEdit"])
        self.assertTrue(self.student_data()["addDropOpen"])
        self.mutate("save")
        self.assertEqual(self.db.execute("SELECT status FROM student_schedules").fetchone()[0], "DRAFT")
        self.mutate("submit")
        self.assertEqual(self.db.execute("SELECT status FROM student_schedules").fetchone()[0], "SUBMITTED")
        self.mutate("delete")
        self.assertEqual(self.db.execute("SELECT status FROM student_schedules").fetchone()[0], "CANCELLED")

    def test_open_initial_registration_allows_new_schedule(self):
        self.db.execute("DELETE FROM schedule_choices")
        self.db.execute("DELETE FROM enrollments")
        self.db.execute("DELETE FROM student_schedules")
        past = (date.today() - timedelta(days=3)).isoformat()
        self.db.execute("UPDATE registration_periods SET registration_status = 'OPEN', "
                        "add_drop_start_at = ?, add_drop_end_at = ?", (past, past))
        self.db.commit()
        data = self.student_data()
        self.assertTrue(data["canEdit"])
        self.assertTrue(data["registrationOpen"])
        self.assertFalse(data["addDropOpen"])
        self.mutate("save")
        self.mutate("submit")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM enrollments WHERE enrollment_status = 'ENROLLED'")
                         .fetchone()[0], 4)

    def test_open_submitted_schedule_still_requires_add_drop_window(self):
        past = (date.today() - timedelta(days=3)).isoformat()
        self.db.execute("UPDATE registration_periods SET registration_status = 'OPEN', "
                        "add_drop_start_at = ?, add_drop_end_at = ?", (past, past))
        self.db.commit()
        self.assertFalse(self.student_data()["canEdit"])
        for operation in ("save", "submit", "delete"):
            with self.subTest(operation=operation):
                self.assertIn("加退选期间", self.assert_rejected_without_changes(operation))

    def test_registration_period_is_locked_before_schedule_for_each_write(self):
        self.db.execute("UPDATE registration_periods SET registration_status = 'OPEN'")
        self.db.commit()
        for operation in ("save", "submit", "delete"):
            self.connection.queries.clear()
            self.mutate(operation)
            first = self.connection.queries[0]
            self.assertIn("FROM registration_periods", first)
            self.assertIn("FOR UPDATE", first)


if __name__ == "__main__":
    unittest.main()
