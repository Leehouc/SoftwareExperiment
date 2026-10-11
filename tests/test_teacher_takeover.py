"""撤回班次接手、当前唯一归属及审计历史的持久化回归测试。"""

import json
import sqlite3

from teacher import server
from tests.test_teacher_roster import TeacherRosterTestCase


class TeacherTakeoverTests(TeacherRosterTestCase):
    def setUp(self):
        super().setUp()
        self.db.execute("UPDATE registration_periods SET registration_status = 'OPEN'")
        for professor_id in ("Q", "T"):
            self.db.execute("INSERT INTO app_users (user_id, username, password_hash, role) "
                            "VALUES (?, ?, 'unused', 'PROFESSOR')", (professor_id, professor_id))
            self.db.execute("INSERT INTO professors "
                            "(professor_id, user_id, full_name, date_of_birth, department_id) "
                            "VALUES (?, ?, ?, '1980-01-01', 'CS')", (professor_id,) * 3)
        self.db.executemany("INSERT INTO professor_course_eligibility (professor_id, course_id) VALUES (?, ?)",
                            [(professor_id, f"C{i}") for professor_id in ("P", "Q", "T") for i in range(1, 7)])
        self.db.commit()

    def select(self, professor_id, offering_ids):
        server._save_teaching_selection(professor_id, professor_id, "TERM", offering_ids, self.catalog)

    def release(self, offering_id="O1"):
        self.db.execute("UPDATE professor_offering_selections SET selection_status = 'WITHDRAWN', "
                        "selected_at = '2001-01-01 10:00:00' WHERE offering_id = ?", (offering_id,))
        self.db.commit()

    def assignment(self, offering_id="O1"):
        return dict(self.db.execute("SELECT * FROM professor_offering_selections WHERE offering_id = ?",
                                    (offering_id,)).fetchone())

    def history(self):
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT details_json FROM audit_logs WHERE action = 'UPDATE_TEACHING_SELECTION' ORDER BY audit_id",
        )]

    def assert_selection_rejected(self, professor_id, offering_ids, status=422, issue=None):
        self.db.commit()
        before = list(self.db.iterdump())
        with self.assertRaises(server.ApiError) as error:
            self.select(professor_id, offering_ids)
        self.assertEqual(error.exception.status, status)
        if issue:
            self.assertTrue(any(issue in message for message in error.exception.issues), error.exception.issues)
        self.assertEqual(list(self.db.iterdump()), before)

    def test_released_offering_is_selectable_on_other_teacher_page(self):
        self.release()
        data = server._build_teacher_data("Q", "TERM")
        offers = {item["offeringId"]: item for item in data["eligibleOfferings"]}
        self.assertFalse(offers["O1"]["reservedByOther"])
        self.assertFalse(offers["O1"]["selected"])
        self.assertTrue(offers["O2"]["reservedByOther"])

    def test_takeover_reuses_unique_record_and_preserves_previous_assignment_in_audit(self):
        self.release()
        before = self.assignment()
        self.select("Q", ["O1"])
        after = self.assignment()
        self.assertEqual(after["professor_id"], "Q")
        self.assertEqual(after["selection_status"], "SELECTED")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM professor_offering_selections").fetchone()[0], 6)
        history = self.history()[0]
        self.assertEqual(history["professorId"], "Q")
        self.assertEqual(history["assignmentChanges"], [{
            "offeringId": "O1",
            "before": {"professorId": "P", "semesterId": "TERM", "selectionStatus": "WITHDRAWN",
                       "selectedAt": before["selected_at"]},
            "after": {"professorId": "Q", "semesterId": "TERM", "selectionStatus": "SELECTED",
                      "selectedAt": after["selected_at"]},
        }])

    def test_active_owner_blocks_takeover_for_selected_and_confirmed(self):
        for status in ("SELECTED", "CONFIRMED"):
            self.db.execute("UPDATE professor_offering_selections SET selection_status = ? WHERE offering_id = 'O1'", (status,))
            self.assert_selection_rejected("Q", ["O1"], issue="已由其他教师占用")

    def test_withdraw_then_multiple_teachers_take_over_retains_complete_history(self):
        self.select("P", ["O2", "O3", "O4", "O5", "O6"])
        self.select("Q", ["O1"])
        self.select("Q", [])
        self.select("T", ["O1"])
        transitions = [entry["assignmentChanges"][0] for entry in self.history()]
        self.assertEqual([(item["before"]["professorId"], item["before"]["selectionStatus"],
                           item["after"]["professorId"], item["after"]["selectionStatus"])
                          for item in transitions], [
            ("P", "SELECTED", "P", "WITHDRAWN"), ("P", "WITHDRAWN", "Q", "SELECTED"),
            ("Q", "SELECTED", "Q", "WITHDRAWN"), ("Q", "WITHDRAWN", "T", "SELECTED"),
        ])
        self.assertEqual(self.assignment()["professor_id"], "T")

    def test_same_teacher_can_restore_withdrawn_record(self):
        self.release()
        self.select("P", self.primary + self.alternate)
        self.assertEqual(self.assignment()["selection_status"], "SELECTED")
        self.assertEqual(len(self.history()[0]["assignmentChanges"]), 1)

    def test_unchanged_active_assignments_keep_confirmation_and_registration_time(self):
        self.db.execute("UPDATE professor_offering_selections SET selection_status = 'CONFIRMED', "
                        "selected_at = '2001-01-01 10:00:00' WHERE offering_id = 'O1'")
        before = self.assignment()
        self.select("P", self.primary + self.alternate)
        self.assertEqual(self.assignment(), before)
        self.assertEqual(self.history()[0]["assignmentChanges"], [])

    def test_ineligible_teacher_cannot_take_over_released_offering(self):
        self.release()
        self.db.execute("DELETE FROM professor_course_eligibility WHERE professor_id = 'Q' AND course_id = 'C1'")
        self.assert_selection_rejected("Q", ["O1"], issue="没有资格")

    def test_closed_registration_and_conflicts_still_block_takeover(self):
        self.release()
        self.db.execute("UPDATE registration_periods SET registration_status = 'CLOSED'")
        self.assert_selection_rejected("Q", ["O1"], status=409)
        self.db.execute("UPDATE registration_periods SET registration_status = 'OPEN'")
        self.release("O2")
        for offering in self.catalog["courseOfferings"][:2]:
            offering.update(days=["MON"], startTime="09:00", endTime="10:00")
        self.assert_selection_rejected("Q", ["O1", "O2"], issue="时间冲突")

    def test_failed_batch_does_not_transfer_or_withdraw_any_offering(self):
        self.release()
        self.db.execute("UPDATE professor_offering_selections SET professor_id = 'Q' WHERE offering_id = 'O5'")
        self.assert_selection_rejected("Q", ["O1", "O2"], issue="已由其他教师占用")

    def test_old_teacher_loses_access_and_existing_students_and_grades_remain(self):
        self.end_term()
        self.save_grade()
        self.release()
        before_enrollments = [dict(row) for row in self.db.execute("SELECT * FROM enrollments")]
        before_grades = [dict(row) for row in self.db.execute("SELECT * FROM course_grades")]
        self.select("Q", ["O1"])
        self.assertEqual([dict(row) for row in self.db.execute("SELECT * FROM enrollments")], before_enrollments)
        self.assertEqual([dict(row) for row in self.db.execute("SELECT * FROM course_grades")], before_grades)
        with self.assertRaises(server.ApiError) as error:
            self.roster(professor_id="P")
        self.assertEqual(error.exception.status, 403)
        self.assert_grade_rejected(403)
        self.assertEqual(self.roster(professor_id="Q")["roster"][0]["student_id"], "S")

    def test_second_teacher_cannot_take_over_after_first_teacher_claims_offering(self):
        self.release()
        self.select("Q", ["O1"])
        self.assert_selection_rejected("T", ["O1"], issue="已由其他教师占用")
        self.assertEqual(self.assignment()["professor_id"], "Q")
        self.assertTrue(any("FROM registration_periods" in sql and "FOR UPDATE" in sql
                            for sql in self.connection.queries))
        self.assertTrue(any("FROM professor_offering_selections WHERE semester_id" in sql and "FOR UPDATE" in sql
                            for sql in self.connection.queries))

    def test_audit_failure_rolls_back_takeover_and_preserves_old_owner(self):
        self.release()
        self.db.execute("CREATE TRIGGER reject_teaching_audit BEFORE INSERT ON audit_logs "
                        "BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
        self.db.commit()
        before = list(self.db.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            self.select("Q", ["O1"])
        self.assertEqual(list(self.db.iterdump()), before)

    def test_new_assignment_records_initial_state_in_history(self):
        self.db.execute("DELETE FROM professor_offering_selections WHERE offering_id = 'O5'")
        self.db.commit()
        self.select("Q", ["O5"])
        change = self.history()[0]["assignmentChanges"][0]
        self.assertIsNone(change["before"])
        self.assertEqual(change["after"]["professorId"], "Q")
