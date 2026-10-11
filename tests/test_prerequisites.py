"""通过成绩、学生提交和关闭注册补位的一致性回归测试。"""

from datetime import date, timedelta

from database.academic_rules import passed_courses_by_student
from student import server as student_server
from teacher import server as teacher_server
from tests.test_close_registration import CloseRegistrationTestCase
from tests.test_teacher_roster import TeacherRosterTestCase


def add_attempt(db, schedule_id, offering_id, grade=None, status="COMPLETED"):
    db.execute("INSERT INTO professor_offering_selections "
               "(professor_id, offering_id, semester_id) VALUES ('P', ?, 'PAST')", (offering_id,))
    cursor = db.execute("INSERT INTO enrollments "
                        "(schedule_id, offering_id, course_id, course_title_snapshot, credits_snapshot, enrollment_status) "
                        "VALUES (?, ?, 'PRE', 'Prerequisite', 3, ?)", (schedule_id, offering_id, status))
    if grade:
        db.execute("INSERT INTO course_grades (enrollment_id, professor_id, letter_grade) VALUES (?, 'P', ?)",
                   (cursor.lastrowid, grade))
    return cursor.lastrowid


class StudentPrerequisiteTests(TeacherRosterTestCase):
    def setUp(self):
        super().setUp()
        self.db.execute("UPDATE registration_periods SET registration_status = 'OPEN'")
        self.db.execute("INSERT INTO student_schedules (schedule_id, student_id, semester_id) VALUES (2, 'S', 'PAST')")
        self.history_id = add_attempt(self.db, 2, "PRE_O", status="ENROLLED")
        self.catalog["semesters"].append({"semesterId": "PAST",
                                          "endDate": (date.today() - timedelta(days=1)).isoformat()})
        self.catalog["prerequisites"] = [
            {"courseId": "C1", "prerequisiteCourseId": "PRE"},
            {"courseId": "C5", "prerequisiteCourseId": "PRE"},
        ]
        self.db.commit()

    def grade_prerequisite(self, grade):
        teacher_server._save_grades("P", "P", "PRE_O", {str(self.history_id): grade}, self.catalog)

    def issues(self):
        issues, _ = student_server._validate_submission(
            self.connection, "S", 1, "TERM", self.primary, self.alternate, self.catalog,
        )
        return issues

    def test_all_allowed_grades_use_explicit_passing_standard_for_primary_and_alternate(self):
        for grade in ("A", "B", "C", "D", "F", "I"):
            with self.subTest(grade=grade):
                self.grade_prerequisite(grade)
                issues = self.issues()
                if grade in {"A", "B", "C", "D"}:
                    self.assertEqual(issues, [])
                else:
                    self.assertEqual(len(issues), 2)
                    self.assertTrue(all("缺少先修课程" in issue for issue in issues))
                    self.assertTrue(any("备选课程 C5" in issue for issue in issues))

    def test_actual_initial_submission_blocks_failed_and_incomplete_grades_atomically(self):
        self.db.execute("DELETE FROM enrollments WHERE schedule_id = 1")
        for grade in ("F", "I"):
            self.grade_prerequisite(grade)
            before = list(self.db.iterdump())
            with self.subTest(grade=grade), self.assertRaises(student_server.ApiError) as error:
                self.mutate("submit")
            self.assertEqual(error.exception.status, 422)
            self.assertEqual(list(self.db.iterdump()), before)
        self.grade_prerequisite("D")
        self.mutate("submit")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM enrollments WHERE schedule_id = 1 "
                                         "AND enrollment_status = 'ENROLLED'").fetchone()[0], 4)

    def test_add_drop_new_course_also_requires_passing_grade(self):
        self.grade_prerequisite("F")
        before = list(self.db.iterdump())
        with self.assertRaises(student_server.ApiError) as error:
            self.handler._save_or_submit(True, "S", "S", "TERM", ["O2", "O5"], [], self.catalog)
        self.assertEqual(error.exception.status, 422)
        self.assertEqual(list(self.db.iterdump()), before)
        self.grade_prerequisite("C")
        self.handler._save_or_submit(True, "S", "S", "TERM", ["O2", "O5"], [], self.catalog)
        self.assertEqual(self.db.execute("SELECT enrollment_status FROM enrollments "
                                         "WHERE schedule_id = 1 AND offering_id = 'O5'").fetchone()[0], "ENROLLED")

    def test_completed_without_grade_is_not_a_pass(self):
        self.db.execute("UPDATE enrollments SET enrollment_status = 'COMPLETED' WHERE enrollment_id = ?",
                        (self.history_id,))
        self.assertEqual(len(self.issues()), 2)

    def test_passing_grade_does_not_count_for_uncompleted_dropped_or_cancelled_record(self):
        self.grade_prerequisite("A")
        for status in ("ENROLLED", "DROPPED", "CANCELLED"):
            with self.subTest(status=status):
                self.db.execute("UPDATE enrollments SET enrollment_status = ? WHERE enrollment_id = ?",
                                (status, self.history_id))
                self.assertEqual(len(self.issues()), 2)

    def test_another_students_pass_does_not_satisfy_current_student(self):
        self.grade_prerequisite("F")
        self.db.execute("INSERT INTO app_users (user_id, username, password_hash, role) "
                        "VALUES ('OTHER', 'other', 'unused', 'STUDENT')")
        self.db.execute("INSERT INTO students (student_id, user_id, full_name, date_of_birth) "
                        "VALUES ('OTHER', 'OTHER', 'Other', '2000-01-01')")
        self.db.execute("INSERT INTO student_schedules (schedule_id, student_id, semester_id) "
                        "VALUES (3, 'OTHER', 'PAST')")
        add_attempt(self.db, 3, "OTHER_PRE", "A")
        self.assertEqual(len(self.issues()), 2)
        self.assertEqual(passed_courses_by_student(self.connection, "S"), {})

    def test_any_passing_retake_satisfies_course_once(self):
        self.grade_prerequisite("F")
        add_attempt(self.db, 2, "RETAKE_PRE", "B")
        add_attempt(self.db, 2, "ANOTHER_PRE", "I")
        self.assertEqual(self.issues(), [])
        self.assertEqual(passed_courses_by_student(self.connection, "S"), {"S": {"PRE"}})

    def test_grade_revisions_change_eligibility_and_keep_failed_incomplete_on_report_card(self):
        for grade, eligible in (("A", True), ("F", False), ("I", False), ("D", True)):
            with self.subTest(grade=grade):
                self.grade_prerequisite(grade)
                self.assertEqual(not self.issues(), eligible)
                report = self.db.execute("SELECT letter_grade FROM v_student_report_cards "
                                         "WHERE offering_id = 'PRE_O'").fetchone()
                self.assertEqual(report[0], grade)


class ClosePrerequisiteTests(CloseRegistrationTestCase):
    def prepare_replacement(self, grade=None, status="COMPLETED"):
        self.add_four_primaries()
        self.db.execute("UPDATE professor_offering_selections SET selection_status = 'WITHDRAWN' "
                        "WHERE offering_id = 'INVALID_PRIMARY'")
        self.add_alternate("NEEDS_PRE", 1)
        self.add_alternate("FALLBACK", 2)
        self.catalog["prerequisites"] = [{"courseId": "NEEDS_PRE", "prerequisiteCourseId": "PRE"}]
        for i in range(1, 4):
            self.db.execute("INSERT INTO student_schedules (schedule_id, student_id, semester_id) "
                            "VALUES (?, ?, 'PAST')", (i + 3, f"S{i}"))
            add_attempt(self.db, i + 3, f"PRE_O{i}", grade, status)

    def assert_grade_replacement(self, grade, selected):
        self.prepare_replacement(grade)
        self.close_registration()
        self.assert_replacement(selected)

    def test_a_passes_for_alternate_replacement(self):
        self.assert_grade_replacement("A", "NEEDS_PRE")

    def test_b_passes_for_alternate_replacement(self):
        self.assert_grade_replacement("B", "NEEDS_PRE")

    def test_c_passes_for_alternate_replacement(self):
        self.assert_grade_replacement("C", "NEEDS_PRE")

    def test_d_passes_for_alternate_replacement(self):
        self.assert_grade_replacement("D", "NEEDS_PRE")

    def test_f_skips_alternate_and_uses_next_choice(self):
        self.assert_grade_replacement("F", "FALLBACK")

    def test_i_skips_alternate_and_uses_next_choice(self):
        self.assert_grade_replacement("I", "FALLBACK")

    def test_missing_grade_skips_alternate(self):
        self.assert_grade_replacement(None, "FALLBACK")

    def test_dropped_passing_course_skips_alternate(self):
        self.prepare_replacement("A", "DROPPED")
        self.close_registration()
        self.assert_replacement("FALLBACK")

    def test_passing_retake_allows_alternate_after_previous_failure(self):
        self.prepare_replacement("F")
        for i in range(1, 4):
            add_attempt(self.db, i + 3, f"RETAKE{i}", "D")
        self.close_registration()
        self.assert_replacement("NEEDS_PRE")

    def test_grade_revision_to_incomplete_removes_eligibility_before_closing(self):
        self.prepare_replacement("A")
        self.db.execute("UPDATE course_grades SET letter_grade = 'I'")
        self.close_registration()
        self.assert_replacement("FALLBACK")
