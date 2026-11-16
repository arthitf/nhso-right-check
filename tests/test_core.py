import json
from pathlib import Path
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from nhso_right_check.config import Settings
from nhso_right_check.endpoint import ClaimRequest, EndpointClient, build_claim_payload, is_success, to_epoch_ms
from nhso_right_check.srm import SRMClient, SRMError, main_fund, parse_token_text, summarize
from nhso_right_check.store import ClaimStore
from nhso_right_check.validation import ValidationError, is_valid_pid, mask_pid, to_amount

VALID_PID = "1101700203450"      # check digit ถูกต้อง (เลขทดสอบ)
RECORDER = "3101112223332"


# ---------- fake HTTP ----------
class FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._body, str):
            return json.loads(self._body)
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0)

    def get(self, url, **kw):
        return self._next("GET", url, **kw)

    def post(self, url, **kw):
        return self._next("POST", url, **kw)

    def request(self, method, url, **kw):
        return self._next(method, url, **kw)


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.srm_api_base = "https://tsrm.nhso.go.th"
    s.srm_portal_base = "https://tsrmportal.nhso.go.th"
    s.endpoint_base = "https://test.nhso.go.th/nhsoendpoint/"
    s.endpoint_token = "ep-token"
    s.hcode = "13814"
    s.source_id = "13814"
    s.recorder_pid = RECORDER
    s.srm_token_file = tmp_path / "token.txt"
    s.db_path = tmp_path / "log.sqlite3"
    return s


# ---------- validation ----------
def test_pid_checksum():
    assert is_valid_pid(VALID_PID)
    assert not is_valid_pid("1101700203451")
    assert not is_valid_pid("123")


def test_amount_rules():
    assert to_amount("1,234.5", "x") == Decimal("1234.50")
    with pytest.raises(ValidationError):
        to_amount("1.234", "x")
    with pytest.raises(ValidationError):
        to_amount("123456789", "x")
    with pytest.raises(ValidationError):
        to_amount("-1", "x")


def test_mask_pid():
    assert mask_pid(VALID_PID) == "1-xxxx-xxxxx-45-0"


def test_epoch_ms():
    assert to_epoch_ms(1679974614791) == 1679974614791
    assert to_epoch_ms("2023-03-28 10:36:54") == int(datetime(2023, 3, 28, 10, 36, 54).timestamp() * 1000)
    with pytest.raises(ValidationError):
        to_epoch_ms("2566-03-28")


# ---------- token ----------
@pytest.mark.parametrize("text,access,refresh", [
    ('{"access_token":"AAA","refresh_token":"RRR"}', "AAA", "RRR"),
    ("access_token=AAA\nrefresh_token=RRR", "AAA", "RRR"),
    ("﻿AAA\n", "AAA", ""),
    ("Bearer AAA", "AAA", ""),
])
def test_parse_token(text, access, refresh):
    t = parse_token_text(text)
    assert (t.access_token, t.refresh_token) == (access, refresh)


def test_missing_token_file(settings, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SRMError, match="token"):
        SRMClient(settings, FakeSession([])).right_search(VALID_PID)


def test_manual_token_override(settings):
    settings.srm_access_token = "MANUAL"
    sess = FakeSession([FakeResp(200, {"pid": VALID_PID, "funds": []})])
    SRMClient(settings, sess).right_search(VALID_PID)
    assert sess.calls[0][2]["headers"]["Authorization"] == "Bearer MANUAL"


# ---------- SRM ----------
SRM_RESULT = {
    "checkDate": "2026-11-16T10:00:00", "pid": VALID_PID, "tname": "นาย", "fname": "ทดสอบ", "lname": "ระบบ",
    "sex": {"id": "1", "name": "ชาย"}, "birthDate": "มกราคม 2536", "deathDate": None,
    "funds": [
        {"fundType": "N", "mainInscl": {"id": "SSS", "name": "ประกันสังคม"}},
        {"fundType": "Y", "mainInscl": {"id": "UCS", "name": "สิทธิ UC"}, "subInscl": {"id": "89", "name": "ช่วงอายุ 12-59 ปี"},
         "hospMainOp": {"hcode": "10708", "hname": "รพ.ทดสอบ"}, "paidModel": "1"},
    ],
}


def test_right_search_and_summary(settings):
    settings.srm_token_file.write_text('{"access_token":"AAA"}', encoding="utf-8")
    sess = FakeSession([FakeResp(200, SRM_RESULT)])
    res = SRMClient(settings, sess).right_search(VALID_PID)
    method, url, kw = sess.calls[0]
    assert url == "https://tsrm.nhso.go.th/api/ucws/v1/right-search"
    assert kw["params"] == {"pid": VALID_PID}
    assert kw["headers"]["Authorization"] == "Bearer AAA"
    assert main_fund(res)["mainInscl"]["id"] == "UCS"
    s = summarize(res)
    assert s["mainInscl"] == "UCS" and s["hospMainOp"] == "10708" and s["name"] == "นาย ทดสอบ ระบบ"


def test_right_search_refresh_on_401(settings):
    settings.srm_token_file.write_text('{"access_token":"OLD","refresh_token":"RRR"}', encoding="utf-8")
    sess = FakeSession([
        FakeResp(401, {"error": "expired"}),
        FakeResp(200, {"access_token": "NEW"}),
        FakeResp(200, SRM_RESULT),
    ])
    SRMClient(settings, sess).right_search(VALID_PID)
    assert sess.calls[1][0] == "POST"
    assert sess.calls[1][1] == "https://tsrmportal.nhso.go.th/api/scard/access-token"
    assert sess.calls[1][2]["data"] == {"refresh_token": "RRR"}
    assert sess.calls[2][2]["headers"]["Authorization"] == "Bearer NEW"


def test_right_search_404(settings):
    settings.srm_access_token = "AAA"
    with pytest.raises(SRMError, match="ไม่พบข้อมูล"):
        SRMClient(settings, FakeSession([FakeResp(404, "")])).right_search(VALID_PID)


# ---------- Endpoint ----------
def _req(**kw):
    base = dict(pid=VALID_PID, claim_service_code="PG0060001", main_inscl_code="UCS",
                privilege_amount="700", paid_amount="500", service_datetime="2026-01-05 08:30:00")
    base.update(kw)
    return ClaimRequest(**base)


def test_build_payload(settings):
    p = build_claim_payload(_req(visit_number="VN1"), settings)
    assert p["hcode"] == "13814" and p["transactionId"].startswith("13814")
    assert p["totalAmount"] == 1200.0 and p["paidAmount"] == 500.0 and p["privilegeAmount"] == 700.0
    assert p["recorderPid"] == RECORDER and p["sourceId"] == "13814" and p["visitNumber"] == "VN1"
    assert isinstance(p["serviceDateTime"], int)


def test_build_payload_rejects_future(settings):
    future = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    with pytest.raises(ValidationError, match="serviceDateTime"):
        build_claim_payload(_req(service_datetime=future), settings)


def test_build_payload_rejects_bad_inscl(settings):
    with pytest.raises(ValidationError):
        build_claim_payload(_req(main_inscl_code="XYZ"), settings)


def test_claim_and_cancel(settings):
    sess = FakeSession([
        FakeResp(200, {"seq": 10000001, "authenCode": "PP1001458547", "dataError": None}),
        FakeResp(200, {"status": "Y", "authenCode": "PP1001458547", "dataError": None}),
    ])
    ep = EndpointClient(settings, sess)
    payload = build_claim_payload(_req(), settings)
    r = ep.claim(payload)
    assert is_success(r)
    assert sess.calls[0][0] == "POST"
    assert sess.calls[0][1] == "https://test.nhso.go.th/nhsoendpoint/api/nhso-claim-detail"
    assert sess.calls[0][2]["headers"]["Authorization"] == "Bearer ep-token"
    c = ep.cancel(r["seq"], payload["transactionId"])
    assert is_success(c)
    assert sess.calls[1][2]["json"] == {"seq": 10000001, "transactionId": payload["transactionId"],
                                        "paymentStatus": "CANCEL"}


def test_claim_data_error_is_failure(settings):
    sess = FakeSession([FakeResp(400, {"seq": None, "authenCode": None, "dataError": "Invalid Token"})])
    r = EndpointClient(settings, sess).claim(build_claim_payload(_req(), settings))
    assert not is_success(r) and r["dataError"] == "Invalid Token"


# ---------- store ----------
def test_store_roundtrip(settings):
    st = ClaimStore(settings.db_path)
    req = {"transactionId": "13814X", "pid": VALID_PID, "hcode": "13814"}
    st.log(env="test", action="CLAIM", request=req, response={"seq": 5, "authenCode": "PP1"}, success=True)
    row = st.find_claim("13814X")
    assert row["seq"] == "5" and row["authen_code"] == "PP1"


def test_network_error_becomes_srm_error(settings):
    import requests

    class Boom(FakeSession):
        def get(self, url, **kw):
            raise requests.ConnectionError("down")

    settings.srm_access_token = "AAA"
    with pytest.raises(SRMError, match="เชื่อมต่อ"):
        SRMClient(settings, Boom([])).right_search(VALID_PID)


# ---------- ตารางสิทธิ ----------
def test_rights_lookup():
    from nhso_right_check.rights import all_rights, lookup
    u = lookup("ucs")
    assert u["known"] and u["groupId"] == "U" and "หลักประกันสุขภาพ" in u["name"]
    assert lookup("BKK")["groupId"] == "B"
    assert lookup("COR")["known"] and not lookup("ZZZ")["known"]
    assert len(all_rights()) >= 277


def test_payload_accepts_codes_from_rights_table(settings):
    p = build_claim_payload(_req(main_inscl_code="bkk"), settings)
    assert p["mainInsclCode"] == "BKK"


def test_summary_explains_right():
    s = summarize(SRM_RESULT)
    assert s["rightGroup"] == "U" and s["rightGroupName"] and s["otherFunds"] == "SSS"


# ---------- ความปลอดภัย / โควตา ----------
def test_srm_cache_avoids_repeat_calls(settings):
    settings.srm_access_token = "AAA"
    calls = []
    sess = FakeSession([FakeResp(200, SRM_RESULT), FakeResp(200, SRM_RESULT)])
    c = SRMClient(settings, sess, on_call=calls.append)
    c.right_search(VALID_PID)
    c.right_search(VALID_PID)          # ภายในเวลา cache → ไม่เรียก API
    assert len(sess.calls) == 1 and calls == ["srm"] and c.last_cached_age is not None
    c.right_search(VALID_PID, force=True)
    assert len(sess.calls) == 2


def test_srm_429_message(settings):
    settings.srm_access_token = "AAA"
    with pytest.raises(SRMError, match="ตรวจสอบสิทธิแบบกลุ่ม"):
        SRMClient(settings, FakeSession([FakeResp(429, "")])).right_search(VALID_PID)


def test_csv_formula_injection_is_neutralised(tmp_path):
    from nhso_right_check.cli import _write_csv
    out = tmp_path / "o.csv"
    _write_csv(str(out), [{"name": "=HYPERLINK(\"x\")", "n": "ok"}])
    assert "'=HYPERLINK" in out.read_text(encoding="utf-8-sig")


def test_duplicate_claim_guard(settings):
    from nhso_right_check.cli import _close_one
    st = ClaimStore(settings.db_path)
    row = {"pid": VALID_PID, "service_code": "PG0060001", "privilege_amount": "700", "main_inscl": "UCS",
           "service_datetime": "2026-01-05 08:30:00"}
    p = build_claim_payload(_req(), settings)
    st.log(env="test", action="CLAIM", request=p, response={"seq": 1, "authenCode": "PP1"}, success=True)
    with pytest.raises(ValidationError, match="ปิดสิทธิสำเร็จแล้ว"):
        _close_one(row, settings, None, None, st, send=False)
    res = _close_one({**row, "allow_duplicate": "1"}, settings, None, None, st, send=False)
    assert res["dryRun"]
    st.log(env="test", action="CANCEL", request={"transactionId": p["transactionId"]}, response={"status": "Y"},
           success=True)
    assert _close_one(row, settings, None, None, st, send=False)["dryRun"]  # ยกเลิกแล้ว ส่งใหม่ได้


def test_usage_counter(settings):
    st = ClaimStore(settings.db_path)
    st.count_call("srm"); st.count_call("srm")
    assert st.usage_today()["srm"] == 2


# ---------- หน้าตั้งค่า ----------
def test_save_env_keeps_other_lines(tmp_path, monkeypatch):
    from nhso_right_check.config import save_env
    p = tmp_path / ".env"
    p.write_text("# comment\nNHSO_ENV=test\nHTTP_TIMEOUT=30\n# NHSO_CLAIM_PATH=x\n", encoding="utf-8")
    for k in ("NHSO_ENV", "HCODE"):
        monkeypatch.delenv(k, raising=False)
    save_env(p, {"NHSO_ENV": "prod", "HCODE": "13814"})
    t = p.read_text(encoding="utf-8")
    assert "NHSO_ENV=prod" in t and "HCODE=13814" in t and "HTTP_TIMEOUT=30" in t and "# comment" in t


def test_web_settings_roundtrip(tmp_path, monkeypatch):
    from nhso_right_check.web import App
    monkeypatch.chdir(tmp_path)
    for k in ("NHSO_ENV", "HCODE", "RECORDER_PID", "NHSO_ENDPOINT_TOKEN", "SOURCE_ID"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / ".env").write_text("NHSO_ENV=test\n", encoding="utf-8")
    app = App(Settings.load(tmp_path / ".env"))
    with pytest.raises(ValidationError, match="YES"):
        app.save_settings({"env": "prod", "hcode": "13814"})
    r = app.save_settings({"env": "prod", "confirm": "YES", "hcode": "13814", "recorderPid": RECORDER,
                           "endpointToken": "abcd-1234-efgh-5678"})
    assert r["env"] == "prod" and r["endpointTokenSet"] and r["endpointTokenTail"] == "5678"
    assert "abcd-1234" not in json.dumps(r)              # ไม่ส่ง token เต็มกลับไปหน้าจอ
    assert app.s.endpoint_base.startswith("https://nhsoapi")  # ใช้ URL production ทันที
    # เว้น token ว่าง = ใช้ค่าเดิม, recorder แบบ mask = ค่าเดิม
    r2 = app.save_settings({"env": "prod", "hcode": "13814", "recorderPid": r["recorderPid"], "endpointToken": ""})
    assert r2["endpointTokenSet"] and app.s.recorder_pid == RECORDER
    with pytest.raises(ValidationError):
        app.save_settings({"env": "prod", "hcode": "123"})


def test_open_mode_setting(tmp_path, monkeypatch):
    from nhso_right_check.web import App
    monkeypatch.chdir(tmp_path)
    for k in ("NHSO_ENV", "OPEN_MODE"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / ".env").write_text("NHSO_ENV=test\n", encoding="utf-8")
    app = App(Settings.load(tmp_path / ".env"))
    assert app.settings({})["openMode"] == "window"
    app.save_settings({"openMode": "browser"})
    assert app.s.open_mode == "browser" and "OPEN_MODE=browser" in (tmp_path / ".env").read_text()


# ---------- ตรวจแบบกลุ่ม ----------
PID2 = "3101112223332"


def test_batch_parse_text_and_dedupe():
    from nhso_right_check.batch import parse_input
    r = parse_input(f"{VALID_PID}\n1-1017-00203-45-0\n{PID2}, 1101700203451\nโทร 0812345678 HN 12345678901234")
    assert r["pids"] == [VALID_PID, PID2] and r["invalid"] == ["1101700203451"] and r["duplicates"] == 1


def test_xlsx_roundtrip(tmp_path):
    from nhso_right_check.batch import _row, parse_input, write_xlsx
    p = write_xlsx(tmp_path / "o.xlsx", [_row(1, VALID_PID, "OK", {"name": "นาย ทดสอบ", "mainInscl": "UCS"}),
                                          _row(0, "=1+1", "INVALID")])
    r = parse_input(data=p.read_bytes(), filename="o.xlsx")
    assert r["pids"] == [VALID_PID]
    import zipfile
    sheet = zipfile.ZipFile(p).read("xl/worksheets/sheet1.xml").decode()
    assert "นาย ทดสอบ" in sheet and "'=1+1" in sheet and 't="inlineStr"' in sheet


def test_batch_job_runs_and_pauses_on_429(settings):
    from nhso_right_check.batch import BatchJob
    settings.srm_access_token = "AAA"
    sess = FakeSession([FakeResp(200, SRM_RESULT), FakeResp(404, ""), FakeResp(429, ""), FakeResp(200, SRM_RESULT),
                        FakeResp(200, SRM_RESULT)])
    calls = []
    srm = SRMClient(settings, sess, on_call=calls.append)
    job = BatchJob(srm, [VALID_PID, PID2, "1101700203468", "1234567890121"], ["1101700203451"], delay=0)
    job.start(); job.wait(5)
    s = job.snapshot(masked=False)
    assert s["state"] == "paused" and "429" in s["message"] and s["done"] == 2
    assert s["counts"] == {"INVALID": 1, "OK": 1, "NOTFOUND": 1}
    job.start(); job.wait(5)                     # ตรวจต่อ: คนที่โดน 429 ถูกตรวจใหม่
    s = job.snapshot(masked=False)
    assert s["state"] == "done" and s["done"] == 4 and len(sess.calls) == 5
    assert [r["pid"] for r in job.all_rows()][-1] == "1101700203451"


def test_store_usable_from_other_thread(settings):
    import threading
    st = ClaimStore(settings.db_path)
    err = []
    t = threading.Thread(target=lambda: (st.count_call("srm"), st.usage_today()) or None)
    try:
        t.start(); t.join()
    except Exception as e:  # noqa: BLE001
        err.append(e)
    assert not err and st.usage_today()["srm"] == 1


def test_web_batch_flow(tmp_path, monkeypatch):
    from nhso_right_check import batch as bmod
    from nhso_right_check.web import App
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bmod, "export_folder", lambda: tmp_path)
    (tmp_path / ".env").write_text("NHSO_ENV=test\nSRM_ACCESS_TOKEN=AAA\nBATCH_LIMIT=1\nBATCH_DELAY=0.2\n", encoding="utf-8")
    for k in ("NHSO_ENV", "SRM_ACCESS_TOKEN", "BATCH_LIMIT", "BATCH_DELAY"):
        monkeypatch.delenv(k, raising=False)
    app = App(Settings.load(tmp_path / ".env"))
    import base64
    r = app.batch_parse({"file": base64.b64encode(f"pid\n{VALID_PID}\n{PID2}\n".encode()).decode(), "name": "a.csv"})
    assert r["unique"] == 2 and r["apiNeeded"] == 2
    with pytest.raises(ValidationError, match="เกินที่ตั้งไว้"):
        app.batch_start({})
    import nhso_right_check.web as web
    sess = FakeSession([FakeResp(200, SRM_RESULT), FakeResp(200, {**SRM_RESULT, "pid": PID2})])
    real = web.SRMClient
    monkeypatch.setattr(web, "SRMClient", lambda s, on_call=None: real(s, sess, on_call=on_call))
    app.batch_start({"allowLarge": True})
    app.job.wait(5)
    st = app.batch_status({})
    assert st["state"] == "done" and st["done"] == 2 and "xxxx" in st["rows"][0]["pid"]
    out = app.batch_export({})
    assert out["rows"] == 2 and Path(out["path"]).exists()


def test_parse_srm_sso_token_file_with_dashes():
    # รูปแบบจริงของ %USERPROFILE%\SRM Smart Card Single Sign-On\token.txt (ค่า JWT ปลอม)
    t = parse_token_text("access-token=eyJhbGciOi.AAA.BBB\n\nrefresh-token=eyJhbGciOi.CCC.DDD\n")
    assert t.access_token == "eyJhbGciOi.AAA.BBB" and t.refresh_token == "eyJhbGciOi.CCC.DDD"
    assert parse_token_text("eyJx.y.z").access_token == "eyJx.y.z"
    assert parse_token_text("Bearer eyJx.y.z").access_token == "eyJx.y.z"
