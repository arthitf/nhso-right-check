"""หน้าจอโปรแกรม (Web UI ในเครื่อง) — ใช้แค่ Python standard library ไม่ต้องติดตั้งอะไรเพิ่ม

เปิดด้วย:  python -m nhso_right_check ui   (หรือดับเบิลคลิกไฟล์โปรแกรมที่ build แล้ว)
เปิดเฉพาะ 127.0.0.1 (เครื่องตัวเอง) และทุก request ต้องมี session key ที่ฝังในหน้าเว็บ
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import secrets
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from . import __author__, __version__
from .batch import BatchJob, export_folder, export_name, parse_input, write_xlsx
from .config import Settings, save_env
from .endpoint import EndpointClient, EndpointError, is_success
from .srm import MANUAL_TOKEN_FILE, SRMClient, SRMError, parse_token_text, summarize, write_private
from .rights import all_rights, groups
from .store import ClaimStore
from .validation import ValidationError, mask_pid, require_hcode, require_pid

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class App:
    def __init__(self, settings: Settings):
        self.s = settings
        self.session = secrets.token_urlsafe(24)
        self.httpd = None
        self.store = ClaimStore(settings.db_path)
        self.srm = SRMClient(settings, on_call=self.store.count_call)
        self.ep = EndpointClient(settings)
        self.batch_pending: dict | None = None
        self.job: BatchJob | None = None
        self.last_export: Path | None = None

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

    def settings(self, _b: dict) -> dict:
        """ค่าตั้งปิดสิทธิ (ไม่ส่ง token เต็มออกไป)"""
        t = self.s.endpoint_token
        return {"env": self.s.env, "hcode": self.s.hcode, "sourceId": self.s.source_id,
                "recorderPid": mask_pid(self.s.recorder_pid) if self.s.recorder_pid else "",
                "recorderSet": bool(self.s.recorder_pid),
                "endpointTokenSet": bool(t), "endpointTokenTail": t[-4:] if len(t) >= 8 else "",
                "envFile": str(Path(self.s.env_file).resolve()), "openMode": self.s.open_mode}

    def save_settings(self, b: dict) -> dict:
        updates: dict[str, str] = {}
        env = (b.get("env") or self.s.env).lower()
        if env not in ("test", "prod"):
            raise ValidationError("ระบบต้องเป็น test หรือ prod")
        updates["NHSO_ENV"] = env
        if b.get("openMode") in ("window", "browser"):
            updates["OPEN_MODE"] = b["openMode"]
        if (b.get("hcode") or "").strip():
            updates["HCODE"] = require_hcode(b["hcode"])
        if "sourceId" in b:
            src = (b.get("sourceId") or "").strip()
            if len(src) > 50 or any(c in src for c in "\r\n="):
                raise ValidationError("SOURCE_ID ไม่ถูกต้อง")
            updates["SOURCE_ID"] = src
        rp = (b.get("recorderPid") or "").strip()
        if rp and "x" not in rp:  # ค่าที่ปิดบางส่วน (ค่าเดิม) ไม่ต้องบันทึกซ้ำ
            updates["RECORDER_PID"] = require_pid(rp, "เลขบัตรผู้บันทึก")
        tok = (b.get("endpointToken") or "").strip()
        if tok:  # เว้นว่าง = ใช้ค่าเดิม
            if len(tok) > 500 or any(c in tok for c in "\r\n ="):
                raise ValidationError("TOKEN KEY มีรูปแบบไม่ถูกต้อง")
            updates["NHSO_ENDPOINT_TOKEN"] = tok
        if env == "prod" and self.s.env != "prod" and b.get("confirm") != "YES":
            raise ValidationError("เปลี่ยนเป็น Production ต้องพิมพ์ YES เพื่อยืนยัน")
        save_env(self.s.env_file, updates)
        self._reload()
        return {"saved": True, **self.settings({})}

    def _reload(self) -> None:
        override = self.s.srm_access_token
        self.s = Settings.load(self.s.env_file)
        self.s.srm_access_token = override
        self.srm = SRMClient(self.s, on_call=self.store.count_call)
        self.ep = EndpointClient(self.s)

    def quit(self, _b: dict) -> dict:
        """ปิดโปรแกรม (ใช้ในโหมดเบราว์เซอร์ เพราะปิดแท็บแล้วโปรแกรมยังทำงานอยู่)"""
        if self.httpd is not None:
            threading.Timer(0.3, self.httpd.shutdown).start()
        return {"bye": True}

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

    # ---------- ตรวจแบบกลุ่ม ----------
    def batch_parse(self, b: dict) -> dict:
        """อ่านรายชื่อจากข้อความที่วาง และ/หรือไฟล์ (CSV / TXT / XLSX) — ยังไม่เรียก API"""
        if self.job and self.job.state == "running":
            raise ValidationError("กำลังตรวจกลุ่มเดิมอยู่ — กดหยุดก่อน")
        data = None
        if b.get("file"):
            try:
                data = base64.b64decode(b["file"], validate=True)
            except ValueError as e:
                raise ValidationError("อ่านไฟล์ไม่ได้") from e
        r = parse_input(b.get("text") or "", data, b.get("name") or "")
        if not r["pids"] and not r["invalid"]:
            raise ValidationError("ไม่พบเลขบัตรประชาชน 13 หลักในข้อมูลที่ให้มา")
        now = time.time()
        cache = self.srm._cache
        cached = sum(1 for p in r["pids"] if p in cache and now - cache[p][0] < self.s.srm_cache_seconds)
        self.batch_pending = r
        return {"found": r["found"], "unique": len(r["pids"]), "invalid": len(r["invalid"]),
                "invalidList": [mask_pid(p) for p in r["invalid"][:20]], "duplicates": r["duplicates"],
                "cached": cached, "apiNeeded": len(r["pids"]) - cached, "limit": self.s.batch_limit,
                "delay": self.s.batch_delay, "usageToday": self.store.usage_today()}

    def batch_start(self, b: dict) -> dict:
        if self.job and self.job.state == "running":
            raise ValidationError("กำลังตรวจอยู่แล้ว")
        r = self.batch_pending
        if not r:
            raise ValidationError("ยังไม่ได้อ่านรายชื่อ")
        if len(r["pids"]) > self.s.batch_limit and not b.get("allowLarge"):
            raise ValidationError(f"รายชื่อมี {len(r['pids'])} คน เกินที่ตั้งไว้ ({self.s.batch_limit}) "
                                  "— แนะนำใช้ SRM Portal → ตรวจสอบสิทธิแบบกลุ่ม หรือยืนยันเพื่อตรวจผ่าน API")
        srm = SRMClient(self.s, on_call=self.store.count_call)
        srm._cache = self.srm._cache           # ใช้ cache ร่วมกับการตรวจทีละคน
        srm._token = self.srm._token
        self.job = BatchJob(srm, r["pids"], r["invalid"], self.s.batch_delay)
        self.batch_pending = None
        self.job.start()
        return self.batch_status({})

    def batch_status(self, b: dict) -> dict:
        if not self.job:
            return {"state": "none"}
        return {**self.job.snapshot(int(b.get("since") or 0)), "usageToday": self.store.usage_today()}

    def batch_stop(self, _b: dict) -> dict:
        if self.job:
            self.job.stop()
        return {"ok": True}

    def batch_resume(self, b: dict) -> dict:
        if not self.job:
            raise ValidationError("ไม่มีงานตรวจค้างอยู่")
        if self.job.state in ("paused", "stopped"):
            if not self.s.srm_access_token:
                self.job.srm._token = None      # อ่าน token.txt ใหม่ (เผื่อเพิ่ง Login SRM ใหม่)
            self.job.start()
        return self.batch_status(b)

    def batch_export(self, _b: dict) -> dict:
        if not self.job:
            raise ValidationError("ยังไม่มีผลตรวจ")
        rows = self.job.all_rows()
        if not rows:
            raise ValidationError("ยังไม่มีผลตรวจ")
        path = write_xlsx(export_folder() / export_name(), rows)
        self.last_export = path
        return {"path": str(path), "folder": str(path.parent), "rows": len(rows)}

    def batch_open(self, b: dict) -> dict:
        """เปิดไฟล์ Excel ที่เพิ่งบันทึก หรือโฟลเดอร์ที่เก็บ (เปิดได้เฉพาะไฟล์ที่โปรแกรมสร้างเอง)"""
        if not self.last_export or not self.last_export.exists():
            raise ValidationError("ยังไม่ได้บันทึกไฟล์")
        target = self.last_export.parent if b.get("what") == "folder" else self.last_export
        if sys.platform == "win32":
            os.startfile(str(target))  # noqa: S606  (ไฟล์ที่โปรแกรมสร้างเอง)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(target)])  # noqa: S603
        return {"opened": str(target)}

    def batch_clear(self, _b: dict) -> dict:
        if self.job and self.job.state == "running":
            raise ValidationError("กำลังตรวจอยู่ — กดหยุดก่อน")
        self.job, self.batch_pending = None, None
        return {"cleared": True}

    def rights(self, _b: dict) -> dict:
        return {"rights": all_rights(), "groups": groups()}

    ROUTES = {"quit": quit, "settings": settings, "save_settings": save_settings, "rights": rights, "status": status, "check": check, "close": close, "cancel": cancel,
              "history": history, "token": set_token,
              "batch_parse": batch_parse, "batch_start": batch_start, "batch_status": batch_status,
              "batch_stop": batch_stop, "batch_resume": batch_resume, "batch_export": batch_export,
              "batch_open": batch_open, "batch_clear": batch_clear}


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
            if self.path in ("/icon.png", "/favicon.ico"):
                return self._send(200, (STATIC / "icon.png").read_bytes(), "image/png")
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
                if n > (12_000_000 if name == "batch_parse" else 1_000_000):  # กัน request ใหญ่ผิดปกติ
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


def _hide_own_console() -> None:
    """Windows: ซ่อนหน้าต่างดำ เฉพาะกรณีดับเบิลคลิกเปิดโปรแกรม (ไม่ซ่อน cmd ที่ผู้ใช้เปิดเอง)"""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        procs = (ctypes.c_uint * 4)()
        if k32.GetConsoleProcessList(procs, 4) <= 1:   # console นี้เป็นของโปรแกรมเราเอง
            ctypes.windll.user32.ShowWindow(k32.GetConsoleWindow(), 0)
    except Exception:  # noqa: BLE001
        pass


def _open_app_window(url: str) -> bool:
    """เปิดหน้าจอเป็นหน้าต่างโปรแกรม (pywebview: Windows = Edge WebView2, macOS = WebKit)
    คืน False ถ้าเครื่องนี้เปิดไม่ได้ จะใช้เบราว์เซอร์แทน"""
    try:
        import webview  # pywebview
    except Exception:  # noqa: BLE001
        return False
    try:
        try:
            webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        except Exception:  # noqa: BLE001
            pass
        webview.create_window(f"ตรวจสอบสิทธิ สปสช. v{__version__} — พัฒนาโดย {__author__}", url,
                              width=1100, height=820, min_size=(760, 560))
        _hide_own_console()
        webview.start()          # บล็อกจนกว่าจะปิดหน้าต่าง
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("เปิดหน้าต่างโปรแกรมไม่ได้ (%s) — ใช้เบราว์เซอร์แทน", e)
        return False


def serve(settings: Settings, port: int = 8765, open_browser: bool = True, window: bool = True) -> None:
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
    app.httpd = httpd
    url = f"http://127.0.0.1:{port}/"
    print(f"ตรวจสอบสิทธิ สปสช. v{__version__} — พัฒนาโดย {__author__}")
    print(f"เปิดหน้าจอที่ {url}   (ปิดหน้าต่างนี้ = ปิดโปรแกรม / กด Ctrl+C)")
    if window and open_browser:
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        if _open_app_window(url):
            return  # ปิดหน้าต่าง = ปิดโปรแกรม
        print("(ไม่มี WebView ในเครื่อง — เปิดในเบราว์เซอร์แทน)")
        webbrowser.open(url)
        try:
            t.join()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.shutdown()
            httpd.server_close()
        return
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
