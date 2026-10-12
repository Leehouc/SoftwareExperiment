"""最终关闭须等待注册与加退选窗口结束，拒绝请求不得改变业务数据。"""

from datetime import date, datetime, timedelta
from unittest.mock import patch
import unittest

from administer import server
from tests.test_close_registration import CloseRegistrationTestCase
from tests.test_student_registration_closed import StudentConnection
from tests import test_session_security as security


class CloseRegistrationTimingTests(CloseRegistrationTestCase):
    def setUp(self):
        super().setUp()
        self.add_offering("VALID", (1, 2, 3))
        self.db.commit()

    def window(self, registration_start, registration_end, add_drop_start, add_drop_end):
        dates = [(date.today() + timedelta(days=offset)).isoformat()
                 for offset in (registration_start, registration_end, add_drop_start, add_drop_end)]
        self.db.execute("UPDATE registration_periods SET registration_start_at = ?, registration_end_at = ?, "
                        "add_drop_start_at = ?, add_drop_end_at = ?", dates)
        self.db.commit()

    def assert_rejected_without_changes(self):
        before = list(self.db.iterdump())
        with self.assertRaises(server.ApiError) as error:
            self.close_registration()
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(list(self.db.iterdump()), before)
        return str(error.exception)

    def test_registration_and_add_drop_deadline_days_still_reject_closing(self):
        for windows in ((-10, 0, -10, -1), (-10, -1, -5, 0), (-10, 0, -5, 0)):
            with self.subTest(windows=windows):
                self.window(*windows)
                self.assertIn("尚未全部结束", self.assert_rejected_without_changes())

    def test_registration_in_progress_is_rejected_even_if_add_drop_ended(self):
        self.window(-10, 5, -10, -1)
        self.assert_rejected_without_changes()

    def test_add_drop_in_progress_is_rejected_after_initial_registration_deadline(self):
        self.window(-10, -5, -2, 2)
        self.assert_rejected_without_changes()

    def test_gap_before_future_add_drop_does_not_prematurely_close(self):
        self.window(-10, -5, 2, 5)
        self.assert_rejected_without_changes()

    def test_not_started_registration_is_rejected(self):
        self.window(1, 5, 6, 10)
        self.assert_rejected_without_changes()

    def test_day_after_last_deadline_closes_with_billing(self):
        self.window(-20, -10, -5, -1)
        self.close_registration()
        self.assert_finalized()
        self.assert_billing({"VALID"}, 45000)

    def test_late_settlement_has_no_artificial_upper_deadline(self):
        self.window(-100, -90, -80, -70)
        self.close_registration()
        self.assert_finalized()

    def test_registration_end_can_be_the_later_deadline(self):
        self.window(-20, -1, -10, -5)
        self.close_registration()
        self.assert_finalized()

    def test_closed_period_cannot_repeat_finalization_or_billing(self):
        self.close_registration()
        self.assertIn("不能重复结算", self.assert_rejected_without_changes())

    def test_admin_data_matches_locked_write_permission_and_first_allowed_date(self):
        self.catalog["semesters"] = [{"semesterId": "TERM", "name": "Term"}]
        connection = StudentConnection(self.db)
        for windows, allowed in (((-10, -5, -2, 2), False), ((-10, -5, -2, -1), True)):
            self.window(*windows)
            with patch.object(server, "get_connection", return_value=connection):
                period = server._record_data(self.catalog)["periods"][0]
            self.assertEqual(period["canClose"], allowed)
            self.assertEqual(period["closeAvailableOn"],
                             (date.today() + timedelta(days=max(windows[1], windows[3]) + 1)).isoformat())
            if not allowed:
                self.assertEqual(period["closeReason"], self.assert_rejected_without_changes())


class CloseStateValidationTests(unittest.TestCase):
    def period(self):
        return {"registration_status": "OPEN", "registration_start_at": "2026-01-01",
                "registration_end_at": "2026-01-10", "add_drop_start_at": "2026-01-15", "add_drop_end_at": "2026-01-20"}

    def test_explicit_date_boundary_and_database_date_types(self):
        period = self.period()
        self.assertFalse(server._registration_close_state(period, today=date(2026, 1, 20))["canClose"])
        self.assertTrue(server._registration_close_state(period, today=date(2026, 1, 21))["canClose"])
        period["registration_start_at"] = datetime(2026, 1, 1)
        period["registration_end_at"] = date(2026, 1, 10)
        self.assertTrue(server._registration_close_state(period, today=date(2026, 1, 21))["canClose"])

    def test_bad_missing_or_reversed_dates_fail_closed(self):
        for key, value in (("registration_end_at", None), ("add_drop_end_at", "bad"),
                           ("registration_start_at", "2026-02-01"), ("add_drop_start_at", "2026-02-01"),
                           ("add_drop_end_at", "9999-12-31")):
            with self.subTest(key=key, value=value):
                period = {**self.period(), key: value}
                self.assertFalse(server._registration_close_state(period)["canClose"])


class CloseTimingHttpTests(unittest.TestCase):
    def test_direct_http_close_rechecks_dates_even_if_client_claims_eligible(self):
        fixture = security.SessionSecurityTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.catalog["departments"] = []
        fixture.db.execute("UPDATE registration_periods SET registration_status = 'OPEN'")
        fixture.db.commit()
        cookie, csrf = fixture.login("admin")
        with patch.object(server, "_fetch_catalog", return_value=fixture.catalog):
            before = list(fixture.db.iterdump())
            status, body, _ = fixture.http("admin", "/api/admin/registration/close",
                {"semesterId": "TERM", "confirm": True, "canClose": True}, cookie, csrf)
            self.assertEqual(status, 409)
            self.assertIn("尚未全部结束", body["error"])
            self.assertEqual(list(fixture.db.iterdump()), before)
            fixture.db.execute("UPDATE registration_periods SET registration_start_at = ?, registration_end_at = ?, "
                               "add_drop_start_at = ?, add_drop_end_at = ?",
                               ((date.today() - timedelta(days=10)).isoformat(),
                                (date.today() - timedelta(days=1)).isoformat()) * 2)
            fixture.db.commit()
            self.assertTrue(fixture.http("admin", "/api/admin/data", cookie=cookie)[1]["periods"][0]["canClose"])
            status, _, _ = fixture.http("admin", "/api/admin/registration/close",
                {"semesterId": "TERM", "confirm": True}, cookie, csrf)
            self.assertEqual(status, 200)
            before = list(fixture.db.iterdump())
            self.assertEqual(fixture.http("admin", "/api/admin/registration/close",
                {"semesterId": "TERM", "confirm": True}, cookie, csrf)[0], 409)
            self.assertEqual(list(fixture.db.iterdump()), before)
