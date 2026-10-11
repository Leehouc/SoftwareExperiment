"""学生课程目录中的实际教师和动态人数回归测试。"""

from student import server
from tests.test_student_registration_closed import StudentScheduleTestCase


class StudentCatalogTests(StudentScheduleTestCase):
    def test_actual_teacher_comes_from_active_local_assignment(self):
        self.catalog["faculty"] = [{"facultyId": "LEGACY", "name": "Legacy teacher"}]
        self.catalog["courseOfferings"][0]["facultyId"] = "LEGACY"
        data = server._build_student_data("S", "TERM")
        self.assertEqual(data["teachingAssignments"]["O1"], {
            "professorId": "P", "fullName": "Professor", "selectionStatus": "SELECTED",
        })

    def test_withdrawn_inactive_and_other_term_assignments_are_excluded(self):
        self.db.execute("UPDATE professor_offering_selections SET selection_status = 'WITHDRAWN' WHERE offering_id = 'O1'")
        self.db.execute("UPDATE professor_offering_selections SET semester_id = 'OTHER' WHERE offering_id = 'O2'")
        data = server._build_student_data("S", "TERM")
        self.assertNotIn("O1", data["teachingAssignments"])
        self.assertNotIn("O2", data["teachingAssignments"])
        self.db.execute("UPDATE professors SET status = 'INACTIVE' WHERE professor_id = 'P'")
        self.assertEqual(server._build_student_data("S", "TERM")["teachingAssignments"], {})

    def test_teacher_takeover_and_enrollment_changes_are_visible_on_next_read(self):
        self.db.execute("INSERT INTO app_users (user_id, username, password_hash, role) VALUES ('Q', 'teacherQ', 'unused', 'PROFESSOR')")
        self.db.execute("INSERT INTO professors (professor_id, user_id, full_name, date_of_birth, department_id) "
                        "VALUES ('Q', 'Q', 'New teacher', '1980-01-01', 'CS')")
        initial = server._build_student_data("S", "TERM")
        self.assertEqual(initial["offeringCounts"]["O1"], 1)
        self.db.execute("UPDATE professor_offering_selections SET professor_id = 'Q' WHERE offering_id = 'O1'")
        self.db.execute("UPDATE enrollments SET enrollment_status = 'DROPPED' WHERE offering_id = 'O1'")
        updated = server._build_student_data("S", "TERM")
        self.assertEqual(updated["teachingAssignments"]["O1"]["fullName"], "New teacher")
        self.assertNotIn("O1", updated["offeringCounts"])
