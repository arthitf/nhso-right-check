"""หน้าจอโปรแกรม (Web UI ในเครื่อง) — ใช้แค่ Python standard library ไม่ต้องติดตั้งอะไรเพิ่ม

เปิดด้วย:  python -m nhso_right_check ui   (หรือดับเบิลคลิกไฟล์โปรแกรมที่ build แล้ว)
เปิดเฉพาะ 127.0.0.1 (เครื่องตัวเอง) และทุก request ต้องมี session key ที่ฝังในหน้าเว็บ
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from . import __author__, __version__
from .config import Settings
from .endpoint import EndpointClient, EndpointError, is_success
from .srm import MANUAL_TOKEN_FILE, SRMClient, SRMError, parse_token_text, summarize, write_private
from .rights import all_rights, groups
from .store import ClaimStore
from .validation import ValidationError, mask_pid

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class App:
    def __init__(self, settings: Settings):
        self.s = settings
        self.session = secrets.token_urlsafe(24)
        self.store = ClaimStore(settings.db_path)
        self.srm = SRMClient(settings, on_call=self.store.count_call)
        self.ep = EndpointClient(settings)

    # ---------- actions ----------
    def status(self, _body: dict) -> dict:
        info = []
        for label, p in (("token.txt (SRM)", Path(self.s.srm_token_file)), ("วางเอง", Path(MANUAL_TOKEN_FILE))):
            if p.is_file() and p.read_text(encoding="utf-8-sig").strip():
                mins = (time.time() - p.stat().st_mtime) / 60
                info.append({"source": label, "path": str(p), "ageMin": round(mins)})
        return {
            "env": self.s.env, "hcode": self.s.hcode, "version": __version__, "author": __author__,
            "srmToken": info, "srmTokenOverride": bool(self.s.srm_access_token),
            "endpointToken": bool(self.s.endpoint_token), "recorder": bool(self.s.recorder_pid),
            "tokenFile": str(self.s.srm_token_file),
            "usageToday": self.store.usage_today(), "cacheSeconds": self.s.srm_cache_seconds,
        }

    def check(self, b: dict) -> dict:
        self.srm.reload_token() if not self.s.srm_access_token else None
        raw = self.srm.right_search(b.get("pid", ""), force=bool(b.get("force")))
        age = self.srm.last_cached_age
        return {"summary": summarize(raw), "raw": raw,
                "cachedAgeSec": None if age is None else round(age), "usageToday": self.store.usage_today()}

    def close(self, b: dict) -> dict:
        from .cli import _close_one  # ใช้ logic เดียวกับ CLI

        send = bool(b.get("send"))
        if send and self.s.env == "prod" and b.get("confirm") != "YES":
            raise ValidationError("Production ต้องพิมพ์ YES เพื่อยืนยัน")
        row = {k: str(b.get(k, "") or "") for k in (
            "pid", "service_code", "privilege_amount", "paid_amount", "total_amount", "service_datetime",
            "invoice_datetime", "visit_number", "main_inscl", "department_code", "department_name", "transaction_id",
            "allow_duplicate")}
        row["service_datetime"] = row["service_datetime"].replace("T", " ")
        row["invoice_datetime"] = row["invoice_datetime"].replace("T", " ")
        srm = None if row["main_inscl"] else self.srm
        return _close_one(row, self.s, srm, self.ep, self.store, send)

    def cancel(self, b: dict) -> dict:
        trans = (b.get("trans") or "").strip()
        if not trans:
            raise ValidationError("กรุณาระบุ transactionId")
        seq = (str(b.get("seq") or "")).strip()
        if not seq:
            row = self.store.find_claim(trans)
            if not row or not row["seq"]:
                raise ValidationError("ไม่พบ seq ของ transactionId นี้ในประวัติ — กรุณาระบุ seq")
            seq = row["seq"]
        payload = {"seq": int(seq), "transactionId": trans, "paymentStatus": "CANCEL"}
        if not b.get("send"):
            return {"dryRun": True, "payload": payload}
        if self.s.env == "prod" and b.get("confirm") != "YES":
            raise ValidationError("Production ต้องพิมพ์ YES เพื่อยืนยัน")
        resp = self.ep.cancel(seq, trans)
        ok = is_success(resp)
        self.store.log(env=self.s.env, action="CANCEL", request=payload, response=resp, success=ok)
        return {"success": ok, **resp}

    def history(self, _b: dict) -> dict:
        rows = []
        for r in self.store.recent(100):
            rows.append({k: r[k] for k in ("created_at", "env", "action", "transaction_id", "seq", "authen_code",
                                           "success", "data_error", "claim_service", "main_inscl",
                                           "privilege_amount", "paid_amount")} | {"pid": mask_pid(r["pid"] or "")})
        return {"rows": rows}

    def set_token(self, b: dict) -> dict:
        text = (b.get("text") or "").strip()
        if not text:
            Path(MANUAL_TOKEN_FILE).unlink(missing_ok=True)
            self.srm.reload_token() if Path(self.s.srm_token_file).is_file() else None
            return {"cleared": True}
        pair = parse_token_text(text)
        write_private(MANUAL_TOKEN_FILE, text)
        self.srm._token = pair
        return {"saved": True, "length": len(pair.access_token), "refresh": bool(pair.refresh_token)}

    def rights(self, _b: dict) -> dict:
        return {"rights": all_rights(), "groups": groups()}

    ROUTES = {"rights": rights, "status": status, "check": check, "close": close, "cancel": cancel,
              "history": history, "token": set_token}


def make_handler(app: App, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "nhso-right-check"

        def log_message(self, fmt, *args):  # ไม่พิมพ์ log ทุก request (อาจมีเลขบัตรใน URL)
            log.debug(fmt, *args)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in allowed_hosts

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            if self.path in ("/", "/index.html"):
                html = (STATIC / "index.html").read_text(encoding="utf-8")
                html = html.replace("{{SESSION}}", app.session).replace("{{VERSION}}", __version__)
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            if self.path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if not self._host_ok() or self.headers.get("X-Session") != app.session:
                return self._json(403, {"error": "session ไม่ถูกต้อง — ปิดแล้วเปิดโปรแกรมใหม่"})
            name = self.path.removeprefix("/api/")
            fn = App.ROUTES.get(name)
            if not fn:
                return self._json(404, {"error": "ไม่พบคำสั่ง"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 1_000_000:  # กัน request ใหญ่ผิดปกติ
                    return self._json(413, {"error": "ข้อมูลใหญ่เกินไป"})
                if n and "application/json" not in (self.headers.get("Content-Type") or ""):
                    return self._json(415, {"error": "ต้องเป็น JSON"})
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                if not isinstance(body, dict):
                    return self._json(400, {"error": "รูปแบบข้อมูลไม่ถูกต้อง"})
                self._json(200, fn(app, body))
            except (SRMError, EndpointError, ValidationError) as e:
                self._json(400, {"error": str(e), "detail": getattr(e, "body", None)})
            except Exception as e:  # noqa: BLE001
                log.exception("UI error")
                self._json(500, {"error": f"เกิดข้อผิดพลาดภายในโปรแกรม ({type(e).__name__})"})

    return Handler


def serve(settings: Settings, port: int = 8765, open_browser: bool = True) -> None:
    app = App(settings)
    httpd = None
    for p in range(port, port + 20):  # ถ้า port ไม่ว่างให้ลองตัวถัดไป
        try:
            httpd = HTTPServer(("127.0.0.1", p), make_handler(app, p))
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit("ไม่มี port ว่าง")
    url = f"http://127.0.0.1:{port}/"
    print(f"ตรวจสอบสิทธิ สปสช. v{__version__} — พัฒนาโดย {__author__}")
    print(f"เปิดหน้าจอที่ {url}   (ปิดหน้าต่างนี้ = ปิดโปรแกรม / กด Ctrl+C)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
