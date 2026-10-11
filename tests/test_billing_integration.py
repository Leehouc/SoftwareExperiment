"""真实本地 HTTP 账单联调：最终课表、金额、重试及接收端持久化幂等。"""

from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from administer import server
from billing.contract import ContractError, validate_bill
from billing.receiver import BillingReceiverHandler, create_server
from tests.test_close_registration import CloseRegistrationTestCase, SqliteConnection


class TestReceiverHandler(BillingReceiverHandler):
    def do_POST(self):
        if self.server.mode == "unavailable":
            self._send_json(503, {"error": "Temporary outage"})
        elif self.server.mode == "reject":
            self._send_json(422, {"error": "Invalid bill"})
        elif self.server.mode == "empty_ack":
            self._send_json(200, {})
        elif self.server.mode == "wrong_ack":
            self._send_json(201, {"schemaVersion": 1, "status": "ACCEPTED", "receiptId": "wrong",
                                  "transactionId": "wrong", "idempotencyKey": "wrong"})
        else:
            super().do_POST()

    def _send_json(self, status, payload):
        if status in (200, 201) and payload.get("status") == "ACCEPTED":
            if self.server.mode == "accepted_then_disconnected":
                # 接收账本已提交，但客户端收不到确认；重试仍不得重复生成账单。
                self.connection.close()
                return
            if self.server.mode == "accepted_then_pending":
                status = 202
        super()._send_json(status, payload)

    def log_message(self, format_string, *args):
        pass


class BillingIntegrationTests(CloseRegistrationTestCase):
    def setUp(self):
        super().setUp()
        self.add_offering("VALID", (1, 2, 3), credits=4)
        self.catalog["departments"] = [{"departmentId": "CS", "name": "计算机科学系"}]
        self.catalog["semesters"] = [{"semesterId": "TERM", "name": "当前学期"}]
        self.catalog["courses"][0].update(title="数据库系统", departmentId="CS")
        self.catalog["courseOfferings"][0].update(
            section="02", days=["TUE", "THU"], startTime="09:00", endTime="10:15", room="科学楼201",
        )
        self.add_offering("CANCELLED", (1, 2, 3), assigned=False)
        self.close_registration()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.ledger_path = str(Path(self.folder.name) / "receiver.sqlite3")
        self.http = None
        self.start_receiver()
        self.addCleanup(self.stop_receiver)

        class QueueConnection(SqliteConnection):
            def close(self):
                pass

        @contextmanager
        def transaction():
            with self.db:
                yield QueueConnection(self.db)

        for target, value in (("get_connection", lambda: QueueConnection(self.db)),
                              ("transaction", transaction)):
            mock = patch.object(server, target, value)
            mock.start()
            self.addCleanup(mock.stop)

    def start_receiver(self):
        self.http = create_server("127.0.0.1", 0, self.ledger_path, TestReceiverHandler)
        self.http.mode = "normal"
        self.thread = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.http.server_port}/api/bills"

    def stop_receiver(self):
        if self.http is not None:
            self.http.shutdown()
            self.http.server_close()
            self.thread.join(timeout=2)
            self.http = None

    def dispatch(self):
        with patch.object(server, "BILLING_API_URL", self.url):
            server._send_pending_billing()

    def queue(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM billing_transactions ORDER BY student_id")]

    def ledger(self):
        with sqlite3.connect(self.ledger_path) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM received_bills ORDER BY transaction_id")]

    def assert_queue(self, status, attempts):
        rows = self.queue()
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["status"] for row in rows}, {status})
        self.assertEqual({row["attempt_count"] for row in rows}, {attempts})
        if status == "SENT":
            self.assertTrue(all(row["last_error"] is None for row in rows))
        else:
            self.assertTrue(all(row["last_error"] for row in rows))

    def post(self, payload, key=None):
        request = Request(self.url, data=json.dumps(payload, ensure_ascii=False).encode(), method="POST",
                          headers={"Content-Type": "application/json",
                                   "Idempotency-Key": key or payload["idempotencyKey"]})
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())

    def test_frozen_amount_currency_and_final_timetable_arrive_at_http_receiver(self):
        # 费率、目录和档案后来改变也不能修改已入队的账单快照。
        self.catalog["courses"][0].update(title="Changed title", credits=9)
        self.db.execute("UPDATE professors SET full_name = 'Changed teacher'")
        self.db.execute("UPDATE students SET full_name = 'Changed student'")
        with patch.object(server, "AMOUNT_CENTS_PER_CREDIT", 99999), \
                patch.object(server, "_fetch_catalog", side_effect=AssertionError("must use frozen payload")):
            self.dispatch()
        self.assert_queue("SENT", 1)
        self.assertEqual(len(self.ledger()), 3)
        expected = {row["transaction_id"]: row for row in self.queue()}
        for bill in self.ledger():
            payload = json.loads(bill["payload_json"])
            row = expected[bill["transaction_id"]]
            validate_bill(payload, bill["idempotency_key"])
            self.assertEqual(payload["amountCents"], row["amount_cents"])
            self.assertEqual(payload["amountCents"], 60000)
            self.assertEqual(payload["currency"], "CNY")
            self.assertEqual(payload["unitAmountCentsPerCredit"], 15000)
            self.assertEqual(payload["totalCredits"], 4)
            self.assertEqual(payload["studentName"], f"Student {row['student_id'][1:]}")
            self.assertEqual(payload["semesterName"], "当前学期")
            self.assertEqual(payload["scheduleStatus"], "FINALIZED")
            self.assertEqual(payload["scheduleId"], int(row["student_id"][1:]))
            self.assertEqual(payload["enrollments"], [{
                "offeringId": "VALID", "courseId": "VALID", "courseTitle": "数据库系统", "credits": 4,
                "amountCents": 60000, "section": "02", "departmentId": "CS", "departmentName": "计算机科学系",
                "professorId": "P", "professorName": "Professor", "days": ["TUE", "THU"],
                "startTime": "09:00", "endTime": "10:15", "room": "科学楼201",
            }])
            self.assertEqual(payload, json.loads(row["payload_json"]))
        self.dispatch()
        self.assert_queue("SENT", 1)
        self.assertEqual(len(self.ledger()), 3)

    def test_transient_http_failure_retries_identical_payload_and_clears_error(self):
        before = [row["payload_json"] for row in self.queue()]
        self.http.mode = "unavailable"
        self.dispatch()
        self.assert_queue("RETRY", 1)
        self.assertEqual(self.ledger(), [])
        self.http.mode = "normal"
        self.dispatch()
        self.assert_queue("SENT", 2)
        self.assertEqual([row["payload_json"] for row in self.queue()], before)
        self.assertEqual(len(self.ledger()), 3)

    def test_lost_confirmation_retries_without_duplicate_bill_even_after_receiver_restart(self):
        self.http.mode = "accepted_then_disconnected"
        self.dispatch()
        self.assert_queue("RETRY", 1)
        before = self.ledger()
        self.assertEqual(len(before), 3)
        self.stop_receiver()
        self.start_receiver()
        self.dispatch()
        self.assert_queue("SENT", 2)
        self.assertEqual(self.ledger(), before)

    def test_queue_update_failure_after_acceptance_is_safe_to_retry(self):
        @contextmanager
        def failed_transaction():
            raise RuntimeError("simulated local commit failure")
            yield

        with patch.object(server, "transaction", failed_transaction), patch("builtins.print"):
            self.dispatch()
        self.assertEqual({row["status"] for row in self.queue()}, {"PENDING"})
        before = self.ledger()
        self.assertEqual(len(before), 3)
        self.dispatch()
        self.assert_queue("SENT", 1)
        self.assertEqual(self.ledger(), before)

    def test_concurrent_duplicate_requests_get_one_durable_bill_and_same_receipt(self):
        payload = json.loads(self.queue()[0]["payload_json"])
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.post(payload), range(6)))
        self.assertEqual([status for status, _ in results].count(201), 1)
        self.assertEqual([status for status, _ in results].count(200), 5)
        self.assertEqual(len({receipt["receiptId"] for _, receipt in results}), 1)
        self.assertEqual(len(self.ledger()), 1)

    def test_same_key_different_bill_is_rejected_and_original_ledger_is_unchanged(self):
        payload = json.loads(self.queue()[0]["payload_json"])
        self.post(payload)
        before = self.ledger()
        changed = copy.deepcopy(payload)
        changed["studentName"] = "Different student"
        with self.assertRaises(HTTPError) as error:
            self.post(changed)
        self.assertEqual(error.exception.code, 409)
        error.exception.close()
        self.assertEqual(self.ledger(), before)

    def test_receiver_rejects_incomplete_wrong_amount_and_mismatched_header(self):
        payload = json.loads(self.queue()[0]["payload_json"])
        for field, value in (("amountCents", 1), ("currency", "USD"), ("schemaVersion", True)):
            with self.subTest(field=field):
                changed = copy.deepcopy(payload)
                changed[field] = value
                with self.assertRaises(HTTPError) as error:
                    self.post(changed)
                self.assertEqual(error.exception.code, 422)
                error.exception.close()
        del payload["enrollments"][0]["professorName"]
        with self.assertRaises(HTTPError) as error:
            self.post(payload)
        self.assertEqual(error.exception.code, 422)
        error.exception.close()
        payload = json.loads(self.queue()[0]["payload_json"])
        with self.assertRaises(HTTPError) as error:
            self.post(payload, key="wrong key")
        self.assertEqual(error.exception.code, 422)
        error.exception.close()
        self.assertEqual(self.ledger(), [])

    def test_2xx_without_matching_acceptance_is_never_marked_sent(self):
        for mode in ("empty_ack", "wrong_ack", "accepted_then_pending"):
            with self.subTest(mode=mode):
                self.http.mode = mode
                self.dispatch()
                self.assert_queue("RETRY", ("empty_ack", "wrong_ack", "accepted_then_pending").index(mode) + 1)
        self.http.mode = "normal"
        self.dispatch()
        self.assert_queue("SENT", 4)
        self.assertEqual(len(self.ledger()), 3)

    def test_permanent_rejection_is_failed_and_not_retried_automatically(self):
        self.http.mode = "reject"
        self.dispatch()
        self.assert_queue("FAILED", 1)
        self.http.mode = "normal"
        self.dispatch()
        self.assert_queue("FAILED", 1)
        self.assertEqual(self.ledger(), [])

    def test_legacy_or_inconsistent_local_payload_fails_without_sending(self):
        for row in self.queue():
            payload = json.loads(row["payload_json"])
            if row["student_id"] == "S1":
                del payload["schemaVersion"]
            elif row["student_id"] == "S2":
                # 合计自洽但与数据库已计算好的金额不同，必须拒发。
                payload["amountCents"] = payload["enrollments"][0]["amountCents"] = 4
                payload["unitAmountCentsPerCredit"] = 1
            else:
                payload["transactionId"] = "wrong transaction"
            self.db.execute("UPDATE billing_transactions SET payload_json = ? WHERE transaction_id = ?",
                            (json.dumps(payload), row["transaction_id"]))
        self.dispatch()
        self.assert_queue("FAILED", 0)
        self.assertEqual(self.ledger(), [])

    def test_no_external_url_preserves_pending_queue_without_claiming_delivery(self):
        before = self.queue()
        with patch.object(server, "BILLING_API_URL", ""), \
                patch.object(server, "get_connection", side_effect=AssertionError("not configured")):
            server._send_pending_billing()
        self.assertEqual(self.queue(), before)
        self.assertEqual(self.ledger(), [])

    def test_duplicate_registration_close_does_not_enqueue_a_second_bill(self):
        before = self.queue()
        with self.assertRaises(server.ApiError):
            self.close_registration()
        self.assertEqual(self.queue(), before)
