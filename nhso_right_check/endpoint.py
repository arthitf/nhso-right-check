"""Client ปิดสิทธิผ่าน NHSO ENDPOINT FOR SINGLE CLAIM (DataSet v8.0)

- แฟม 6  NHSO Claim Detail            -> ส่งยืนยันปิดสิทธิ
- แฟม 7  NHSO Claim Detail Response   -> seq, authenCode, dataError
- แฟม 8  NHSO Claim Detail Cancel     -> seq, transactionId, paymentStatus=CANCEL
- แฟม 9  Cancel Response              -> status, authenCode, dataError

Authorization: Bearer <TOKEN KEY ที่ สปสช. ส่งให้ทางอีเมล> (ใช้ได้เฉพาะ HCODE ที่ขอ)
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import requests

from .config import Settings
from .validation import ValidationError, mask_pid, require_hcode, require_pid, to_amount

log = logging.getLogger(__name__)

VALID_MAININSCL = {"WEL", "UCS", "OFC", "LGO", "SSS", "OTH", "COR", "NULL"}


class EndpointError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


def to_epoch_ms(dt: datetime | int | float | str | None) -> int:
    """แปลงวันเวลาเป็น unix timestamp (ms) — รับ datetime, ตัวเลข ms, หรือ 'YYYY-MM-DD HH:MM[:SS]'"""
    if dt is None or dt == "":
        return int(time.time() * 1000)
    if isinstance(dt, (int, float)):
        return int(dt)
    if isinstance(dt, str):
        s = dt.strip()
        if s.isdigit():
            return int(s)
        s = s.replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValidationError(f"รูปแบบวันเวลาไม่ถูกต้อง: {s!r} (ใช้ YYYY-MM-DD HH:MM:SS ปี ค.ศ.)")
        if dt.year > 2400:
            raise ValidationError("ปีต้องเป็น ค.ศ. ไม่ใช่ พ.ศ.")
    return int(dt.timestamp() * 1000)


def new_transaction_id(hcode: str) -> str:
    """transactionId = HCODE + System Generated No (Unique)"""
    return f"{hcode}{datetime.now():%y%m%d%H%M%S}{random.randint(0, 999):03d}"


@dataclass
class ClaimRequest:
    pid: str
    claim_service_code: str          # เช่น PG0060001
    main_inscl_code: str             # จากผลตรวจสอบสิทธิ SRM
    privilege_amount: Any            # ค่าบริการที่เบิกได้
    paid_amount: Any = 0             # ค่าบริการที่เบิกไม่ได้ (ผู้ป่วยชำระเอง)
    total_amount: Any = None         # ถ้าไม่ระบุ = paid + privilege
    service_datetime: Any = None
    invoice_datetime: Any = None
    visit_number: str = ""
    department_code: str = ""
    department_name: str = ""
    transaction_id: str = ""
    extra: dict = field(default_factory=dict)


def build_claim_payload(req: ClaimRequest, s: Settings) -> dict:
    """สร้าง JSON แฟม 6 พร้อมตรวจความถูกต้องตาม spec"""
    hcode = require_hcode(s.hcode)
    pid = require_pid(req.pid)
    recorder = require_pid(s.recorder_pid, "recorderPid (RECORDER_PID)")
    if not s.source_id:
        raise ValidationError("ต้องตั้งค่า SOURCE_ID")
    if not req.claim_service_code:
        raise ValidationError("ต้องระบุ claimServiceCode (รหัสบริการ เช่น PG0060001)")
    inscl = (req.main_inscl_code or "").upper()
    if inscl not in VALID_MAININSCL:
        raise ValidationError(f"mainInsclCode ไม่ถูกต้อง: {req.main_inscl_code!r}")

    paid = to_amount(req.paid_amount, "paidAmount")
    privilege = to_amount(req.privilege_amount, "privilegeAmount")
    total = to_amount(req.total_amount, "totalAmount") if req.total_amount not in (None, "") else paid + privilege
    if total != paid + privilege:
        log.warning("totalAmount (%s) ไม่เท่ากับ paid+privilege (%s)", total, paid + privilege)

    now_ms = int(time.time() * 1000)
    service_ms = to_epoch_ms(req.service_datetime)
    invoice_ms = to_epoch_ms(req.invoice_datetime)
    if service_ms > now_ms + 60_000:
        raise ValidationError("serviceDateTime ต้องไม่เกินวันเวลาปัจจุบัน")
    if invoice_ms > now_ms + 60_000:
        raise ValidationError("invoiceDateTime ต้องไม่เกินวันเวลาปัจจุบัน")

    payload: dict[str, Any] = {
        "hcode": hcode,
        "mainInsclCode": inscl,
        "serviceDateTime": service_ms,
        "invoiceDateTime": invoice_ms,
        "transactionId": req.transaction_id or new_transaction_id(hcode),
        "totalAmount": float(total),
        "paidAmount": float(paid),
        "privilegeAmount": float(privilege),
        "claimServiceCode": req.claim_service_code.strip(),
        "pid": pid,
        "sourceId": s.source_id,
        "recorderPid": recorder,
    }
    if req.visit_number:
        payload["visitNumber"] = str(req.visit_number)[:30]
    if req.department_code or req.department_name:
        payload["department"] = {"code": req.department_code, "name": req.department_name}
    payload.update(req.extra or {})
    return payload


class EndpointClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.s = settings
        self.http = session or requests.Session()

    def _url(self, path: str) -> str:
        return self.s.endpoint_base.rstrip("/") + "/" + path.lstrip("/")

    def _headers(self) -> dict:
        if not self.s.endpoint_token:
            raise EndpointError("ยังไม่ได้ตั้งค่า NHSO_ENDPOINT_TOKEN (TOKEN KEY จาก สปสช.)")
        return {"Authorization": f"Bearer {self.s.endpoint_token}",
                "Content-Type": "application/json", "Accept": "application/json"}

    def _send(self, method: str, path: str, payload: dict) -> dict:
        try:
            r = self.http.request(method, self._url(path), json=payload,
                                  headers=self._headers(), timeout=self.s.timeout)
        except requests.Timeout as e:
            # timeout ตอนปิดสิทธิ: ฝั่ง สปสช. อาจบันทึกไปแล้ว — ห้ามส่งซ้ำทันที ให้ตรวจสอบก่อน
            raise EndpointError(f"หมดเวลารอ (timeout) transactionId={payload.get('transactionId')} "
                                "— อาจบันทึกสำเร็จแล้ว ตรวจสอบก่อนส่งซ้ำ") from e
        except requests.RequestException as e:
            raise EndpointError(f"เชื่อมต่อ NHSO ENDPOINT ไม่ได้: {type(e).__name__}") from e
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text}
        if r.status_code >= 400 and not (isinstance(body, dict) and body.get("dataError")):
            raise EndpointError(f"HTTP {r.status_code}", r.status_code, body)
        if isinstance(body, dict):
            body.setdefault("_httpStatus", r.status_code)
        return body

    def claim(self, payload: dict) -> dict:
        """ส่งยืนยันปิดสิทธิ (แฟม 6) คืน {seq, authenCode, dataError}"""
        log.info("ปิดสิทธิ %s trans=%s", mask_pid(payload.get("pid", "")), payload.get("transactionId"))
        return self._send("POST", self.s.endpoint_claim_path, payload)

    def cancel(self, seq: int | str, transaction_id: str) -> dict:
        """ยกเลิกการปิดสิทธิ (แฟม 8) คืน {status, authenCode, dataError}"""
        payload = {"seq": int(seq), "transactionId": str(transaction_id), "paymentStatus": "CANCEL"}
        log.info("ยกเลิกปิดสิทธิ seq=%s trans=%s", seq, transaction_id)
        return self._send(self.s.endpoint_cancel_method, self.s.endpoint_cancel_path, payload)


def is_success(resp: dict) -> bool:
    """สำเร็จเมื่อไม่มี dataError และ (มี authenCode หรือ status = Y)"""
    if not isinstance(resp, dict):
        return False
    if (resp.get("dataError") or "").strip():
        return False
    if "status" in resp and str(resp["status"]).upper() == "N":
        return False
    return bool(resp.get("authenCode") or str(resp.get("status", "")).upper() == "Y")
