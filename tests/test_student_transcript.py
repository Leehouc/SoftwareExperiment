"""成绩单按已结束学期过滤、默认学期与本人权限的数据库回归。"""

from datetime import date, timedelta
from unittest.mock import patch

from student import server
from tests.test_student_registration_closed import StudentScheduleTestCase


class StudentTranscriptTests(StudentScheduleTestCase):
    def setUp(self):
        super().setUp()
        today = date.today()
        self.catalog["semesters"] = [
            {"semesterId": "OLD", "name": "旧学期", "endDate": (today - timedelta(days=180)).isoformat()},
            {"semesterId": "TERM", "name": "进行中学期", "endDate": (today + timedelta(days=30)).isoformat()},
            {"semesterId": "RECENT", "name": "上一学期", "endDate": (today - timedelta(days=1)).isoformat()},
            {"semesterId": "TODAY", "endDate": today.isoformat()},
            {"semesterId": "INVALID", "endDate": "invalid"},
            {"semesterId": "UNDATED"},
        ]
        self.add_grade("OLD", "old", "F", graded_at="2099-01-01")
        self.add_grade("RECENT", "recent", "A", graded_at="2000-01-01")
        self.add_grade("RECENT", "incomplete", "I")
        self.add_grade("TERM", "current", "B")

    def add_grade(self, term, offering, grade="A", student="S", graded_at=None, status="COMPLETED"):
        self.db.execute("INSERT OR IGNORE INTO student_schedules (student_id, semester_id) VALUES (?, ?)",
                        (student, term))
        schedule = self.db.execute("SELECT schedule_id FROM student_schedules "
                                   "WHERE student_id = ? AND semester_id = ?", (student, term)).fetchone()[0]
        self.db.execute("INSERT INTO professor_offering_selections "
                        "(professor_id, offering_id, semester_id) VALUES ('P', ?, ?)", (offering, term))
        cursor = self.db.execute("INSERT INTO enrollments (schedule_id, offering_id, course_id, "
                                 "course_title_snapshot, credits_snapshot, enrollment_status) "
                                 "VALUES (?, ?, ?, ?, 3, ?)", (schedule, offering, offering, offering, status))
        if grade:
            self.db.execute("INSERT INTO course_grades (enrollment_id, professor_id, letter_grade) "
                            "VALUES (?, 'P', ?)", (cursor.lastrowid, grade))
            if graded_at:
                self.db.execute("UPDATE course_grades SET graded_at = ? WHERE enrollment_id = ?",
                                (graded_at, cursor.lastrowid))

    def transcript(self, term=None):
        return server._build_student_transcript("S", term)

    def test_default_uses_latest_end_date_and_filters_details_not_latest_grade_date(self):
        data = self.transcript()
        self.assertEqual(data["transcriptSemesterId"], "RECENT")
        self.assertEqual([row["semesterId"] for row in data["transcriptSemesters"]], ["RECENT", "OLD"])
        self.assertEqual({row["semester_id"] for row in data["transcript"]}, {"RECENT"})
        self.assertEqual({row["letter_grade"] for row in data["transcript"]}, {"A", "I"})
        self.assertEqual(data["completedCourseCount"], 3)
        detail_sql = [sql for sql in self.connection.queries if "course_title_snapshot" in sql]
        self.assertTrue(all("student_id = %s AND semester_id = %s" in sql for sql in detail_sql))

    def test_explicit_history_filter_keeps_current_schedule_and_cumulative_count(self):
        data = server._build_student_data("S", "TERM", "OLD")
        self.assertEqual(data["semesterId"], "TERM")
        self.assertEqual(data["schedule"]["schedule_id"], 1)
        self.assertEqual(len(data["choices"]), 6)
        self.assertEqual(data["transcriptSemesterId"], "OLD")
        self.assertEqual([row["letter_grade"] for row in data["transcript"]], ["F"])
        self.assertEqual(data["completedCourseCount"], 3)
        # 选课页面的学期不决定成绩单默认学期。
        self.assertEqual(server._build_student_data("S", "OLD")["transcriptSemesterId"], "RECENT")

    def test_latest_ended_term_with_no_grades_stays_default(self):
        self.db.execute("DELETE FROM course_grades WHERE enrollment_id IN "
                        "(SELECT enrollment_id FROM enrollments e JOIN student_schedules s "
                        "ON s.schedule_id = e.schedule_id WHERE semester_id = 'RECENT')")
        data = self.transcript()
        self.assertEqual(data["transcriptSemesterId"], "RECENT")
        self.assertEqual(data["transcript"], [])
        self.assertEqual(data["transcriptSemesters"][0]["recordCount"], 0)
        self.assertEqual(len(self.transcript("OLD")["transcript"]), 1)

    def test_current_end_today_invalid_undated_and_unknown_terms_are_rejected(self):
        for term in ("TERM", "TODAY", "INVALID", "UNDATED", "UNKNOWN", "OLD' OR 1=1 --"):
            with self.subTest(term=term), self.assertRaises(server.ApiError) as error:
                self.transcript(term)
            self.assertEqual(error.exception.status, 409)

    def test_archived_terms_are_selectable_but_do_not_override_known_recent_term(self):
        self.add_grade("ARCHIVE", "archive", graded_at="2099-12-31")
        data = self.transcript()
        self.assertEqual(data["transcriptSemesterId"], "RECENT")
        self.assertEqual(data["transcriptSemesters"][-1]["semesterId"], "ARCHIVE")
        self.assertTrue(data["transcriptSemesters"][-1]["archived"])
        self.assertEqual(self.transcript("ARCHIVE")["transcript"][0]["offering_id"], "archive")

    def test_no_ended_terms_returns_empty_and_archived_grades_survive_catalog_outage(self):
        self.db.execute("DELETE FROM course_grades")
        self.catalog["semesters"] = [self.catalog["semesters"][1]]
        data = self.transcript()
        self.assertIsNone(data["transcriptSemesterId"])
        self.assertEqual(data["transcriptSemesters"], [])
        self.assertEqual(data["transcript"], [])
        self.add_grade("ARCHIVE", "archive")
        with patch.object(server, "_fetch_catalog", side_effect=server.ApiError("offline", 503)):
            data = self.transcript()
        self.assertEqual(data["transcriptSemesterId"], "ARCHIVE")
        self.assertEqual(len(data["transcript"]), 1)

    def test_only_own_completed_graded_records_are_returned_and_archived(self):
        self.db.execute("INSERT INTO app_users (user_id, username, password_hash, role) "
                        "VALUES ('OTHER', 'other', 'unused', 'STUDENT')")
        self.db.execute("INSERT INTO students (student_id, user_id, full_name, date_of_birth) "
                        "VALUES ('OTHER', 'OTHER', 'Other', '2000-01-01')")
        self.add_grade("OLD", "other_old", student="OTHER")
        self.add_grade("OTHER_ARCHIVE", "other_archive", student="OTHER")
        self.add_grade("OLD", "dropped", status="DROPPED")
        self.add_grade("OLD", "ungraded", grade=None)
        self.assertEqual([row["offering_id"] for row in self.transcript("OLD")["transcript"]], ["old"])
        with self.assertRaises(server.ApiError):
            self.transcript("OTHER_ARCHIVE")

    def test_http_filters_are_authenticated_and_cannot_override_student_id(self):
        handler = self.handler
        session = ("token", {"user": {"studentId": "S"}})
        for path in ("/api/student/transcript?semester=OLD&studentId=OTHER",
                     "/api/student/data?semester=TERM&transcriptSemester=OLD&studentId=OTHER"):
            handler.path = path
            with patch.object(handler, "_require_session", return_value=session) as auth, \
                    patch.object(handler, "_send_json") as send:
                handler.do_GET()
            auth.assert_called_once()
            self.assertEqual(send.call_args.args[0], 200)
            self.assertEqual([row["offering_id"] for row in send.call_args.args[1]["transcript"]], ["old"])
        handler.path = "/api/student/transcript"
        with patch.object(handler, "_require_session", side_effect=server.ApiError("login", 401)), \
                patch.object(handler, "_send_json") as send, \
                patch.object(server, "_build_student_transcript") as build:
            handler.do_GET()
        self.assertEqual(send.call_args.args[0], 401)
        build.assert_not_called()
