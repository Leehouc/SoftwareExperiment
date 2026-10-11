"""教师资格维护的原子性、真实 HTTP 权限及教师授课流程回归。"""

import json
import unittest
from unittest.mock import patch

from administer import server as admin
from teacher import server as teacher
from tests.test_teacher_roster import TeacherRosterTestCase
from tests import test_session_security as security


class ProfessorEligibilityTests(TeacherRosterTestCase):
    def setUp(self):
        super().setUp()
        self.catalog.update(departments=[{"departmentId": "CS", "name": "CS"},
                                         {"departmentId": "MATH", "name": "Math"}], faculty=[])
        self.catalog["courses"][1]["departmentId"] = "MATH"
        self.catalog["courseOfferings"].append({"offeringId": "FREE", "courseId": "C1", "semesterId": "TERM",
                                               "status": "OPEN", "capacity": 10, "days": []})
        self.db.execute("UPDATE registration_periods SET registration_status = 'OPEN'")
        self.db.execute("INSERT INTO app_users (user_id, username, password_hash, role) "
                        "VALUES ('R', 'registrar', 'unused', 'REGISTRAR')")
        self.db.execute("INSERT INTO registrars (registrar_id, user_id, full_name) VALUES ('R', 'R', 'Registrar')")
        self.db.executemany("INSERT INTO professor_course_eligibility VALUES ('P', ?)", [(f"C{i}",) for i in range(1, 7)])
        self.db.commit()
        self.session = {"user": {"userId": "R"}}
        for target, replacement in (("get_connection", lambda: self.connection),
                                    ("transaction", teacher.transaction), ("_fetch_catalog", lambda: self.catalog)):
            mock = patch.object(admin, target, replacement)
            mock.start()
            self.addCleanup(mock.stop)

    def body(self, **updates):
        return {"fullName": "New Teacher", "dateOfBirth": "1980-01-01", "username": "new.teacher",
                "password": "TeacherPassword1!", "departmentId": "CS", "status": "ACTIVE", **updates}

    def update(self, course_ids=None, **updates):
        body = self.body(**{"username": "professor", "password": "", **updates})
        if course_ids is not None:
            body["eligibleCourseIds"] = course_ids
        admin._update_profile("professor", "P", body, self.session, self.catalog)

    def ids(self, professor="P"):
        return [row[0] for row in self.db.execute("SELECT course_id FROM professor_course_eligibility "
                                                 "WHERE professor_id = ? ORDER BY course_id", (professor,))]

    def test_creation_configures_cross_department_qualification_and_teacher_can_login_and_select(self):
        result = admin._create_profile("professor", self.body(eligibleCourseIds=["C2", "C1", "C1"]), self.session, self.catalog)
        self.assertEqual(self.ids(result["id"]), ["C1", "C2"])
        _, user = teacher._do_login("new.teacher", "TeacherPassword1!")
        self.addCleanup(teacher._sessions.clear)
        self.assertEqual(user["professorId"], result["id"])
        data = teacher._build_teacher_data(result["id"], "TERM")
        self.assertIn("FREE", [row["offeringId"] for row in data["eligibleOfferings"]])
        teacher._save_teaching_selection(result["id"], result["id"], "TERM", ["FREE"], self.catalog)
        self.assertEqual(self.db.execute("SELECT professor_id FROM professor_offering_selections "
                                         "WHERE offering_id = 'FREE'").fetchone()[0], result["id"])

    def test_record_data_returns_complete_eligibility_including_archived_course(self):
        self.db.execute("INSERT INTO professor_course_eligibility VALUES ('P', 'ARCHIVE')")
        data = admin._record_data(self.catalog)
        self.assertEqual(data["professors"][0]["eligibleCourseIds"], ["ARCHIVE"] + [f"C{i}" for i in range(1, 7)])

    def test_omitted_update_field_preserves_qualifications_but_empty_list_clears_them(self):
        self.update()
        self.assertEqual(len(self.ids()), 6)
        self.update([])
        self.assertEqual(self.ids(), [])
        events = list(self.db.execute("SELECT details_json FROM audit_logs WHERE action = 'UPDATE_PROFESSOR_ELIGIBILITY'"))
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0][0]), {"before": [f"C{i}" for i in range(1, 7)], "after": []})

    def test_idempotent_sets_do_not_duplicate_rows_or_eligibility_audit(self):
        self.update(["C2", "C1", "C2"])
        self.update(["C1", "C2"])
        self.assertEqual(self.ids(), ["C1", "C2"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM audit_logs "
                                         "WHERE action = 'UPDATE_PROFESSOR_ELIGIBILITY'").fetchone()[0], 1)

    def test_invalid_new_course_rolls_back_new_account_and_profile(self):
        before = list(self.db.iterdump())
        with self.assertRaises(admin.ApiError):
            admin._create_profile("professor", self.body(eligibleCourseIds=["UNKNOWN"]), self.session, self.catalog)
        self.assertEqual(list(self.db.iterdump()), before)

    def test_invalid_update_rolls_back_identity_password_and_eligibility(self):
        before = list(self.db.iterdump())
        with self.assertRaises(admin.ApiError):
            self.update(["UNKNOWN"], fullName="Changed", password="NewPassword2!")
        self.assertEqual(list(self.db.iterdump()), before)

    def test_invalid_array_inputs_do_not_modify_any_records(self):
        for ids in (None, "C1", {}, [1], [""], [" "], ["C" * 33]):
            before = list(self.db.iterdump())
            with self.subTest(ids=ids), self.assertRaises(admin.ApiError):
                admin._create_profile("professor", self.body(eligibleCourseIds=ids), self.session, self.catalog)
            self.assertEqual(list(self.db.iterdump()), before)

    def test_existing_archived_qualification_can_be_retained_or_removed_but_not_granted(self):
        self.db.execute("INSERT INTO professor_course_eligibility VALUES ('P', 'ARCHIVE')")
        self.db.commit()
        self.update(["C1", "ARCHIVE"])
        self.assertEqual(self.ids(), ["ARCHIVE", "C1"])
        self.update(["C1"])
        before = list(self.db.iterdump())
        with self.assertRaises(admin.ApiError):
            self.update(["C1", "ARCHIVE"])
        self.assertEqual(list(self.db.iterdump()), before)

    def test_revoke_qualification_retains_existing_teaching_but_blocks_new_and_reselected_offerings(self):
        assignments = list(self.db.execute("SELECT * FROM professor_offering_selections"))
        self.update(["C2", "C3", "C4", "C5", "C6"])
        self.assertEqual(list(self.db.execute("SELECT * FROM professor_offering_selections")), assignments)
        data = teacher._build_teacher_data("P", "TERM")
        offers = {row["offeringId"]: row for row in data["eligibleOfferings"]}
        self.assertTrue(offers["O1"]["retainedWithoutEligibility"])
        self.assertNotIn("FREE", offers)
        teacher._save_teaching_selection("P", "P", "TERM", self.primary + self.alternate, self.catalog)
        self.assertEqual([row["student_id"] for row in self.roster()["roster"]], ["S"])
        with self.assertRaises(teacher.ApiError):
            teacher._save_teaching_selection("P", "P", "TERM", ["FREE"], self.catalog)
        teacher._save_teaching_selection("P", "P", "TERM", ["O2", "O3", "O4", "O5", "O6"], self.catalog)
        with self.assertRaises(teacher.ApiError):
            teacher._save_teaching_selection("P", "P", "TERM", ["O1"], self.catalog)

    def test_empty_initial_qualification_is_explicit_pending_configuration(self):
        result = admin._create_profile("professor", self.body(), self.session, self.catalog)
        self.assertEqual(self.ids(result["id"]), [])
        self.assertEqual(teacher._build_teacher_data(result["id"], "TERM")["eligibleOfferings"], [])

    def test_audit_failure_rolls_back_all_qualification_and_profile_changes(self):
        before = list(self.db.iterdump())
        with patch.object(admin, "_insert_audit", side_effect=RuntimeError("audit failed")), self.assertRaises(RuntimeError):
            self.update(["C1"], fullName="Changed")
        self.assertEqual(list(self.db.iterdump()), before)

    def test_logical_delete_keeps_profile_eligibility_and_student_history_but_releases_assignments(self):
        before = list(self.db.execute("SELECT * FROM enrollments"))
        admin._deactivate_profile("professor", "P", self.session)
        self.assertEqual(len(self.ids()), 6)
        self.assertEqual(self.db.execute("SELECT status FROM professors WHERE professor_id = 'P'").fetchone()[0], "INACTIVE")
        self.assertEqual(list(self.db.execute("SELECT * FROM enrollments")), before)
        self.assertEqual({row[0] for row in self.db.execute("SELECT selection_status FROM professor_offering_selections")}, {"WITHDRAWN"})
        self.assertTrue(json.loads(self.db.execute("SELECT details_json FROM audit_logs "
                                                   "WHERE action = 'DEACTIVATE_PROFESSOR_PROFILE'").fetchone()[0])["softDelete"])


class EligibilityHttpTests(unittest.TestCase):
    def setUp(self):
        self.fixture = security.SessionSecurityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.catalog.update(departments=[{"departmentId": "CS", "name": "CS"}], faculty=[])
        mock = patch.object(admin, "_fetch_catalog", lambda: self.fixture.catalog)
        mock.start()
        self.addCleanup(mock.stop)

    def test_http_creation_and_update_require_registrar_and_csrf_and_persist_qualifications(self):
        body = {"fullName": "New", "dateOfBirth": "1980-01-01", "username": "new.teacher",
                "password": "TeacherPassword1!", "departmentId": "CS", "eligibleCourseIds": ["C1"]}
        http = self.fixture.http
        before = list(self.fixture.db.iterdump())
        self.assertEqual(http("admin", "/api/admin/professors", body)[0], 401)
        student_cookie, csrf = self.fixture.login("student")
        self.assertEqual(http("admin", "/api/admin/professors", body, student_cookie, csrf)[0], 401)
        cookie, csrf = self.fixture.login("admin")
        self.assertEqual(http("admin", "/api/admin/professors", body, cookie)[0], 403)
        self.assertEqual(list(self.fixture.db.iterdump()), before)
        status, result, _ = http("admin", "/api/admin/professors", body, cookie, csrf)
        self.assertEqual(status, 201)
        professor_id = result["record"]["id"]
        body["eligibleCourseIds"] = ["C2"]
        status, _, _ = http("admin", f"/api/admin/professors/{professor_id}", body, cookie, csrf, method="PUT")
        self.assertEqual(status, 200)
        data = http("admin", "/api/admin/data", cookie=cookie)[1]
        self.assertEqual(next(p for p in data["professors"] if p["id"] == professor_id)["eligibleCourseIds"], ["C2"])
