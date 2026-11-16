"""คำสั่งใช้งาน:

  python -m nhso_right_check token show|set|clear     จัดการ SRM token (อ่านอัตโนมัติจาก token.txt หรือวางเอง)
  python -m nhso_right_check check <PID>              ตรวจสอบสิทธิรายคน
  python -m nhso_right_check check-batch in.csv       ตรวจสอบสิทธิหลายคน -> CSV
  python -m nhso_right_check close --pid ... --service PG0060001 --privilege 700 [--send]
  python -m nhso_right_check close-batch in.csv [--send]
  python -m nhso_right_check cancel --trans <transactionId> [--seq N] [--send]
  python -m nhso_right_check history

ค่าเริ่มต้นเป็น DRY-RUN (แสดง JSON ที่จะส่ง แต่ยังไม่ส่ง) ต้องใส่ --send ถึงจะส่งจริง
"""
from __future__ import annotations

import argparse
import csv
import getpass
import json
import logging
import sys
import time
from pathlib import Path

from . import __author__, __version__
from .config import Settings
from .endpoint import ClaimRequest, EndpointClient, EndpointError, build_claim_payload, is_success
from .srm import MANUAL_TOKEN_FILE, SRMClient, SRMError, main_fund, parse_token_text, summarize
from .store import ClaimStore
from .validation import ValidationError, mask_pid

log = logging.getLogger("nhso_right_check")


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def _safe_cell(v):
    """กัน CSV/Excel formula injection: ค่าที่ขึ้นต้นด้วย = + - @ ให้ใส่ ' นำหน้า"""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols: list[str] = []
    for r in rows:
        cols += [k for k in r if k not in cols]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig ให้ Excel อ่านภาษาไทยได้
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({k: _safe_cell(v) for k, v in r.items()} for r in rows)


# ---------------- token ----------------
def cmd_token(a, s: Settings) -> int:
    manual = Path(MANUAL_TOKEN_FILE)
    if a.action == "set":
        print("วาง access_token (หรือเนื้อหาทั้งไฟล์ token.txt) แล้วกด Enter:")
        text = getpass.getpass("token: ") if not a.visible else input("token: ")
        pair = parse_token_text(text)
        from .srm import write_private
        write_private(manual, text.strip())
        print(f"บันทึกแล้วที่ {manual.resolve()} (ยาว {len(pair.access_token)} ตัวอักษร)")
        return 0
    if a.action == "clear":
        manual.unlink(missing_ok=True)
        print("ลบ token ที่วางเองแล้ว")
        return 0
    # show
    for label, p in (("token.txt (SRM SSO)", Path(s.srm_token_file)), ("วางเอง", manual)):
        if p.is_file():
            pair = parse_token_text(p.read_text(encoding="utf-8-sig"))
            age = (time.time() - p.stat().st_mtime) / 60
            print(f"[พบ] {label}: {p}\n      แก้ไขล่าสุด {age:.0f} นาทีที่แล้ว | access ...{pair.access_token[-6:]} "
                  f"| refresh_token: {'มี' if pair.refresh_token else 'ไม่มี'}")
        else:
            print(f"[ไม่พบ] {label}: {p}")
    return 0


# ---------------- ตรวจสอบสิทธิ ----------------
QUOTA_NOTE = ("⚠ การตรวจสอบสิทธิผ่าน API ใช้โควตาของหน่วยบริการ การตรวจจำนวนมากหรือซ้ำซ้อน อาจทำให้โควตาหมด\n"
              "  ถ้าต้องตรวจเป็นกลุ่ม แนะนำใช้ https://srmportal.nhso.go.th/ → ตรวจสอบสิทธิแบบกลุ่ม")


def _srm_with_usage(s: Settings) -> SRMClient:
    store = ClaimStore(s.db_path)
    return SRMClient(s, on_call=store.count_call)


def cmd_check(a, s: Settings) -> int:
    res = _srm_with_usage(s).right_search(a.pid)
    _print_json(res if a.raw else summarize(res))
    return 0


def cmd_check_batch(a, s: Settings) -> int:
    srm = _srm_with_usage(s)
    raw_rows = _read_csv(a.input)
    seen, rows = set(), []
    for r in raw_rows:  # ตัดเลขบัตรซ้ำ ไม่เรียก API ซ้ำ
        pid = (r.get("pid") or "").strip()
        if pid and pid not in seen:
            seen.add(pid)
            rows.append(r)
    print(QUOTA_NOTE)
    if len(raw_rows) != len(rows):
        print(f"ตัดเลขบัตรซ้ำ/ว่างออก {len(raw_rows) - len(rows)} แถว เหลือ {len(rows)} คน")
    if len(rows) > s.batch_limit and not a.allow_large:
        print(f"\nรายชื่อมี {len(rows)} คน มากกว่าที่ตั้งไว้ ({s.batch_limit}) — แนะนำตรวจผ่าน SRM Portal แบบกลุ่มแทน\n"
              "ถ้ายืนยันจะตรวจผ่าน API ให้ใส่ --allow-large")
        return 1
    out = []
    for i, r in enumerate(rows, 1):
        pid = r.get("pid", "")
        try:
            row = {"input_pid": pid, "status": "OK", **summarize(srm.right_search(pid))}
        except (SRMError, ValidationError) as e:
            row = {"input_pid": pid, "status": "ERROR", "error": str(e)}
        out.append(row)
        print(f"[{i}/{len(rows)}] {mask_pid(pid)} {row['status']} {row.get('mainInscl', row.get('error', ''))}")
        time.sleep(s.batch_delay)
    _write_csv(a.output, out)
    print(f"เขียนผลลัพธ์ -> {a.output}")
    return 0


# ---------------- ปิดสิทธิ ----------------
def _close_one(r: dict, s: Settings, srm: SRMClient | None, ep: EndpointClient, store: ClaimStore | None,
               send: bool) -> dict:
    pid = r["pid"]
    inscl = (r.get("main_inscl") or "").upper()
    if not inscl:
        if srm is None:
            raise ValidationError("ต้องระบุ main_inscl หรือเปิดการตรวจสอบสิทธิผ่าน SRM")
        right = srm.right_search(pid)
        if right.get("deathDate"):
            raise ValidationError(f"ผู้รับบริการมีสถานะเสียชีวิต ({right['deathDate']}) — ไม่ปิดสิทธิ")
        fund = main_fund(right) or {}
        inscl = ((fund.get("mainInscl") or {}).get("id") or "NULL").upper()
    req = ClaimRequest(
        pid=pid,
        claim_service_code=r.get("service_code", ""),
        main_inscl_code=inscl,
        privilege_amount=r.get("privilege_amount", 0),
        paid_amount=r.get("paid_amount", 0) or 0,
        total_amount=r.get("total_amount") or None,
        service_datetime=r.get("service_datetime") or None,
        invoice_datetime=r.get("invoice_datetime") or None,
        visit_number=r.get("visit_number", ""),
        department_code=r.get("department_code", ""),
        department_name=r.get("department_name", ""),
        transaction_id=r.get("transaction_id", ""),
    )
    payload = build_claim_payload(req, s)
    if store is not None and not str(r.get("allow_duplicate", "")).lower() in ("1", "true", "y", "yes"):
        dup = store.find_duplicate(payload["pid"], payload["claimServiceCode"], payload["serviceDateTime"])
        if dup:
            raise ValidationError(
                f"พบการปิดสิทธิสำเร็จแล้ว (คนเดิม รหัสบริการเดิม วันเดียวกัน) authenCode={dup['authen_code']} "
                f"transactionId={dup['transaction_id']} — ถ้าตั้งใจส่งซ้ำจริง ให้ใช้ --allow-duplicate")
    if not send:
        return {"dryRun": True, "payload": payload}
    resp = ep.claim(payload)
    ok = is_success(resp)
    if store:
        store.log(env=s.env, action="CLAIM", request=payload, response=resp, success=ok)
    return {"success": ok, "transactionId": payload["transactionId"], "mainInsclCode": inscl, **resp}


def _confirm_prod(s: Settings, send: bool, yes: bool) -> bool:
    if send and s.env == "prod" and not yes:
        ans = input("⚠ กำลังส่งข้อมูลจริงไป PRODUCTION ยืนยันพิมพ์ YES: ")
        return ans.strip() == "YES"
    return True


def cmd_close(a, s: Settings) -> int:
    if not _confirm_prod(s, a.send, a.yes):
        print("ยกเลิก")
        return 1
    store = ClaimStore(s.db_path)
    row = {"allow_duplicate": "1" if a.allow_duplicate else "", "pid": a.pid, "service_code": a.service, "privilege_amount": a.privilege, "paid_amount": a.paid,
           "total_amount": a.total, "service_datetime": a.service_datetime, "invoice_datetime": a.invoice_datetime,
           "visit_number": a.vn or "", "main_inscl": a.main_inscl or "", "department_code": a.dept_code or "",
           "department_name": a.dept_name or ""}
    srm = None if a.main_inscl else SRMClient(s, on_call=store.count_call)
    result = _close_one(row, s, srm, EndpointClient(s), store, a.send)
    _print_json(result)
    if result.get("dryRun"):
        print("\n(DRY-RUN — ยังไม่ได้ส่ง ใส่ --send เพื่อส่งจริง)")
        return 0
    return 0 if result.get("success") else 2


def cmd_close_batch(a, s: Settings) -> int:
    if not _confirm_prod(s, a.send, a.yes):
        print("ยกเลิก")
        return 1
    rows = _read_csv(a.input)
    store = ClaimStore(s.db_path)
    srm, ep = SRMClient(s, on_call=store.count_call), EndpointClient(s)
    print(QUOTA_NOTE)
    out = []
    for i, r in enumerate(rows, 1):
        try:
            res = _close_one(r, s, srm, ep, store, a.send)
            if res.get("dryRun"):
                p = res["payload"]
                row = {**r, "status": "DRY-RUN", "mainInsclCode": p["mainInsclCode"],
                       "transactionId": p["transactionId"], "totalAmount": p["totalAmount"]}
            else:
                row = {**r, "status": "OK" if res["success"] else "FAIL",
                       **{k: res.get(k, "") for k in ("transactionId", "mainInsclCode", "seq", "authenCode", "dataError")}}
        except (SRMError, EndpointError, ValidationError, KeyError) as e:
            row = {**r, "status": "ERROR", "error": str(e)}
        out.append(row)
        print(f"[{i}/{len(rows)}] {mask_pid(r.get('pid', ''))} {row['status']} "
              f"{row.get('authenCode') or row.get('dataError') or row.get('error') or ''}")
        time.sleep(s.batch_delay)
    _write_csv(a.output, out)
    print(f"เขียนผลลัพธ์ -> {a.output}" + ("" if a.send else "  (DRY-RUN)"))
    return 0


def cmd_cancel(a, s: Settings) -> int:
    seq = a.seq
    store = ClaimStore(s.db_path)
    if seq is None:
        row = store.find_claim(a.trans)
        if not row or not row["seq"]:
            print("ไม่พบ seq ของ transactionId นี้ในประวัติ — ระบุ --seq เอง")
            return 1
        seq = row["seq"]
    payload = {"seq": int(seq), "transactionId": a.trans, "paymentStatus": "CANCEL"}
    if not a.send:
        _print_json({"dryRun": True, "method": s.endpoint_cancel_method, "path": s.endpoint_cancel_path,
                     "payload": payload})
        return 0
    if not _confirm_prod(s, True, a.yes):
        return 1
    resp = EndpointClient(s).cancel(seq, a.trans)
    ok = is_success(resp)
    store.log(env=s.env, action="CANCEL", request=payload, response=resp, success=ok)
    _print_json({"success": ok, **resp})
    return 0 if ok else 2


def cmd_history(a, s: Settings) -> int:
    store = ClaimStore(s.db_path)
    for r in store.recent(a.limit):
        print(f"{r['created_at']} {r['env']:4} {r['action']:6} {'OK ' if r['success'] else 'ERR'} "
              f"trans={r['transaction_id']} seq={r['seq'] or '-'} authen={r['authen_code'] or '-'} "
              f"pid={mask_pid(r['pid'] or '')} {r['data_error'] or ''}")
    return 0


# ---------------- main ----------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nhso_right_check", description="ตรวจสอบสิทธิ (SRM) + ปิดสิทธิ (NHSO ENDPOINT)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__} — พัฒนาโดย {__author__}")
    p.add_argument("--env-file", default=".env")
    p.add_argument("--env", choices=["test", "prod"], help="ทับค่า NHSO_ENV")
    p.add_argument("--token", help="ใส่ SRM access token เอง (แทนการอ่าน token.txt)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("token", help="ดู/ตั้ง SRM token")
    t.add_argument("action", choices=["show", "set", "clear"], nargs="?", default="show")
    t.add_argument("--visible", action="store_true", help="แสดงตัวอักษรขณะวาง token")
    t.set_defaults(func=cmd_token)

    c = sub.add_parser("check", help="ตรวจสอบสิทธิรายคน")
    c.add_argument("pid")
    c.add_argument("--raw", action="store_true", help="แสดง JSON เต็มจาก สปสช.")
    c.set_defaults(func=cmd_check)

    cb = sub.add_parser("check-batch", help="ตรวจสอบสิทธิจาก CSV (คอลัมน์ pid)")
    cb.add_argument("input")
    cb.add_argument("-o", "--output", default="check_result.csv")
    cb.add_argument("--allow-large", action="store_true", help="ยืนยันตรวจรายชื่อจำนวนมากผ่าน API")
    cb.set_defaults(func=cmd_check_batch)

    cl = sub.add_parser("close", help="ปิดสิทธิรายคน")
    cl.add_argument("--pid", required=True)
    cl.add_argument("--service", required=True, help="claimServiceCode เช่น PG0060001")
    cl.add_argument("--privilege", required=True, help="ค่าบริการที่เบิกได้")
    cl.add_argument("--paid", default="0", help="ค่าบริการที่เบิกไม่ได้")
    cl.add_argument("--total", help="ค่าบริการทั้งหมด (default = paid + privilege)")
    cl.add_argument("--service-datetime", help="YYYY-MM-DD HH:MM:SS (default = ตอนนี้)")
    cl.add_argument("--invoice-datetime", help="YYYY-MM-DD HH:MM:SS (default = ตอนนี้)")
    cl.add_argument("--vn")
    cl.add_argument("--main-inscl", help="ข้ามการตรวจ SRM และใช้สิทธินี้ (UCS/WEL/OFC/LGO/SSS/...)")
    cl.add_argument("--dept-code")
    cl.add_argument("--dept-name")
    cl.add_argument("--send", action="store_true", help="ส่งจริง (ไม่ใส่ = dry-run)")
    cl.add_argument("--allow-duplicate", action="store_true", help="ยอมส่งซ้ำ (คนเดิม รหัสบริการเดิม วันเดียวกัน)")
    cl.add_argument("--yes", action="store_true", help="ไม่ต้องถามยืนยัน production")
    cl.set_defaults(func=cmd_close)

    cbt = sub.add_parser("close-batch", help="ปิดสิทธิจาก CSV")
    cbt.add_argument("input")
    cbt.add_argument("-o", "--output", default="close_result.csv")
    cbt.add_argument("--send", action="store_true")
    cbt.add_argument("--yes", action="store_true")
    cbt.set_defaults(func=cmd_close_batch)

    ca = sub.add_parser("cancel", help="ยกเลิกการปิดสิทธิ")
    ca.add_argument("--trans", required=True, help="transactionId")
    ca.add_argument("--seq", help="seq (ถ้าไม่ใส่ จะค้นจากประวัติ)")
    ca.add_argument("--send", action="store_true")
    ca.add_argument("--yes", action="store_true")
    ca.set_defaults(func=cmd_cancel)

    h = sub.add_parser("history", help="ดูประวัติการส่ง")
    h.add_argument("-n", "--limit", type=int, default=20)
    h.set_defaults(func=cmd_history)

    rt = sub.add_parser("rights", help="ค้นหาตารางรหัสสิทธิ (vr_l_right)")
    rt.add_argument("query", nargs="?", default="", help="รหัสหรือคำค้น เช่น UCS, ข้าราชการ")
    rt.set_defaults(func=cmd_rights)

    u = sub.add_parser("ui", help="เปิดหน้าจอโปรแกรม (Web UI ในเครื่อง)")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--no-browser", action="store_true")
    u.set_defaults(func=cmd_ui)
    return p


def cmd_rights(a, s: Settings) -> int:
    from .rights import all_rights
    q = a.query.strip().lower()
    n = 0
    for r in all_rights():
        if not q or q in r["id"].lower() or q in r["name"].lower() or q in r["en"].lower():
            print(f"{r['id']:5} [{r['groupId'] or '-'}] {r['name']}")
            n += 1
    print(f"-- {n} รายการ")
    return 0


def cmd_ui(a, s: Settings) -> int:
    from .web import serve
    serve(s, port=a.port, open_browser=not a.no_browser)
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # ให้ Windows console แสดงภาษาไทย
    if getattr(sys, "frozen", False):
        # โปรแกรมที่ build แล้ว: ใช้โฟลเดอร์ของไฟล์โปรแกรมเป็นที่เก็บ .env / ประวัติ
        import os
        os.chdir(Path(sys.executable).resolve().parent)
    if argv is None and len(sys.argv) == 1:
        argv = ["ui"]  # ดับเบิลคลิก / รันเปล่าๆ = เปิดหน้าจอ
    a = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        import os
        if a.env:
            os.environ["NHSO_ENV"] = a.env
        s = Settings.load(a.env_file)
        if a.token:
            s.srm_access_token = a.token
        return a.func(a, s)
    except (SRMError, EndpointError, ValidationError) as e:
        print(f"ผิดพลาด: {e}", file=sys.stderr)
        body = getattr(e, "body", None)
        if body:
            print(f"รายละเอียด: {body}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
