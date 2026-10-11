"""注册关闭账单 HTTP 契约；发送端与参考接收端使用同一份校验。"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1
CURRENCY = "CNY"
MAX_PAYLOAD_BYTES = 64 * 1024


class ContractError(ValueError):
    pass


def _text(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} 必须是非空字符串")


def _integer(value: Any, field: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ContractError(f"{field} 必须是大于等于 {minimum} 的整数")


def validate_bill(payload: Any, idempotency_key: str) -> None:
    if not isinstance(payload, dict) or type(payload.get("schemaVersion")) is not int or payload["schemaVersion"] != SCHEMA_VERSION:
        raise ContractError("账单必须采用 schemaVersion=1；旧版队列需核对最终课表后迁移")
    if payload.get("eventType") != "REGISTRATION_FINALIZED" or payload.get("scheduleStatus") != "FINALIZED":
        raise ContractError("只接收 REGISTRATION_FINALIZED / FINALIZED 最终课表")
    for field in ("transactionId", "idempotencyKey", "studentId", "studentName", "semesterId", "semesterName"):
        _text(payload.get(field), field)
    if payload["idempotencyKey"] != idempotency_key:
        raise ContractError("Idempotency-Key 请求头与账单不一致")
    if payload.get("currency") != CURRENCY:
        raise ContractError("当前契约仅支持 CNY，金额单位为人民币分")
    for field in ("amountCents", "unitAmountCentsPerCredit"):
        _integer(payload.get(field), field)
    for field in ("scheduleId", "totalCredits"):
        _integer(payload.get(field), field, 1)
    entries = payload.get("enrollments")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 4:
        raise ContractError("enrollments 必须包含 1 至 4 门最终注册课程")
    offering_ids = set()
    course_ids = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ContractError("课程明细必须是对象")
        for field in ("offeringId", "courseId", "courseTitle", "professorId", "professorName"):
            _text(entry.get(field), f"enrollments.{field}")
        if entry["offeringId"] in offering_ids or entry["courseId"] in course_ids:
            raise ContractError("最终课表不能包含重复开课或课程")
        offering_ids.add(entry["offeringId"])
        course_ids.add(entry["courseId"])
        for field in ("section", "departmentId", "departmentName", "startTime", "endTime", "room"):
            if field not in entry or (entry[field] is not None and not isinstance(entry[field], str)):
                raise ContractError(f"enrollments.{field} 必须是字符串或 null")
        days = entry.get("days")
        if not isinstance(days, list) or any(not isinstance(day, str) for day in days):
            raise ContractError("enrollments.days 必须是字符串数组")
        _integer(entry.get("credits"), "enrollments.credits", 1)
        _integer(entry.get("amountCents"), "enrollments.amountCents")
        if entry["amountCents"] != entry["credits"] * payload["unitAmountCentsPerCredit"]:
            raise ContractError("课程金额与学分及冻结费率不一致")
    if payload["totalCredits"] != sum(entry["credits"] for entry in entries):
        raise ContractError("totalCredits 与课程学分合计不一致")
    if payload["amountCents"] != sum(entry["amountCents"] for entry in entries):
        raise ContractError("amountCents 与课程金额合计不一致")


def validate_receipt(receipt: Any, transaction_id: str, idempotency_key: str) -> None:
    if not isinstance(receipt, dict) or type(receipt.get("schemaVersion")) is not int or receipt["schemaVersion"] != SCHEMA_VERSION:
        raise ContractError("账单接收端没有返回 v1 确认")
    if receipt.get("status") != "ACCEPTED":
        raise ContractError("账单接收端尚未确认接受账单")
    if receipt.get("transactionId") != transaction_id or receipt.get("idempotencyKey") != idempotency_key:
        raise ContractError("账单接收确认的交易号或幂等键不匹配")
    _text(receipt.get("receiptId"), "receiptId")
