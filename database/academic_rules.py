"""学生提交与关闭注册共用的先修课程通过标准。"""

from __future__ import annotations

from typing import Any


PASSING_GRADES = ("A", "B", "C", "D")


def passed_courses_by_student(
    connection: Any, student_id: str | None = None,
) -> dict[str, set[str]]:
    """只有已结课且有通过成绩的课程满足先修要求；任一次通过的重修有效。"""
    sql = (
        "SELECT DISTINCT sch.student_id, e.course_id FROM enrollments e "
        "JOIN student_schedules sch ON sch.schedule_id = e.schedule_id "
        "JOIN course_grades g ON g.enrollment_id = e.enrollment_id "
        "WHERE e.enrollment_status = 'COMPLETED' "
        "AND g.letter_grade IN (" + ",".join(["%s"] * len(PASSING_GRADES)) + ")"
    )
    params = PASSING_GRADES
    if student_id is not None:
        sql += " AND sch.student_id = %s"
        params += (student_id,)
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        passed: dict[str, set[str]] = {}
        for row in cursor.fetchall():
            passed.setdefault(str(row["student_id"]), set()).add(str(row["course_id"]))
        return passed
    finally:
        cursor.close()
