"""เชื่อมต่อ NHSO Secure SmartCard Agent v2.0 (เสียบบัตรผู้ป่วย → อ่านบัตร + ตรวจสิทธิ → ปิดสิทธิ)

Agent ของ สปสช. ติดตั้งเป็น Windows Service บนเครื่องหน่วยบริการ ทำงานที่ http://localhost:8189
TOKEN ของหน่วยบริการตั้งไว้ในตัว Agent เอง (userconfig.properties) — โปรแกรมนี้ไม่ต้องใช้ SRM token

API ที่ใช้ (คู่มือ NHSO Secure SmartCard Agent v2.0, secureagent 1.2.4 Production):
  GET  /api/smartcard/read?readImageFlag=true          อ่านบัตร + ตรวจสอบสิทธิ (ได้ correlationId, claimTypes)
  GET  /api/smartcard/read-card-only?readImageFlag=true อ่านเฉพาะข้อมูลในบัตร (ไม่ต่อ สปสช.)
  GET  /api/nhso-service/latest-authen-code/{pid}       authen code ล่าสุดที่หน่วยบริการเคยขอ
  POST /api/nhso-service/save-endpoint                  ปิดสิทธิ → {seq, authenCode}
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import requests

from .validation import ValidationError, mask_pid, require_pid, to_amount

log = logging.getLogger(__name__)

DEFAULT_AGENT_URL = "http://localhost:8189"
MOBILE_RE = re.compile(r"^\d{9,12}$")


class AgentError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


def normalize_agent_url(url: str) -> str:
    url = (url or DEFAULT_AGENT_URL).strip().rstrip("/")
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValidationError("ที่อยู่ Agent ต้องเป็น http://เครื่อง:พอร์ต เช่น http://localhost:8189")
    return url


# ข้อความ error จาก Agent (Spring) → ภาษาไทยที่ผู้ใช้เข้าใจ
_KNOWN = [
    ("NoCardAttached", "ยังไม่ได้เสียบบัตรประชาชน (หรือบัตรเสียบไม่สนิท)"),
    ("NoReaderAttached", "ไม่พบเครื่องอ่านบัตร — ตรวจสาย USB / ไดรเวอร์"),
    ("NoDriverSupport", "ไดรเวอร์เครื่องอ่านบัตรไม่รองรับ"),
    ("NhsoServiceUnavailable", "Agent เชื่อมต่อ สปสช. ไม่ได้ — ตรวจอินเทอร์เน็ต/proxy ของเครื่อง"),
    ("ResourceNotFound", "ไม่พบข้อมูล"),
    ("PKIX path", "Agent ต่อ สปสช. (authenservice.nhso.go.th) ไม่ได้ เพราะไม่เชื่อใบรับรอง SSL — มักเกิดจาก "
                  "firewall/proxy ขององค์กรที่ตรวจ HTTPS (SSL inspection) · ทดลองต่อเน็ตอื่น (เช่น hotspot มือถือ) "
                  "หรือให้ IT ยกเว้น *.nhso.go.th จาก SSL inspection / นำ root CA ขององค์กรเข้า Java ของ Agent "
                  "แล้ว restart Agent"),
    ("SSLHandshake", "Agent ต่อ สปสช. แบบ HTTPS ไม่สำเร็จ (SSL handshake) — ตรวจ firewall/proxy ขององค์กร "
                     "และวันเวลาของเครื่อง แล้ว restart Agent"),
    ("UnknownHost", "Agent หาเซิร์ฟเวอร์ สปสช. ไม่เจอ — ตรวจอินเทอร์เน็ต/DNS ของเครื่อง"),
]


def _error_text(r: requests.Response) -> str:
    text = r.text or ""
    try:
        body = r.json()
    except ValueError:
        body = None
    if r.status_code in (401, 403) or ("sign in" in text.lower() and "<html" in text.lower()):
        return "Agent ยังไม่ได้ตั้ง TOKEN ของหน่วยบริการ — ใส่ใน userconfig.properties แล้ว restart Agent"
    blob = " ".join(str(v) for v in (body.values() if isinstance(body, dict) else [text]))
    for key, th in _KNOWN:
        if key.lower() in blob.lower():
            return th
    if isinstance(body, dict):
        msg = body.get("message") or body.get("error") or body.get("rsMessage")
        if msg:
            return f"Agent แจ้ง: {msg}"
    return f"Agent ตอบกลับ HTTP {r.status_code}"


class AgentClient:
    def __init__(self, base_url: str = DEFAULT_AGENT_URL, timeout: float = 30.0,
                 session: requests.Session | None = None):
        self.base = normalize_agent_url(base_url)
        self.timeout = timeout
        self.http = session or requests.Session()
        # Agent อยู่ในเครื่อง/เครือข่ายภายใน — ไม่ส่งผ่าน proxy ของเครื่อง
        self.http.trust_env = False

    def _get(self, path: str, params: dict | None = None, timeout: float | None = None) -> requests.Response:
        try:
            return self.http.get(self.base + path, params=params, timeout=timeout or self.timeout,
                                 headers={"Accept": "application/json"})
        except requests.ConnectionError as e:
            raise AgentError(f"ไม่พบ Agent ที่ {self.base} — ติดตั้ง/เปิด NHSOSecureSmartCardAgent ก่อน "
                             "(Windows Services หรือ start-no-service.bat)") from e
        except requests.Timeout as e:
            raise AgentError("Agent ไม่ตอบสนอง (timeout)") from e

    def _json(self, r: requests.Response) -> dict:
        if r.status_code >= 400:
            raise AgentError(_error_text(r), r.status_code, r.text[:2000])
        try:
            data = r.json()
        except ValueError as e:
            raise AgentError(_error_text(r), r.status_code, r.text[:2000]) from e
        if not isinstance(data, dict):
            raise AgentError("Agent ตอบกลับรูปแบบไม่ถูกต้อง", r.status_code, data)
        return data

    # ---------- API ----------
    def ping(self) -> dict:
        """ตรวจว่า Agent ทำงานอยู่ไหม (ไม่ต้องเสียบบัตร)"""
        r = self._get("/actuator/health", timeout=4)
        if r.status_code == 404:
            r = self._get("/", timeout=4)
        ok = r.status_code < 500
        info: dict[str, Any] = {"online": ok, "url": self.base, "http": r.status_code}
        try:
            body = r.json()
            if isinstance(body, dict) and body.get("status"):
                info["status"] = body["status"]
        except ValueError:
            pass
        return info

    def read(self, image: bool = True) -> dict:
        """อ่านบัตร + ตรวจสอบสิทธิ (Response #1)"""
        data = self._json(self._get("/api/smartcard/read", {"readImageFlag": str(bool(image)).lower()}))
        log.info("Agent read %s", mask_pid(str(data.get("pid", ""))))
        return data

    def read_card_only(self, image: bool = True) -> dict:
        """อ่านเฉพาะข้อมูลในบัตร ไม่ต่อ สปสช. (Response #2) — ใช้ตอนเชื่อมต่อ สปสช. ไม่ได้"""
        return self._json(self._get("/api/smartcard/read-card-only", {"readImageFlag": str(bool(image)).lower()}))

    def latest_authen(self, pid: str) -> dict:
        pid = require_pid(pid)
        r = self._get(f"/api/nhso-service/latest-authen-code/{pid}")
        if r.status_code == 404:
            return {}
        return self._json(r)

    def save_endpoint(self, payload: dict) -> dict:
        """ปิดสิทธิ (Request #3 → Response #6: seq, authenCode)"""
        try:
            r = self.http.post(self.base + "/api/nhso-service/save-endpoint", json=payload, timeout=self.timeout,
                               headers={"Accept": "application/json", "Content-Type": "application/json"})
        except requests.ConnectionError as e:
            raise AgentError(f"ไม่พบ Agent ที่ {self.base}") from e
        except requests.Timeout as e:
            raise AgentError("Agent ไม่ตอบสนอง (timeout) — อาจปิดสิทธิสำเร็จแล้ว ตรวจด้วยปุ่ม "
                             "“authen code ล่าสุด” ก่อนส่งซ้ำ") from e
        data = self._json(r)
        log.info("Agent save-endpoint %s -> authen=%s", mask_pid(payload.get("pid", "")), data.get("authenCode"))
        return data


def build_endpoint_payload(*, pid: str, correlation_id: str, claim_type: str, privilege_amount: Any,
                           paid_amount: Any = 0, invoice_datetime: str | datetime | None = None,
                           mobile: str = "", visit_number: str = "") -> dict:
    """สร้าง Request #3 ของ /api/nhso-service/save-endpoint พร้อมตรวจความถูกต้อง"""
    pid = require_pid(pid)
    if not (correlation_id or "").strip():
        raise ValidationError("ไม่มี correlationId — ต้องอ่านบัตร + ตรวจสิทธิผ่าน Agent ก่อนปิดสิทธิ")
    claim_type = (claim_type or "").strip().upper()
    if not claim_type:
        raise ValidationError("กรุณาเลือกประเภทบริการ (claimType)")
    if isinstance(invoice_datetime, datetime):
        dt = invoice_datetime
    elif invoice_datetime:
        s = str(invoice_datetime).replace("T", " ").strip()
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            raise ValidationError("วันเวลาเบิกไม่ถูกต้อง (YYYY-MM-DD HH:MM:SS ปี ค.ศ.)")
    else:
        dt = datetime.now()
    if dt.year > 2400:
        raise ValidationError("ปีต้องเป็น ค.ศ.")
    if (dt - datetime.now()).total_seconds() > 60:
        raise ValidationError("วันเวลาเบิกต้องไม่เกินเวลาปัจจุบัน")
    mobile = re.sub(r"[\s-]", "", mobile or "")
    if mobile and not MOBILE_RE.match(mobile):
        raise ValidationError("เบอร์มือถือต้องเป็นตัวเลข 9–12 หลัก")
    payload = {
        "pid": pid,
        "invoiceDate": dt.strftime("%Y-%m-%d"),
        "invoiceTime": dt.strftime("%H:%M:%S"),
        "paidAmount": float(to_amount(paid_amount or 0, "ค่าบริการที่เบิกไม่ได้")),
        "privilegeAmount": float(to_amount(privilege_amount, "ค่าบริการที่เบิกได้")),
        "claimType": claim_type,
        "correlationId": correlation_id.strip(),
    }
    if mobile:
        payload["mobile"] = mobile
    if visit_number:
        payload["visitNumber"] = str(visit_number).strip()[:30]
    return payload


SEX = {"1": "ชาย", "2": "หญิง"}


def summarize_card(d: dict) -> dict:
    """รวม Response #1 / #2 ให้เป็นรูปแบบเดียว (ไม่รวมรูป)"""
    sex = str(d.get("sex") or d.get("gender") or "")
    return {
        "pid": d.get("pid", ""),
        "name": " ".join(x for x in (d.get("titleName") or d.get("titleTh"), d.get("fname") or d.get("firstnameTh"),
                                     d.get("lname") or d.get("lastnameTh")) if x),
        "sex": SEX.get(sex, sex),
        "birthDate": d.get("birthDate", ""),
        "age": d.get("age", ""),
        "nation": d.get("nation", ""),
        "mainInscl": d.get("mainInscl") or "",
        "subInscl": d.get("subInscl") or "",
        "deathDate": d.get("deathDate") or "",
        "transDate": d.get("transDate") or "",
        "checkDate": d.get("checkDate") or "",
        "startDateTime": d.get("startDateTime") or "",
        "expireDateTime": d.get("expireDateTime") or "",
        "paidModel": d.get("paidModel") or "",
        "hospMainOp": d.get("hospMainOp") or {},
        "hospSub": d.get("hospSub") or {},
        "hospMain": d.get("hospMain") or {},
        "claimTypes": [{"code": c.get("claimType", ""), "name": c.get("claimTypeName", "")}
                       for c in (d.get("claimTypes") or []) if isinstance(c, dict)],
        "correlationId": d.get("correlationId") or "",
    }
