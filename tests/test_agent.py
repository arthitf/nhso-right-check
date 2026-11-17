"""ทดสอบการเชื่อมต่อ NHSO Secure SmartCard Agent (ใช้ Agent จำลอง ไม่ต้องมีเครื่องอ่านบัตร)"""
import json
from datetime import datetime, timedelta

import pytest
import requests

from nhso_right_check.agent import (AgentClient, AgentError, build_endpoint_payload, normalize_agent_url,
                                    summarize_card)
from nhso_right_check.config import Settings
from nhso_right_check.validation import ValidationError

PID = "1101700203450"

# ตัวอย่างตาม Response #1 ในคู่มือ Agent v2.0
READ = {
    "pid": PID, "titleName": "นาย", "fname": "ทดสอบ", "lname": "ระบบ", "nation": "099", "birthDate": "25360115",
    "sex": "1", "transDate": "2024-10-01T00:00:00", "deathDate": None, "mainInscl": "UCS", "subInscl": "89",
    "age": "33 ปี", "checkDate": "2026-11-16T14:40:00",
    "claimTypes": [{"claimType": "PG0060001", "claimTypeName": "เข้ารับบริการรักษาทั่วไป (OPD/ IPD/ PP)"},
                   {"claimType": "PG0150001", "claimTypeName": "บริการดูแลอาการเจ็บป่วยเบื้องต้น"}],
    "correlationId": "abc-def-123",
    "hospSub": {"hcode": "03722", "hname": "รพ.สต.บ้านทดสอบ"}, "hospMainOp": {"hcode": "10708", "hname": "รพ.อุดรธานี"},
    "hospMain": {"hcode": "10708", "hname": "รพ.อุดรธานี"}, "image": "/9j/4AAQSkZJRg==",
    "startDateTime": "2024-10-01T00:00:00", "expireDateTime": None,
}


class R:
    def __init__(self, status, body):
        self.status_code = status
        self._b = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._b, str):
            return json.loads(self._b)
        return self._b


class FakeAgent:
    """จำลอง Agent ที่ localhost:8189"""
    def __init__(self, routes):
        self.routes = routes  # {(method, path): R | Exception}
        self.calls = []
        self.trust_env = True

    def _hit(self, method, url, **kw):
        path = url.split("8189", 1)[1]
        self.calls.append((method, path, kw))
        v = self.routes.get((method, path))
        if isinstance(v, Exception):
            raise v
        return v or R(404, {"status": 404, "error": "Not Found"})

    def get(self, url, **kw):
        return self._hit("GET", url, **kw)

    def post(self, url, **kw):
        return self._hit("POST", url, **kw)


def test_read_card_and_summary():
    fa = FakeAgent({("GET", "/api/smartcard/read"): R(200, READ)})
    a = AgentClient("http://localhost:8189", session=fa)
    assert fa.trust_env is False                     # ไม่ส่งผ่าน proxy
    d = a.read()
    assert fa.calls[0][2]["params"] == {"readImageFlag": "true"}
    s = summarize_card(d)
    assert s["name"] == "นาย ทดสอบ ระบบ" and s["sex"] == "ชาย" and s["mainInscl"] == "UCS"
    assert s["claimTypes"][1] == {"code": "PG0150001", "name": "บริการดูแลอาการเจ็บป่วยเบื้องต้น"}
    assert s["correlationId"] == "abc-def-123" and "image" not in s


@pytest.mark.parametrize("body,expect", [
    ({"status": 500, "message": "th.co...NoCardAttachedException"}, "ยังไม่ได้เสียบบัตร"),
    ({"status": 500, "error": "NoReaderAttachedException"}, "ไม่พบเครื่องอ่านบัตร"),
    ({"status": 503, "message": "NhsoServiceUnavailableException"}, "เชื่อมต่อ สปสช. ไม่ได้"),
    ({"status": 500, "message": 'I/O error on GET request for "https://authenservice.nhso.go.th/x": '
      "sun.security.validator.ValidatorException: PKIX path building failed"}, "SSL inspection"),
])
def test_agent_errors_are_thai(body, expect):
    fa = FakeAgent({("GET", "/api/smartcard/read"): R(500, body)})
    with pytest.raises(AgentError, match=expect):
        AgentClient(session=fa).read()


def test_agent_not_running():
    fa = FakeAgent({("GET", "/api/smartcard/read"): requests.ConnectionError("refused")})
    with pytest.raises(AgentError, match="ไม่พบ Agent"):
        AgentClient(session=fa).read()


def test_agent_without_token_shows_sign_in():
    fa = FakeAgent({("GET", "/api/smartcard/read"): R(401, "<html><title>Sign in</title></html>")})
    with pytest.raises(AgentError, match="userconfig.properties"):
        AgentClient(session=fa).read()


def test_build_payload_matches_request3():
    p = build_endpoint_payload(pid=PID, correlation_id="abc-def-123", claim_type="pg0060001",
                               privilege_amount="150", paid_amount="500", invoice_datetime="2026-03-17 10:30:00",
                               mobile="089-123-4567", visit_number="VN001234")
    assert p == {"pid": PID, "invoiceDate": "2026-03-17", "invoiceTime": "10:30:00", "paidAmount": 500.0,
                 "privilegeAmount": 150.0, "claimType": "PG0060001", "correlationId": "abc-def-123",
                 "mobile": "0891234567", "visitNumber": "VN001234"}


def test_build_payload_validation():
    ok = dict(pid=PID, correlation_id="c", claim_type="PG0060001", privilege_amount="1")
    with pytest.raises(ValidationError, match="correlationId"):
        build_endpoint_payload(**{**ok, "correlation_id": ""})
    with pytest.raises(ValidationError, match="มือถือ"):
        build_endpoint_payload(**ok, mobile="12ab")
    future = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    with pytest.raises(ValidationError, match="ไม่เกินเวลาปัจจุบัน"):
        build_endpoint_payload(**ok, invoice_datetime=future)


def test_save_endpoint_and_latest():
    fa = FakeAgent({("POST", "/api/nhso-service/save-endpoint"): R(200, {"seq": "10000001", "authenCode": "EP1001458547"}),
                    ("GET", f"/api/nhso-service/latest-authen-code/{PID}"): R(200, {"claimCode": "EP1"})})
    a = AgentClient(session=fa)
    r = a.save_endpoint({"pid": PID})
    assert r["authenCode"] == "EP1001458547" and fa.calls[0][2]["json"] == {"pid": PID}
    assert a.latest_authen(PID)["claimCode"] == "EP1"


def test_url_validation():
    assert normalize_agent_url("http://localhost:8189/") == "http://localhost:8189"
    with pytest.raises(ValidationError):
        normalize_agent_url("file:///etc/passwd")


def test_web_agent_close_logs_and_blocks_duplicate(tmp_path, monkeypatch):
    from nhso_right_check import web
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NHSO_ENV", raising=False)
    (tmp_path / ".env").write_text("NHSO_ENV=test\n", encoding="utf-8")
    app = web.App(Settings.load(tmp_path / ".env"))
    fa = FakeAgent({("GET", "/api/smartcard/read"): R(200, READ),
                    ("POST", "/api/nhso-service/save-endpoint"): R(200, {"seq": "1", "authenCode": "EP1"})})
    monkeypatch.setattr(app, "_agent", lambda: AgentClient(session=fa))
    rd = app.agent_read({})
    assert rd["image"] and "image" not in rd["raw"]            # รูปส่งไปแสดงผลเท่านั้น
    body = {"pid": PID, "correlationId": "abc-def-123", "claimType": "PG0060001", "privilegeAmount": "150",
            "invoiceDatetime": "2026-01-05 09:00:00", "send": True}   # วันที่ในอดีต (CI ใช้เวลา UTC)
    assert app.agent_close(body)["authenCode"] == "EP1"
    row = app.store.recent(1)[0]
    assert row["action"] == "AGENT_CLAIM" and row["authen_code"] == "EP1" and "image" not in row["request_json"]
    with pytest.raises(ValidationError, match="ปิดสิทธิสำเร็จแล้ว"):
        app.agent_close(body)
    assert app.agent_close({**body, "allow_duplicate": "1"})["success"]
