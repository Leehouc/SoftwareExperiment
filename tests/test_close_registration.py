"""注册关闭的持久化回归测试；使用内存 SQLite 执行 SQL，不连接演示数据库。"""

from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from administer import server


class SqliteCursor:
    """适配被测流程使用的 MySQL 占位符和少量 SQL 方言。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.cursor = connection.cursor()

    def execute(self, sql: str, params: tuple = ()) -> None:
        sql = sql.replace("%s", "?").replace(" FOR UPDATE", "")
        sql = sql.replace("CURRENT_TIMESTAMP(3)", "CURRENT_TIMESTAMP")
        sql = sql.replace(
            "ON DUPLICATE KEY UPDATE idempotency_key = VALUES(idempotency_key)",
            "ON CONFLICT(idempotency_key) DO NOTHING",
        )
        self.cursor.execute(sql, params)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        for key in ("registration_start_at", "registration_end_at"):
            if key in result:
                result[key] = date.fromisoformat(result[key])
        return result

    def fetchone(self) -> dict | None:
        return self._row(self.cursor.fetchone())

    def fetchall(self) -> list[dict]:
        return [self._row(row) for row in self.cursor.fetchall()]

    def close(self) -> None:
        self.cursor.close()


class SqliteConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def cursor(self, dictionary: bool = False) -> SqliteCursor:
        return SqliteCursor(self.connection)


class CloseRegistrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[1] / "database" / "schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))
        self.db.executemany(
            "INSERT INTO app_users (user_id, username, password_hash, role) VALUES (?, ?, 'unused', ?)",
            [("R", "registrar", "REGISTRAR"), ("P", "professor", "PROFESSOR")]
            + [(f"S{i}", f"student{i}", "STUDENT") for i in range(1, 4)],
        )
        self.db.execute(
            "INSERT INTO professors (professor_id, user_id, full_name, date_of_birth, department_id) "
            "VALUES ('P', 'P', 'Professor', '1980-01-01', 'CS')"
        )
        self.db.executemany(
            "INSERT INTO students (student_id, user_id, full_name, date_of_birth) "
            "VALUES (?, ?, ?, '2000-01-01')",
            [(f"S{i}", f"S{i}", f"Student {i}") for i in range(1, 4)],
        )
        today = date.today()
        self.db.execute(
            "INSERT INTO registration_periods VALUES ('TERM', 'OPEN', ?, ?, ?, ?, NULL)",
            ((today - timedelta(days=1)).isoformat(), (today + timedelta(days=1)).isoformat()) * 2,
        )
        self.db.executemany(
            "INSERT INTO student_schedules (schedule_id, student_id, semester_id, status) "
            "VALUES (?, ?, 'TERM', 'SUBMITTED')",
            [(i, f"S{i}") for i in range(1, 4)],
        )
        self.catalog = {"courseOfferings": [], "courses": [], "prerequisites": []}

    def add_offering(self, offering_id: str, students: tuple[int, ...], *,
                     assigned: bool = True, credits: int = 3, semester: str = "TERM") -> None:
        self.catalog["courses"].append({"courseId": offering_id, "title": offering_id, "credits": credits})
        self.catalog["courseOfferings"].append({
            "offeringId": offering_id, "courseId": offering_id, "semesterId": semester,
            "capacity": 10, "enrolledCount": 0, "status": "OPEN", "days": [],
        })
        if assigned:
            self.db.execute(
                "INSERT INTO professor_offering_selections "
                "(professor_id, offering_id, semester_id, selection_status) VALUES ('P', ?, ?, 'CONFIRMED')",
                (offering_id, semester),
            )
        for schedule_id in students:
            priority = self.db.execute(
                "SELECT COUNT(*) + 1 FROM schedule_choices WHERE schedule_id = ?", (schedule_id,)
            ).fetchone()[0]
            self.db.execute(
                "INSERT INTO schedule_choices "
                "(schedule_id, offering_id, choice_type, preference_order, selection_status) "
                "VALUES (?, ?, 'PRIMARY', ?, 'ENROLLED')", (schedule_id, offering_id, priority),
            )
            self.db.execute(
                "INSERT INTO enrollments "
                "(schedule_id, offering_id, course_id, course_title_snapshot, credits_snapshot) "
                "VALUES (?, ?, ?, ?, ?)", (schedule_id, offering_id, offering_id, offering_id, credits),
            )

    def close_registration(self) -> dict:
        self.db.commit()

        @contextmanager
        def transaction():
            with self.db:
                yield SqliteConnection(self.db)

        with patch.object(server, "transaction", transaction), \
                patch.object(server, "AMOUNT_CENTS_PER_CREDIT", 15000):
            return server._close_registration("TERM", {"user": {"userId": "R"}}, self.catalog)

    def add_alternate(self, offering_id: str, order: int, *, assigned: bool = True) -> dict:
        self.add_offering(offering_id, (), assigned=assigned)
        self.db.executemany(
            "INSERT INTO schedule_choices "
            "(schedule_id, offering_id, choice_type, preference_order, selection_status) "
            "VALUES (?, ?, 'ALTERNATE', ?, 'SELECTED')",
            [(i, offering_id, order) for i in range(1, 4)],
        )
        return self.catalog["courseOfferings"][-1]

    def add_four_primaries(self) -> None:
        for offering_id in ("PRIMARY1", "PRIMARY2", "PRIMARY3", "INVALID_PRIMARY"):
            self.add_offering(offering_id, (1, 2, 3))

    def assert_replacement(self, alternate: str) -> None:
        expected = {"PRIMARY1", "PRIMARY2", "PRIMARY3", alternate}
        for offering_id in expected:
            for table in ("enrollments", "schedule_choices"):
                self.assertEqual(self.offering_states(table, offering_id), ["ENROLLED"] * 3)
        for table in ("enrollments", "schedule_choices"):
            self.assertEqual(self.offering_states(table, "INVALID_PRIMARY"), ["CANCELLED"] * 3)
        self.assert_finalized()
        self.assert_billing(expected, 180000)

    def offering_states(self, table: str, offering_id: str) -> list[str]:
        column = "enrollment_status" if table == "enrollments" else "selection_status"
        return [row[0] for row in self.db.execute(
            f"SELECT {column} FROM {table} WHERE offering_id = ? ORDER BY schedule_id", (offering_id,)
        )]

    def assert_finalized(self) -> None:
        self.assertEqual([row[0] for row in self.db.execute(
            "SELECT status FROM student_schedules WHERE semester_id = 'TERM' ORDER BY schedule_id"
        )], ["FINALIZED"] * 3)
        self.assertEqual(self.db.execute(
            "SELECT registration_status FROM registration_periods WHERE semester_id = 'TERM'"
        ).fetchone()[0], "CLOSED")

    def assert_billing(self, expected: set[str], amount: int) -> None:
        rows = self.db.execute("SELECT * FROM billing_transactions ORDER BY student_id").fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["student_id"] for row in rows}, {"S1", "S2", "S3"})
        for row in rows:
            self.assertEqual(row["amount_cents"], amount)
            self.assertEqual(row["status"], "PENDING")
            payload = json.loads(row["payload_json"])
            self.assertEqual({item["offeringId"] for item in payload["enrollments"]}, expected)


class CloseRegistrationTests(CloseRegistrationTestCase):
    def test_missing_professor_cancels_only_that_offering(self) -> None:
        self.add_offering("VALID", (1, 2, 3))
        self.add_offering("NO_PROFESSOR", (1, 2, 3), assigned=False)
        result = self.close_registration()
        self.assertEqual(result["cancelledOfferings"], ["NO_PROFESSOR"])
        self.assertEqual(result["cancelledSchedules"], 0)
        self.assertEqual(result["billingTransactionsQueued"], 3)
        for table in ("enrollments", "schedule_choices"):
            self.assertEqual(self.offering_states(table, "VALID"), ["ENROLLED"] * 3)
            self.assertEqual(self.offering_states(table, "NO_PROFESSOR"), ["CANCELLED"] * 3)
        self.assert_finalized()
        self.assert_billing({"VALID"}, 45000)
        details = json.loads(self.db.execute("SELECT details_json FROM audit_logs").fetchone()[0])
        self.assertEqual(details["cancelledSchedules"], 0)

    def test_low_enrollment_does_not_cancel_other_three_student_class(self) -> None:
        self.add_offering("VALID", (1, 2, 3), credits=4)
        self.add_offering("TOO_SMALL", (1, 2))
        result = self.close_registration()
        self.assertEqual(result["cancelledOfferings"], ["TOO_SMALL"])
        for table in ("enrollments", "schedule_choices"):
            self.assertEqual(self.offering_states(table, "VALID"), ["ENROLLED"] * 3)
            self.assertEqual(self.offering_states(table, "TOO_SMALL"), ["CANCELLED"] * 2)
        self.assertEqual(self.db.execute(
            "SELECT selection_status FROM professor_offering_selections WHERE offering_id = 'VALID'"
        ).fetchone()[0], "CONFIRMED")
        self.assertEqual(self.db.execute(
            "SELECT selection_status FROM professor_offering_selections WHERE offering_id = 'TOO_SMALL'"
        ).fetchone()[0], "WITHDRAWN")
        self.assert_finalized()
        self.assert_billing({"VALID"}, 60000)

    def test_no_cancelled_offerings_preserves_enrolled_choices(self) -> None:
        self.add_offering("VALID", (1, 2, 3))
        result = self.close_registration()
        self.assertEqual(result["cancelledOfferings"], [])
        self.assertEqual(self.offering_states("schedule_choices", "VALID"), ["ENROLLED"] * 3)
        self.assert_finalized()
        self.assert_billing({"VALID"}, 45000)

    def test_all_courses_cancelled_keeps_empty_schedule_without_billing(self) -> None:
        self.add_offering("NO_PROFESSOR", (1, 2, 3), assigned=False)
        result = self.close_registration()
        self.assertEqual(result["cancelledSchedules"], 0)
        self.assertEqual(result["billingTransactionsQueued"], 0)
        self.assertEqual(self.offering_states("enrollments", "NO_PROFESSOR"), ["CANCELLED"] * 3)
        self.assert_finalized()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM billing_transactions").fetchone()[0], 0)

    def test_other_semester_and_dropped_records_are_preserved(self) -> None:
        self.add_offering("VALID", (1, 2, 3))
        self.add_offering("TOO_SMALL", (1, 2))
        self.db.execute("UPDATE enrollments SET enrollment_status = 'DROPPED' "
                        "WHERE offering_id = 'TOO_SMALL' AND schedule_id = 2")
        self.db.execute("UPDATE schedule_choices SET selection_status = 'CANCELLED' "
                        "WHERE offering_id = 'TOO_SMALL' AND schedule_id = 2")
        self.db.execute("INSERT INTO student_schedules (schedule_id, student_id, semester_id, status) "
                        "VALUES (4, 'S1', 'OTHER', 'SUBMITTED')")
        self.add_offering("OTHER_OFFERING", (4,), semester="OTHER")
        self.close_registration()
        self.assertEqual(self.offering_states("enrollments", "TOO_SMALL"), ["CANCELLED", "DROPPED"])
        for table in ("enrollments", "schedule_choices"):
            self.assertEqual(self.offering_states(table, "OTHER_OFFERING"), ["ENROLLED"])
        self.assertEqual(self.db.execute("SELECT status FROM student_schedules WHERE schedule_id = 4")
                         .fetchone()[0], "SUBMITTED")
        self.assert_billing({"VALID"}, 45000)

    def test_invalid_primary_is_replaced_by_first_available_alternate(self) -> None:
        self.add_four_primaries()
        self.db.execute("DELETE FROM professor_offering_selections WHERE offering_id = 'INVALID_PRIMARY'")
        self.add_alternate("FIRST_ALTERNATE", 1)
        second = self.add_alternate("SECOND_ALTERNATE", 2)
        second["enrolledCount"] = 3
        result = self.close_registration()
        self.assertEqual(result["cancelledOfferings"], ["INVALID_PRIMARY"])
        self.assertEqual(result["cancelledSchedules"], 0)
        self.assertEqual(result["billingTransactionsQueued"], 3)
        self.assert_replacement("FIRST_ALTERNATE")
        self.assertEqual(self.offering_states("enrollments", "SECOND_ALTERNATE"), [])
        self.assertEqual(self.offering_states("schedule_choices", "SECOND_ALTERNATE"), ["CANCELLED"] * 3)

    def test_withdrawn_professor_primary_is_replaced(self) -> None:
        self.add_four_primaries()
        self.db.execute("UPDATE professor_offering_selections SET selection_status = 'WITHDRAWN' "
                        "WHERE offering_id = 'INVALID_PRIMARY'")
        self.add_alternate("ALTERNATE", 1)
        self.close_registration()
        self.assert_replacement("ALTERNATE")

    def test_cancelled_catalog_primary_is_replaced(self) -> None:
        self.add_four_primaries()
        self.catalog["courseOfferings"][-1]["status"] = "CANCELLED"
        self.add_alternate("ALTERNATE", 1)
        result = self.close_registration()
        self.assertIn("INVALID_PRIMARY", result["cancelledOfferings"])
        self.assert_replacement("ALTERNATE")

    def test_primary_removed_from_catalog_is_replaced_and_cancelled_locally(self) -> None:
        self.add_four_primaries()
        self.catalog["courseOfferings"].pop()
        self.add_alternate("ALTERNATE", 1)
        result = self.close_registration()
        self.assertIn("INVALID_PRIMARY", result["cancelledOfferings"])
        self.assert_replacement("ALTERNATE")

    def test_unavailable_first_alternate_is_skipped(self) -> None:
        self.add_four_primaries()
        self.db.execute("DELETE FROM professor_offering_selections WHERE offering_id = 'INVALID_PRIMARY'")
        first = self.add_alternate("FULL_ALTERNATE", 1)
        first["enrolledCount"] = 10
        self.add_alternate("SECOND_ALTERNATE", 2)
        self.close_registration()
        self.assert_replacement("SECOND_ALTERNATE")
        self.assertEqual(self.offering_states("enrollments", "FULL_ALTERNATE"), [])

    def test_full_existing_primary_is_retained(self) -> None:
        self.add_four_primaries()
        self.catalog["courseOfferings"][0]["status"] = "FULL"
        alternate = self.add_alternate("ALTERNATE", 1)
        alternate["enrolledCount"] = 3
        result = self.close_registration()
        self.assertEqual(result["cancelledOfferings"], [])
        self.assertEqual(self.offering_states("enrollments", "ALTERNATE"), [])
        self.assertEqual(self.offering_states("schedule_choices", "PRIMARY1"), ["ENROLLED"] * 3)
        self.assert_billing({"PRIMARY1", "PRIMARY2", "PRIMARY3", "INVALID_PRIMARY"}, 180000)


if __name__ == "__main__":
    unittest.main()
