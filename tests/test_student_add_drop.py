"""初次提交与加退选的持久化回归测试。"""

from student import server
from tests.test_student_registration_closed import StudentScheduleTestCase


class StudentAddDropTests(StudentScheduleTestCase):
    def setUp(self):
        super().setUp()
        self.db.execute("UPDATE registration_periods SET registration_status = 'OPEN'")
        self.db.commit()

    def submit(self, primary, alternate):
        # 同时经过 HTTP 请求使用的格式、上限和重复课程校验。
        semester, primary, alternate = server._schedule_from_body(
            {"semesterId": "TERM", "primary": primary, "alternate": alternate}, self.catalog,
        )
        self.handler._save_or_submit(True, "S", "S", semester, primary, alternate, self.catalog)

    def enrollment(self, offering_id):
        return dict(self.db.execute("SELECT * FROM enrollments WHERE schedule_id = 1 AND offering_id = ?",
                                    (offering_id,)).fetchone())

    def assert_invalid_submission(self, primary, alternate, message):
        self.db.commit()
        before = list(self.db.iterdump())
        with self.assertRaises(server.ApiError) as error:
            self.submit(primary, alternate)
        self.assertEqual(error.exception.status, 422)
        self.assertTrue(any(message in issue for issue in error.exception.issues), error.exception.issues)
        self.assertEqual(list(self.db.iterdump()), before)

    def test_drop_one_with_optional_alternates_preserves_remaining_enrollments(self):
        before = {id: self.enrollment(id) for id in self.primary[:3]}
        self.submit(self.primary[:3], [])
        self.assertEqual(self.enrollment("O4")["enrollment_status"], "DROPPED")
        for id, row in before.items():
            self.assertEqual(self.enrollment(id), row)
        self.assertEqual([row[0] for row in self.db.execute(
            "SELECT selection_status FROM schedule_choices ORDER BY preference_order",
        )], ["ENROLLED"] * 3)

    def test_full_retained_offering_keeps_original_registration(self):
        for offering in self.catalog["courseOfferings"][:4]:
            offering.update(status="已满", enrolledCount=10)
        self.db.execute("UPDATE enrollments SET enrolled_at = '2001-01-01 10:00:00'")
        self.catalog["courses"][0].update(title="Changed title", credits=6)
        before = self.enrollment("O1")
        self.submit(self.primary[:3], [])
        self.assertEqual(self.enrollment("O1"), before)

    def test_retained_courses_do_not_repeat_prerequisite_or_teacher_validation(self):
        self.catalog["prerequisites"] = [{"courseId": "C1", "prerequisiteCourseId": "MISSING"}]
        self.db.execute("DELETE FROM professor_offering_selections WHERE offering_id = 'O1'")
        self.submit(self.primary[:3], [])
        self.assertEqual(self.enrollment("O1")["enrollment_status"], "ENROLLED")

    def test_new_offering_added_with_fewer_than_four_primary_and_two_alternates(self):
        self.submit(["O1", "O5"], ["O6"])
        self.assertEqual(self.enrollment("O5")["enrollment_status"], "ENROLLED")
        self.assertEqual(self.enrollment("O2")["enrollment_status"], "DROPPED")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM enrollments WHERE offering_id = 'O6'").fetchone()[0], 0)

    def test_new_and_alternate_full_offerings_are_rejected_atomically(self):
        self.catalog["courseOfferings"][4]["status"] = "FULL"
        self.assert_invalid_submission(["O1", "O5"], [], "O5 当前未开放")
        self.assert_invalid_submission(["O1"], ["O5"], "备选开课 O5 当前未开放")
        self.catalog["courseOfferings"][4].update(status="OPEN", enrolledCount=10)
        self.assert_invalid_submission(["O1", "O5"], [], "O5 已满")

    def test_new_prerequisite_and_teacher_requirements_still_apply(self):
        self.catalog["prerequisites"] = [{"courseId": "C5", "prerequisiteCourseId": "MISSING"}]
        self.assert_invalid_submission(["O1", "O5"], [], "缺少先修课程")
        self.catalog["prerequisites"] = []
        self.db.execute("DELETE FROM professor_offering_selections WHERE offering_id = 'O5'")
        self.assert_invalid_submission(["O1", "O5"], [], "尚未确认授课教师")

    def test_added_course_conflict_with_retained_course_rolls_back(self):
        for index in (0, 4):
            self.catalog["courseOfferings"][index].update(days=["MON"], startTime="09:00", endTime="10:00")
        self.assert_invalid_submission(["O1", "O5"], [], "上课时间冲突")

    def test_existing_courses_do_not_repeat_conflict_validation(self):
        for offering in self.catalog["courseOfferings"][:2]:
            offering.update(days=["MON"], startTime="09:00", endTime="10:00")
        self.submit(["O1", "O2"], [])

    def test_drop_all_and_readd_still_counts_as_schedule_adjustment(self):
        self.submit([], [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM enrollments WHERE enrollment_status = 'ENROLLED'").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM schedule_choices").fetchone()[0], 0)
        self.submit(["O1"], [])
        self.assertEqual(self.enrollment("O1")["enrollment_status"], "ENROLLED")

    def test_dropped_course_is_not_exempt_from_validation_when_readded(self):
        self.submit(self.primary[:3], [])
        self.catalog["courseOfferings"][3]["status"] = "FULL"
        self.assert_invalid_submission(self.primary, [], "O4 当前未开放")

    def test_save_draft_keeps_registration_markers_and_does_not_drop_courses(self):
        before = [dict(row) for row in self.db.execute("SELECT * FROM enrollments")]
        self.handler._save_or_submit(False, "S", "S", "TERM", ["O1", "O5"], [], self.catalog)
        self.assertEqual([dict(row) for row in self.db.execute("SELECT * FROM enrollments")], before)
        self.assertEqual({row[0]: row[1] for row in self.db.execute(
            "SELECT offering_id, selection_status FROM schedule_choices",
        )}, {"O1": "ENROLLED", "O5": "SELECTED"})
        self.submit(["O1", "O5"], [])
        self.assertEqual(self.enrollment("O2")["enrollment_status"], "DROPPED")

    def test_repeated_draft_saves_keep_registered_markers_in_both_groups(self):
        before = [dict(row) for row in self.db.execute("SELECT * FROM enrollments")]
        for primary, alternate in ((["O1", "O5"], ["O2", "O6"]),
                                   (["O2", "O5"], ["O1", "O6"]),
                                   (["O1", "O5"], ["O2", "O6"])):
            with self.subTest(primary=primary, alternate=alternate):
                self.handler._save_or_submit(False, "S", "S", "TERM", primary, alternate, self.catalog)
                rows = [tuple(row) for row in self.db.execute(
                    "SELECT offering_id, choice_type, preference_order, selection_status "
                    "FROM schedule_choices ORDER BY choice_type DESC, preference_order",
                )]
                self.assertEqual(rows, [
                    (offering_id, kind, order, "ENROLLED" if offering_id in {"O1", "O2"} else "SELECTED")
                    for kind, ids in (("PRIMARY", primary), ("ALTERNATE", alternate))
                    for order, offering_id in enumerate(ids, start=1)
                ])
                self.assertEqual([dict(row) for row in self.db.execute("SELECT * FROM enrollments")], before)
                self.assertEqual(self.db.execute("SELECT status FROM student_schedules").fetchone()[0], "DRAFT")
                choices = {row["offering_id"]: row["selection_status"] for row in self.student_data()["choices"]}
                self.assertEqual(choices, {"O1": "ENROLLED", "O2": "ENROLLED", "O5": "SELECTED", "O6": "SELECTED"})

        # 只有提交变更才退掉移到备选的课程，新增主选才真正注册。
        self.submit(["O1", "O5"], ["O2", "O6"])
        self.assertEqual(self.enrollment("O1")["enrollment_status"], "ENROLLED")
        self.assertEqual(self.enrollment("O5")["enrollment_status"], "ENROLLED")
        self.assertEqual(self.enrollment("O2")["enrollment_status"], "DROPPED")
        self.assertEqual({row[0]: row[1] for row in self.db.execute(
            "SELECT offering_id, selection_status FROM schedule_choices",
        )}, {"O1": "ENROLLED", "O5": "ENROLLED", "O2": "SELECTED", "O6": "SELECTED"})

    def test_draft_registration_markers_follow_active_enrollments_not_old_choices(self):
        self.db.execute("UPDATE enrollments SET enrollment_status = 'DROPPED' WHERE offering_id = 'O2'")
        self.db.execute("UPDATE enrollments SET enrollment_status = 'CANCELLED' WHERE offering_id = 'O3'")
        self.db.execute("UPDATE enrollments SET enrollment_status = 'COMPLETED' WHERE offering_id = 'O4'")
        self.db.execute("UPDATE schedule_choices SET selection_status = 'SELECTED' WHERE offering_id = 'O1'")
        before = [dict(row) for row in self.db.execute("SELECT * FROM enrollments")]
        self.handler._save_or_submit(False, "S", "S", "TERM", ["O1", "O2"], ["O3", "O4"], self.catalog)
        self.assertEqual({row[0]: row[1] for row in self.db.execute(
            "SELECT offering_id, selection_status FROM schedule_choices",
        )}, {"O1": "ENROLLED", "O2": "SELECTED", "O3": "SELECTED", "O4": "SELECTED"})
        self.assertEqual([dict(row) for row in self.db.execute("SELECT * FROM enrollments")], before)

    def test_initial_draft_has_only_selected_markers(self):
        self.db.execute("DELETE FROM enrollments")
        self.handler._save_or_submit(False, "S", "S", "TERM", ["O1", "O2"], ["O5"], self.catalog)
        self.assertEqual({row[0]: row[1] for row in self.db.execute(
            "SELECT offering_id, selection_status FROM schedule_choices",
        )}, {"O1": "SELECTED", "O2": "SELECTED", "O5": "SELECTED"})
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM enrollments").fetchone()[0], 0)

    def test_first_submission_still_requires_four_plus_two_even_in_add_drop_window(self):
        self.db.execute("DELETE FROM enrollments")
        self.db.execute("UPDATE student_schedules SET status = 'DRAFT'")
        self.assert_invalid_submission(self.primary[:3], self.alternate, "恰好 4 门")
        self.assert_invalid_submission(self.primary, [], "恰好 2 门")
        self.submit(self.primary, self.alternate)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM enrollments WHERE enrollment_status = 'ENROLLED'").fetchone()[0], 4)

    def test_adjustment_still_rejects_duplicates_and_selection_limits(self):
        for primary, alternate in ((["O1", "O1"], []), (["O1"], ["O1"]),
                                   (["O1", "O2", "O3", "O4", "O5"], []),
                                   (["O1"], ["O2", "O3", "O4"])):
            with self.subTest(primary=primary, alternate=alternate):
                before = list(self.db.iterdump())
                with self.assertRaises(server.ApiError):
                    self.submit(primary, alternate)
                self.assertEqual(list(self.db.iterdump()), before)
