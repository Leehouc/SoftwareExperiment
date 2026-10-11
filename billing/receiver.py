"""可独立启动的本地账单参考接收端，持久化确认及幂等记录。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from billing.contract import ContractError, MAX_PAYLOAD_BYTES, SCHEMA_VERSION, validate_bill


class IdempotencyConflict(ValueError):
    pass


def initialize_ledger(path: str) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS received_bills ("
            "receipt_id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL UNIQUE, "
            "idempotency_key TEXT NOT NULL UNIQUE, payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL, "
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )


def receive_bill(path: str, key: str, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    validate_bill(payload, key)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    receipt_id = "RCPT-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32].upper()
    with sqlite3.connect(path, timeout=8) as db:
        # 先锁定再查询/插入，使并发重复请求也只登记一笔账单。
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT receipt_id, payload_hash FROM received_bills WHERE idempotency_key = ?", (key,),
        ).fetchone()
        if existing:
            if existing[1] != fingerprint:
                raise IdempotencyConflict("同一幂等键不能提交不同账单内容")
            receipt_id = existing[0]
        else:
            try:
                db.execute("INSERT INTO received_bills VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                           (receipt_id, payload["transactionId"], key, fingerprint, canonical))
            except sqlite3.IntegrityError as exc:
                raise IdempotencyConflict("交易号已经由其他幂等键登记") from exc
    return {
        "schemaVersion": SCHEMA_VERSION, "status": "ACCEPTED", "receiptId": receipt_id,
        "transactionId": payload["transactionId"], "idempotencyKey": key,
    }, existing is not None


class BillingReceiverHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        self._send_json(200 if self.path == "/health" else 404,
                        {"service": "billing-reference-receiver", "schemaVersion": SCHEMA_VERSION})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/bills":
            self._send_json(404, {"error": "找不到账单接收路径"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_PAYLOAD_BYTES:
                raise ContractError("请求长度必须在 1 至 65536 字节之间")
            if self.headers.get_content_type() != "application/json":
                self._send_json(415, {"error": "只支持 application/json"})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            key = self.headers.get("Idempotency-Key", "")
            receipt, duplicate = receive_bill(self.server.ledger_path, key, payload)
        except IdempotencyConflict as exc:
            self._send_json(409, {"error": str(exc)})
            return
        except (ContractError, ValueError, UnicodeDecodeError) as exc:
            self._send_json(422, {"error": str(exc)})
            return
        except sqlite3.Error:
            self._send_json(503, {"error": "账单账本暂时不可用，请重试"})
            return
        self._send_json(200 if duplicate else 201, receipt)

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[billing] {format_string % args}")


def create_server(host: str, port: int, ledger_path: str,
                  handler: type[BillingReceiverHandler] = BillingReceiverHandler) -> ThreadingHTTPServer:
    initialize_ledger(ledger_path)
    server = ThreadingHTTPServer((host, port), handler)
    server.ledger_path = ledger_path
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--db", default="data/billing-receiver.sqlite3", help="接收账本文件（独立于注册业务数据库）")
    args = parser.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    server = create_server(args.host, args.port, args.db)
    print(f"参考账单接收端：http://{args.host}:{args.port}/api/bills")
    print(f"接收账本：{Path(args.db).resolve()}；此服务用于契约联调，不执行真实扣款。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
