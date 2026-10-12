"""向现有 MySQL 数据库安全补充 5 个学生演示账号。"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from init_database import STUDENTS, password_hash  # noqa: E402
from database.mysql_connection import transaction  # noqa: E402


DEMO_STUDENTS = STUDENTS[2:]


def seed_demo_students() -> tuple[int, int]:
    added = 0
    already_present = 0
    with transaction() as connection:
        cursor = connection.cursor(dictionary=True)
        try:
            for student in DEMO_STUDENTS:
                student_id = student["student_id"]
                username = student["username"]
                cursor.execute(
                    "SELECT user_id, username, role FROM app_users "
                    "WHERE user_id = %s OR username = %s FOR UPDATE",
                    (student_id, username),
                )
                users = cursor.fetchall()
                if users:
                    if (len(users) != 1 or users[0]["user_id"] != student_id
                            or users[0]["username"] != username or users[0]["role"] != "STUDENT"):
                        raise RuntimeError(
                            f"账号或编号 {username} / {student_id} 已被其他记录占用；未覆盖任何现有数据。"
                        )
                    cursor.execute(
                        "SELECT student_id, user_id FROM students "
                        "WHERE student_id = %s OR user_id = %s FOR UPDATE",
                        (student_id, student_id),
                    )
                    profiles = cursor.fetchall()
                    if (len(profiles) != 1 or profiles[0]["student_id"] != student_id
                            or profiles[0]["user_id"] != student_id):
                        raise RuntimeError(
                            f"账号 {username} 已存在但学生档案不匹配；未覆盖任何现有数据。"
                        )
                    already_present += 1
                    continue

                cursor.execute(
                    "SELECT student_id, user_id FROM students "
                    "WHERE student_id = %s OR user_id = %s FOR UPDATE",
                    (student_id, student_id),
                )
                if cursor.fetchone():
                    raise RuntimeError(
                        f"学号 {student_id} 已被现有学生档案占用；未覆盖任何现有数据。"
                    )

                cursor.execute(
                    "INSERT INTO app_users (user_id, username, password_hash, role, is_active) "
                    "VALUES (%s, %s, %s, 'STUDENT', 1)",
                    (student_id, username, password_hash(student["password"])),
                )
                cursor.execute(
                    "INSERT INTO students "
                    "(student_id, user_id, full_name, date_of_birth, status, graduation_date) "
                    "VALUES (%s, %s, %s, %s, 'ACTIVE', %s)",
                    (student_id, student_id, student["full_name"], student["date_of_birth"],
                     student["graduation_date"]),
                )
                added += 1
        finally:
            cursor.close()
    return added, already_present


def main() -> None:
    try:
        if not os.environ.get("MYSQL_PASSWORD"):
            os.environ["MYSQL_PASSWORD"] = getpass.getpass("请输入 MySQL 应用账号密码：")
        added, already_present = seed_demo_students()
    except Exception as exc:
        raise SystemExit(f"补充学生演示账号失败：{exc}") from exc
    print(f"已新增 {added} 个学生演示账号；已有并保留 {already_present} 个账号。")
    print("需要查看用户名和演示密码时，请参考 student/README.md。")


if __name__ == "__main__":
    main()
