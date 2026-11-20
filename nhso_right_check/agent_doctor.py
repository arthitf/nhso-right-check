"""ตรวจ / ซ่อม NHSO Secure SmartCard Agent (Windows)

ปัญหาที่พบบ่อย: อ่านบัตรแล้วขึ้น PKIX path building failed เพราะ JRE 8 รุ่นเก่าที่มากับ Agent
ไม่รู้จักใบรับรอง SSL ของ สปสช. — แนวทาง สปสช. (11 พ.ย. 2569) คืออัปเดต JRE 8 (Eclipse Temurin)

โมดูลนี้ตรวจสถานะ (โฟลเดอร์ Agent, รุ่น Java, service) และสั่งรันสคริปต์ tools/fix-agent.ps1
แบบ Administrator เพื่อเปลี่ยน JRE ตามขั้นตอนของ สปสช. (Stop service → jre เดิมเป็น jre_old →
วาง jre ใหม่ → Start service) โปรแกรมหลักไม่ต้องรันด้วยสิทธิ์ Administrator
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SERVICE = "NHSOSecureSmartCardAgent"
OLD_UPDATE = 400          # JRE 8 ที่ต่ำกว่า 8u400 (ก่อนปี 2024) ถือว่าเก่า เสี่ยงขึ้น PKIX
IS_WINDOWS = os.name == "nt"


def _run(cmd: list[str], timeout: int = 15) -> str:
    kw = {"creationflags": 0x08000000} if IS_WINDOWS else {}   # CREATE_NO_WINDOW
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace", **kw)
        return r.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _is_agent_dir(p: Path | None) -> bool:
    return bool(p) and (p / "secureagent.jar").is_file()


def service_info() -> dict:
    """สถานะ service และโฟลเดอร์ที่ติดตั้ง (จาก sc qc / sc query)"""
    if not IS_WINDOWS:
        return {"installed": False, "state": None, "dir": ""}
    q = _run(["sc", "query", SERVICE])
    m = re.search(r"STATE\s*:\s*\d+\s+(\w+)", q)
    cfg = _run(["sc", "qc", SERVICE])
    path = ""
    m2 = re.search(r"BINARY_PATH_NAME\s*:\s*(.+)", cfg)
    if m2:
        raw = m2.group(1).strip()
        exe = raw[1:raw.index('"', 1)] if raw.startswith('"') else (re.match(r"(?i)(.*?\.exe)", raw) or [None, raw])[1]
        path = str(Path(exe).parent)
    return {"installed": bool(m or path), "state": m.group(1) if m else None, "dir": path}


def find_agent_dir(hint: str = "", svc: dict | None = None) -> Path | None:
    cands = [hint, (svc or {}).get("dir", ""), r"C:\NHSOSecureSmartCardAgent",
             os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "NHSOSecureSmartCardAgent")]
    for c in cands:
        if c and _is_agent_dir(Path(c)):
            return Path(c)
    return None


def java_version(agent_dir: Path | None, sub: str = "jre") -> str:
    if not agent_dir:
        return ""
    rel = agent_dir / sub / "release"
    try:
        m = re.search(r'^JAVA_VERSION="?([^"\r\n]+)"?', rel.read_text(encoding="utf-8", errors="replace"), re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""


def java_update(v: str) -> int | None:
    """'1.8.0_202' → 202"""
    m = re.match(r"1\.8\.0_(\d+)", v or "")
    return int(m.group(1)) if m else None


def result_file() -> Path:
    base = os.environ.get("ProgramData") or str(Path.home())
    return Path(base) / "nhso-right-check" / "agent-fix-result.json"


def last_result() -> dict | None:
    try:
        f = result_file()
        d = json.loads(f.read_text(encoding="utf-8-sig"))
        d["mtime"] = f.stat().st_mtime
        return d
    except (OSError, ValueError):
        return None


def diagnose(hint: str = "", online: bool | None = None, last_error: str = "") -> dict:
    svc = service_info()
    d = find_agent_dir(hint, svc)
    ver = java_version(d)
    upd = java_update(ver)
    old = upd is not None and upd < OLD_UPDATE
    issues: list[str] = []
    if not IS_WINDOWS:
        issues.append("ตรวจ/ซ่อม Agent ใช้ได้บน Windows เท่านั้น (Agent ของ สปสช. เป็นโปรแกรม Windows)")
    elif not d:
        issues.append("ไม่พบโฟลเดอร์ Agent (ที่มี secureagent.jar) — ระบุโฟลเดอร์เอง หรือติดตั้ง Agent ก่อน")
    if d and not svc["installed"]:
        issues.append("ยังไม่ได้ติดตั้งเป็น service — คลิกขวา install.bat แล้ว Run as Administrator")
    elif svc["state"] and svc["state"] != "RUNNING":
        issues.append(f"service {SERVICE} อยู่ในสถานะ {svc['state']}")
    if old:
        issues.append(f"Java ที่มากับ Agent เป็นรุ่นเก่า ({ver}) — เสี่ยงขึ้น PKIX path building failed "
                      "ควรอัปเดต JRE 8 (Temurin) ตามแนวทาง สปสช.")
    if "PKIX" in (last_error or "") or "SSLHandshake" in (last_error or ""):
        issues.append("อ่านบัตรล่าสุดขึ้น PKIX / SSLHandshake — ต้องอัปเดต JRE 8 ของ Agent")
    if online is False and d and svc["state"] == "RUNNING":
        issues.append("service ทำงานอยู่แต่ต่อ localhost:8189 ไม่ได้ — ตรวจ port 8189 หรือดู log ของ Agent")
    return {
        "windows": IS_WINDOWS,
        "agentDir": str(d) if d else "",
        "javaVersion": ver,
        "javaOld": old,
        "hasBackup": bool(d and (d / "jre_old").is_dir()),
        "backupVersion": java_version(d, "jre_old") if d else "",
        "service": svc["state"] or ("ไม่ได้ติดตั้ง" if IS_WINDOWS and not svc["installed"] else None),
        "online": online,
        "issues": issues,
        "canFix": IS_WINDOWS and bool(d),
        "last": last_result(),
    }


def _script_dir() -> Path:
    """โฟลเดอร์สคริปต์ (ในโปรแกรมที่ build แล้วอยู่ใน _MEIPASS) → คัดลอกไว้ข้างโปรแกรม ให้ IT เปิดใช้เองได้ด้วย"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    src = base / "nhso_right_check" / "tools"
    if not src.is_dir():
        src = Path(__file__).resolve().parent / "tools"
    dst = Path.cwd() / "tools"
    try:
        dst.mkdir(exist_ok=True)
        for f in ("fix-agent.ps1", "fix-agent.bat"):
            shutil.copy2(src / f, dst / f)
        return dst
    except OSError:
        return src


def launch(mode: str, agent_dir: str = "") -> dict:
    """เปิดสคริปต์ซ่อมแบบ Administrator (Windows จะถามยืนยันสิทธิ์) — ทำงานในหน้าต่างของตัวเอง"""
    if mode not in ("fix", "rollback"):
        raise ValueError("mode ต้องเป็น fix หรือ rollback")
    if not IS_WINDOWS:
        raise RuntimeError("ซ่อม Agent ได้บน Windows เท่านั้น")
    d = find_agent_dir(agent_dir, service_info())
    if not d:
        raise RuntimeError("ไม่พบโฟลเดอร์ Agent (ที่มี secureagent.jar)")
    script = _script_dir() / "fix-agent.ps1"
    rf = result_file()
    try:
        rf.unlink()
    except OSError:
        pass

    def q(s: str) -> str:   # ใส่ใน single-quote ของ PowerShell
        return "'" + s.replace("'", "''") + "'"
    args = (f'-NoProfile -ExecutionPolicy Bypass -File "{script}" -Mode {mode} '
            f'-AgentDir "{d}" -ResultFile "{rf}" -NoPause')
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
           f"try {{ Start-Process -FilePath powershell.exe -Verb RunAs -ArgumentList {q(args)}; exit 0 }} catch {{ exit 5 }}"]
    kw = {"creationflags": 0x08000000}
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)
    if r.returncode == 5:
        raise RuntimeError("ไม่ได้รับสิทธิ์ Administrator (กดยกเลิกที่หน้าต่างยืนยันของ Windows)")
    if r.returncode != 0:
        raise RuntimeError("เปิดสคริปต์ซ่อมไม่สำเร็จ: " + (r.stderr or r.stdout or "").strip()[:300])
    return {"launched": True, "mode": mode, "agentDir": str(d), "script": str(script), "resultFile": str(rf)}
