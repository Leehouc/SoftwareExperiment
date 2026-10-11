from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SCHEMA_FILE = ROOT / "database" / "schema.sql"
DEFAULT_DATABASE = ROOT / "data" / "registration.db"
PBKDF2_ITERATIONS = 310_000


STUDENTS = [
    {
        "student_id": "S2026001",
        "username": "student.lin",
        "password": "StudentDemo1!",
        "full_name": "林晓雨",
        "date_of_birth": "2005-04-12",
        "status": "ACTIVE",
        "graduation_date": "2028-06-30",
    },
    {
        "student_id": "S2026002",
        "username": "student.chen",
        "password": "StudentDemo2!",
        "full_name": "陈嘉伟",
        "date_of_birth": "2004-09-28",
        "status": "ACTIVE",
        "graduation_date": "2027-06-30",
    },
]

PROFESSORS = [
    ("P1001", "prof.chen", "ProfessorDemo1!", "陈梅雅教授", "1979-03-14", "CS"),
    ("P1002", "prof.ortega", "ProfessorDemo2!", "路易斯·奥尔特加教授", "1975-11-03", "CS"),
    ("P1003", "prof.shah", "ProfessorDemo3!", "普丽雅·沙阿教授", "1981-07-22", "CS"),
    ("P1004", "prof.brooks", "ProfessorDemo4!", "伊芙琳·布鲁克斯教授", "1983-02-18", "CS"),
    ("P1005", "prof.wu", "ProfessorDemo5!", "吴亨利教授", "1977-09-09", "MATH"),
    ("P1006", "prof.johnson", "ProfessorDemo6!", "阿米娜·约翰逊教授", "1980-05-26", "BUS"),
]

REGISTRAR = ("R0001", "registrar.demo", "RegistrarDemo1!", "陈老师")

ELIGIBILITY = [
    ("P1001", "CS101"),
    ("P1002", "CS201"),
    ("P1003", "CS301"),
    ("P1004", "CS315"),
    ("P1005", "MATH210"),
    ("P1005", "STAT220"),
    ("P1006", "BUS205"),
]

OFFERING_ASSIGNMENTS = [
    ("P1001", "2026S-CS101-01", "2026-SPRING", "CONFIRMED"),
    ("P1002", "2026S-CS201-01", "2026-SPRING", "CONFIRMED"),
    ("P1005", "2026S-MATH110-01", "2026-SPRING", "CONFIRMED"),
    ("P1001", "2027S-CS101-01", "2027-SPRING", "CONFIRMED"),
    ("P1002", "2027S-CS201-01", "2027-SPRING", "CONFIRMED"),
    ("P1003", "2027S-CS301-01", "2027-SPRING", "CONFIRMED"),
    ("P1004", "2027S-CS315-01", "2027-SPRING", "CONFIRMED"),
    ("P1005", "2027S-MATH210-01", "2027-SPRING", "CONFIRMED"),
    ("P1005", "2027S-STAT220-01", "2027-SPRING", "CONFIRMED"),
    ("P1006", "2027S-BUS205-01", "2027-SPRING", "CONFIRMED"),
]

HISTORICAL_COURSES = [
    ("CS101", "程序设计导论", 3, "2026S-CS101-01", "P1001", "A"),
    ("CS201", "数据结构", 3, "2026S-CS201-01", "P1002", "B"),
    ("MATH110", "大学代数", 3, "2026S-MATH110-01", "P1005", "A"),
]

SPRING_2027_CHOICES = {
    "S2026001": {
        "primary": ["2027S-CS301-01", "2027S-CS315-01", "2027S-MATH210-01", "2027S-STAT220-01"],
        "alternate": ["2027S-CS101-01", "2027S-BUS205-01"],
    },
    "S2026002": {
        "primary": ["2027S-CS201-01", "2027S-MATH210-01", "2027S-CS315-01", "2027S-BUS205-01"],
        "alternate": ["2027S-CS301-01", "2027S-CS101-01"],
    },
}

COURSE_SNAPSHOTS = {
    "CS101": ("程序设计导论", 3),
    "CS201": ("数据结构", 3),
    "MATH110": ("大学代数", 3),
}


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    salt_text = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    digest_text = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt_text}${digest_text}"


def add_user(connection: sqlite3.Connection, user_id: str, username: str, password: str, role: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO app_users (user_id, username, password_hash, role) VALUES (?, ?, ?, ?)",
        (user_id, username, password_hash(password), role),
    )


def get_or_create_schedule(
    connection: sqlite3.Connection, student_id: str, semester_id: str, status: str
) -> int:
    connection.execute(
        "INSERT OR IGNORE INTO student_schedules (student_id, semester_id, status) VALUES (?, ?, ?)",
        (student_id, semester_id, status),
    )
    row = connection.execute(
        "SELECT schedule_id FROM student_schedules WHERE student_id = ? AND semester_id = ?",
        (student_id, semester_id),
    ).fetchone()
    assert row is not None
    return int(row[0])


def seed(connection: sqlite3.Connection) -> None:
    for student in STUDENTS:
        add_user(connection, student["student_id"], student["username"], student["password"], "STUDENT")
        connection.execute(
            """INSERT OR IGNORE INTO students
               (student_id, user_id, full_name, date_of_birth, status, graduation_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (student["student_id"], student["student_id"], student["full_name"],
             student["date_of_birth"], student["status"], student["graduation_date"]),
        )

    for professor_id, username, password, full_name, birth_date, department_id in PROFESSORS:
        add_user(connection, professor_id, username, password, "PROFESSOR")
        connection.execute(
            """INSERT OR IGNORE INTO professors
               (professor_id, user_id, full_name, date_of_birth, department_id)
               VALUES (?, ?, ?, ?, ?)""",
            (professor_id, professor_id, full_name, birth_date, department_id),
        )

    registrar_id, registrar_username, registrar_password, registrar_name = REGISTRAR
    add_user(connection, registrar_id, registrar_username, registrar_password, "REGISTRAR")
    connection.execute(
        "INSERT OR IGNORE INTO registrars (registrar_id, user_id, full_name) VALUES (?, ?, ?)",
        (registrar_id, registrar_id, registrar_name),
    )

    connection.executemany(
        """INSERT OR IGNORE INTO registration_periods
           (semester_id, registration_status, registration_start_at, registration_end_at,
            add_drop_start_at, add_drop_end_at, closed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            ("2026-SPRING", "CLOSED", "2025-10-01", "2026-01-15", "2026-01-11", "2026-01-25", "2026-01-26"),
            ("2026-FALL", "CLOSED", "2026-03-01", "2026-08-20", "2026-08-24", "2026-09-10", "2026-09-11"),
            ("2027-SPRING", "OPEN", "2026-10-01", "2027-01-15", "2027-01-11", "2027-01-25", None),
        ],
    )
    connection.executemany(
        "INSERT OR IGNORE INTO professor_course_eligibility (professor_id, course_id) VALUES (?, ?)",
        ELIGIBILITY,
    )
    connection.executemany(
        """INSERT OR IGNORE INTO professor_offering_selections
           (professor_id, offering_id, semester_id, selection_status) VALUES (?, ?, ?, ?)""",
        OFFERING_ASSIGNMENTS,
    )

    # Completed prior-semester coursework supports prerequisite checks and report cards.
    historical_schedules: dict[str, int] = {}
    for student in STUDENTS:
        schedule_id = get_or_create_schedule(connection, student["student_id"], "2026-SPRING", "FINALIZED")
        historical_schedules[student["student_id"]] = schedule_id
        selected = HISTORICAL_COURSES if student["student_id"] == "S2026001" else HISTORICAL_COURSES[:2]
        for course_id, title, credits, offering_id, professor_id, grade in selected:
            connection.execute(
                """INSERT OR IGNORE INTO enrollments
                   (schedule_id, offering_id, course_id, course_title_snapshot, credits_snapshot, enrollment_status)
                   VALUES (?, ?, ?, ?, ?, 'COMPLETED')""",
                (schedule_id, offering_id, course_id, title, credits),
            )
            enrollment = connection.execute(
                "SELECT enrollment_id FROM enrollments WHERE schedule_id = ? AND offering_id = ?",
                (schedule_id, offering_id),
            ).fetchone()
            assert enrollment is not None
            connection.execute(
                "INSERT OR IGNORE INTO course_grades (enrollment_id, professor_id, letter_grade) VALUES (?, ?, ?)",
                (int(enrollment[0]), professor_id, grade),
            )

    for student_id, choice_sets in SPRING_2027_CHOICES.items():
        schedule_id = get_or_create_schedule(connection, student_id, "2027-SPRING", "DRAFT")
        for choice_type, offering_ids in (("PRIMARY", choice_sets["primary"]), ("ALTERNATE", choice_sets["alternate"])):
            for preference_order, offering_id in enumerate(offering_ids, start=1):
                connection.execute(
                    """INSERT OR IGNORE INTO schedule_choices
                       (schedule_id, offering_id, choice_type, preference_order, selection_status)
                       VALUES (?, ?, ?, ?, 'SELECTED')""",
                    (schedule_id, offering_id, choice_type, preference_order),
                )

    payload = {
        "studentId": "S2026001",
        "semesterId": "2026-SPRING",
        "enrollments": [
            {"offeringId": "2026S-CS101-01", "courseId": "CS101", "credits": 3},
            {"offeringId": "2026S-CS201-01", "courseId": "CS201", "credits": 3},
            {"offeringId": "2026S-MATH110-01", "courseId": "MATH110", "credits": 3},
        ],
    }
    connection.execute(
        """INSERT OR IGNORE INTO billing_transactions
           (transaction_id, student_id, semester_id, idempotency_key, amount_cents, status, payload_json, attempt_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("BILL-DEMO-2026S-001", "S2026001", "2026-SPRING", "S2026001-2026-SPRING",
         135000, "SENT", json.dumps(payload, ensure_ascii=False, separators=(",", ":")), 1),
    )
    connection.execute(
        """INSERT OR IGNORE INTO audit_logs
           (actor_user_id, action, entity_type, entity_id, details_json)
           VALUES (?, 'CLOSE_REGISTRATION', 'SEMESTER', '2026-SPRING', ?)""",
        (registrar_id, json.dumps({"demo": True}, separators=(",", ":"))),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and seed the Course Registration SQLite database.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="SQLite file path (default: data/registration.db)")
    parser.add_argument("--reset", action="store_true", help="Delete the selected SQLite file before recreating it")
    parser.add_argument("--empty", action="store_true", help="Create only the schema, without demo records")
    args = parser.parse_args()

    database_path = args.database.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if args.reset and database_path.exists():
        database_path.unlink()

    schema = SCHEMA_FILE.read_text(encoding="utf-8-sig")
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(schema)
        if not args.empty:
            seed(connection)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Database contains foreign-key violations: {violations}")

    print(f"SQLite database ready: {database_path}")
    print("Demo data included." if not args.empty else "Empty schema created.")


if __name__ == "__main__":
    main()
