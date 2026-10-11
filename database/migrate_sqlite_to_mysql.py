from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE = ROOT / "data" / "registration.db"
TABLES_IN_FK_ORDER = (
    "app_users",
    "students",
    "professors",
    "registrars",
    "registration_periods",
    "student_schedules",
    "schedule_choices",
    "professor_course_eligibility",
    "professor_offering_selections",
    "enrollments",
    "course_grades",
    "billing_transactions",
    "audit_logs",
)
DATETIME_COLUMNS = {
    "created_at", "updated_at", "selected_at", "closed_at", "enrolled_at", "graded_at"
}


def mysql_datetime(value: Any, column: str) -> Any:
    if value is None or column not in DATETIME_COLUMNS or not isinstance(value, str):
        return value
    # SQLite stores UTC ISO-8601 text with a trailing Z. MySQL DATETIME has no
    # timezone component, so normalize to naive UTC before inserting.
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy the local course-registration SQLite data into an empty MySQL database."
    )
    parser.add_argument("--sqlite", type=Path, default=DEFAULT_SQLITE, help="SQLite source (default: data/registration.db)")
    args = parser.parse_args()

    sqlite_path = args.sqlite.expanduser().resolve()
    if not sqlite_path.is_file():
        raise SystemExit(f"SQLite source not found: {sqlite_path}")

    try:
        import mysql.connector
    except ImportError as exc:
        raise SystemExit("MySQL driver missing. Install it with: python -m pip install -r requirements-mysql.txt") from exc

    mysql_options = {
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER", "course_app"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
        "database": os.environ.get("MYSQL_DATABASE", "course_registration"),
        "charset": "utf8mb4",
        "collation": "utf8mb4_0900_ai_ci",
        "autocommit": False,
    }
    if not mysql_options["password"]:
        raise SystemExit("Set MYSQL_PASSWORD in the current shell before running the migration.")

    source = sqlite3.connect(sqlite_path)
    target = None
    try:
        source.execute("PRAGMA foreign_keys = ON")
        target = mysql.connector.connect(**mysql_options)
        target.start_transaction()
        cursor = target.cursor()

        # Fail safely instead of merging demo records over an existing database.
        for table in TABLES_IN_FK_ORDER:
            cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
            row_count = int(cursor.fetchone()[0])
            if row_count:
                raise RuntimeError(
                    f"Target table `{table}` is not empty ({row_count} rows). "
                    "Use a fresh course_registration database for the one-time migration."
                )

        totals: dict[str, int] = {}
        for table in TABLES_IN_FK_ORDER:
            columns = [row[1] for row in source.execute(f"PRAGMA table_info(`{table}`)")]
            rows = source.execute(f"SELECT * FROM `{table}`").fetchall()
            if not columns:
                raise RuntimeError(f"SQLite table not found: {table}")
            if rows:
                names = ", ".join(f"`{column}`" for column in columns)
                marks = ", ".join(["%s"] * len(columns))
                statement = f"INSERT INTO `{table}` ({names}) VALUES ({marks})"
                prepared = [
                    tuple(mysql_datetime(value, column) for column, value in zip(columns, row))
                    for row in rows
                ]
                cursor.executemany(statement, prepared)
            totals[table] = len(rows)

        target.commit()
        for table, count in totals.items():
            print(f"{table}: {count} rows")
        print(f"Migration completed into MySQL database `{mysql_options['database']}`.")
    except Exception:
        if target is not None:
            target.rollback()
        raise
    finally:
        source.close()
        if target is not None:
            target.close()


if __name__ == "__main__":
    main()

