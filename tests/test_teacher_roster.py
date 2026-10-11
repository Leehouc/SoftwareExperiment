"""教师名册查看与成绩录入权限的持久化回归测试。"""

from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import patch

from teacher import server
from tests.test_student_registration_closed import StudentScheduleTestCase


class TeacherRosterTests(StudentScheduleTestCase):
    def setUp(self):
        super().setUp()
        self.catalog["semesters"][0].update(
            name="Current term", endDate=(date.today() + timedelta(days=30)).isoformat(),
        )

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

    def roster(self, offering_id="O1", professor_id="P"):
        return server._get_roster(professor_id, offering_id, self.catalog)

    def save_grade(self, grade="A", professor_id="P", offering_id="O1", enrollment_id="1"):
        return server._save_grades("P", professor_id, offering_id, {enrollment_id: grade}, self.catalog)

    def end_term(self):
        self.catalog["semesters"][0]["endDate"] = (date.today() - timedelta(days=1)).isoformat()

    def assert_grade_rejected(self, status, **kwargs):
        self.db.commit()
        before = list(self.db.iterdump())
        with self.assertRaises(server.ApiError) as error:
            self.save_grade(**kwargs)
        self.assertEqual(error.exception.status, status)
        self.assertEqual(list(self.db.iterdump()), before)

    def test_current_term_roster_is_visible_even_after_registration_closes(self):
        self.assertEqual(self.db.execute("SELECT registration_status FROM registration_periods").fetchone()[0], "CLOSED")
        before = list(self.db.iterdump())
        result = self.roster()
        self.assertEqual(result["semesterId"], "TERM")
        self.assertEqual([row["student_id"] for row in result["roster"]], ["S"])
        self.assertFalse(result["canEditGrades"])
        self.assertEqual(list(self.db.iterdump()), before)

    def test_current_term_grades_are_rejected_without_writes(self):
        self.assert_grade_rejected(409)

    def test_term_ending_today_still_allows_only_roster_view(self):
        self.catalog["semesters"][0]["endDate"] = date.today().isoformat()
        self.assertFalse(self.roster()["canEditGrades"])
        self.assert_grade_rejected(409)

    def test_finished_term_allows_grade_entry_and_revision(self):
        self.end_term()
        self.assertTrue(self.roster()["canEditGrades"])
        self.assertEqual(self.save_grade(), 1)
        result = self.roster()
        self.assertEqual(result["roster"][0]["enrollment_status"], "COMPLETED")
        self.assertEqual(result["roster"][0]["letter_grade"], "A")
        self.assertEqual(self.save_grade("B"), 1)
        self.assertEqual(self.roster()["roster"][0]["letter_grade"], "B")

    def test_unassigned_or_withdrawn_teacher_cannot_view_or_grade(self):
        self.end_term()
        with self.assertRaises(server.ApiError) as error:
            self.roster(professor_id="OTHER")
        self.assertEqual(error.exception.status, 403)
        self.assert_grade_rejected(403, professor_id="OTHER")
        self.db.execute("UPDATE professor_offering_selections SET selection_status = 'WITHDRAWN' WHERE offering_id = 'O1'")
        with self.assertRaises(server.ApiError) as error:
            self.roster()
        self.assertEqual(error.exception.status, 403)
        self.assert_grade_rejected(403)

    def test_roster_excludes_dropped_cancelled_other_offerings_and_other_terms(self):
        self.db.execute("INSERT INTO student_schedules (schedule_id, student_id, semester_id) VALUES (2, 'S', 'OTHER')")
        self.db.execute("INSERT INTO enrollments (schedule_id, offering_id, course_id, course_title_snapshot, credits_snapshot) "
                        "VALUES (2, 'O1', 'C1', 'Other term', 3)")
        for status in ("DROPPED", "CANCELLED"):
            self.db.execute("UPDATE enrollments SET enrollment_status = ? WHERE enrollment_id = 1", (status,))
            self.assertEqual(self.roster()["roster"], [])
        self.db.execute("UPDATE enrollments SET enrollment_status = 'ENROLLED' WHERE enrollment_id = 1")
        self.assertEqual([row["enrollment_id"] for row in self.roster()["roster"]], [1])

    def test_empty_current_roster_is_allowed_and_readonly(self):
        result = self.roster("O5")
        self.assertEqual(result["roster"], [])
        self.assertFalse(result["canEditGrades"])

    def test_page_exposes_current_rosters_but_only_finished_grade_courses(self):
        self.catalog["semesters"].append({"semesterId": "PAST", "name": "Past term",
                                          "endDate": (date.today() - timedelta(days=30)).isoformat()})
        self.db.execute("INSERT INTO professor_offering_selections "
                        "(professor_id, offering_id, semester_id) VALUES ('P', 'PAST_O', 'PAST')")
        self.db.execute("UPDATE professor_offering_selections SET selection_status = 'WITHDRAWN' WHERE offering_id = 'O6'")
        data = server._build_teacher_data("P", "TERM")
        self.assertEqual({item["offeringId"] for item in data["rosterCourses"]},
                         {"O1", "O2", "O3", "O4", "O5", "PAST_O"})
        self.assertEqual([item["offeringId"] for item in data["gradeCourses"]], ["PAST_O"])
        for item in data["rosterCourses"]:
            if item["offeringId"] != "PAST_O":
                self.assertFalse(item["canEditGrades"])

    def test_historical_term_outside_catalog_retains_existing_grade_policy(self):
        self.catalog["semesters"] = []
        self.assertFalse(self.roster()["canEditGrades"])
        self.assert_grade_rejected(409)
        self.db.execute("UPDATE enrollments SET enrollment_status = 'COMPLETED' WHERE enrollment_id = 1")
        self.db.commit()
        self.assertTrue(self.roster()["canEditGrades"])
        self.assertEqual(self.save_grade(), 1)

    def test_completed_record_does_not_allow_early_grading_when_term_is_known(self):
        self.db.execute("UPDATE enrollments SET enrollment_status = 'COMPLETED' WHERE enrollment_id = 1")
        self.assertFalse(self.roster()["canEditGrades"])
        self.assert_grade_rejected(409)

    def test_finished_term_cannot_grade_another_offerings_student(self):
        self.end_term()
        self.assert_grade_rejected(403, enrollment_id="2")
