"""การตั้งค่าโปรแกรม อ่านจาก environment variables / ไฟล์ .env

ค่าทั้งหมดดูตัวอย่างได้ที่ .env.example
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---- Base URL ตามเอกสาร สปสช. ----
SRM_API_BASE = {
    "test": "https://tsrm.nhso.go.th",
    "prod": "https://srm.nhso.go.th",
}
SRM_PORTAL_BASE = {
    "test": "https://tsrmportal.nhso.go.th",
    "prod": "https://srmportal.nhso.go.th",
}
ENDPOINT_BASE = {
    "test": "https://test.nhso.go.th/nhsoendpoint/",
    "prod": "https://nhsoapi.nhso.go.th/nhsoendpoint/",
}


def _load_dotenv(path: Path) -> None:
    """อ่านไฟล์ .env แบบง่าย (ไม่ต้องติดตั้ง python-dotenv) — ไม่ทับค่าที่ตั้งไว้แล้ว"""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def default_srm_token_file() -> Path:
    """ตำแหน่งไฟล์ token ที่ SRM Smart Card SSO สร้างให้: %userprofile%\\SRM Smart Card Single Sign-On\\token.txt"""
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    return home / "SRM Smart Card Single Sign-On" / "token.txt"


@dataclass
class Settings:
    env: str = "prod"                       # prod (ค่าเริ่มต้น ใช้งานจริง) | test
    # --- SRM ตรวจสอบสิทธิ ---
    srm_token_file: Path = field(default_factory=default_srm_token_file)
    srm_access_token: str = ""              # ถ้าระบุ จะใช้แทนการอ่านไฟล์
    srm_api_base: str = ""
    srm_portal_base: str = ""
    srm_rights_path: str = "/api/ucws/v1/right-search"
    srm_refresh_path: str = "/api/scard/access-token"
    # --- NHSO ENDPOINT ปิดสิทธิ ---
    endpoint_token: str = ""                # TOKEN KEY ที่ สปสช. ส่งทางอีเมล (ผูกกับ HCODE)
    endpoint_base: str = ""
    # path ต้องตรวจกับ Swagger UI ของแต่ละ Zone อีกครั้ง (เอกสาร v8.0 ไม่ได้ระบุ path)
    endpoint_claim_path: str = "api/nhso-claim-detail"
    endpoint_cancel_path: str = "api/nhso-claim-detail"
    endpoint_cancel_method: str = "PUT"
    hcode: str = ""
    source_id: str = ""
    recorder_pid: str = ""
    # --- ทั่วไป ---
    timeout: float = 30.0
    batch_delay: float = 0.5                # หน่วงระหว่าง request ในโหมด batch (วินาที)
    srm_cache_seconds: int = 600            # ตรวจคนเดิมซ้ำภายในเวลานี้ ใช้ผลเดิม ไม่เรียก API ซ้ำ (ประหยัดโควตา)
    batch_limit: int = 300                  # check-batch เกินจำนวนนี้ต้องยืนยัน (--allow-large)
    db_path: Path = Path("nhso_right_check_log.sqlite3")
    env_file: Path = Path(".env")
    open_mode: str = "window"               # window = หน้าต่างโปรแกรม, browser = เปิดในเบราว์เซอร์
    agent_url: str = "http://localhost:8189"  # NHSO Secure SmartCard Agent (เสียบบัตร)
    agent_dir: str = ""                       # โฟลเดอร์ที่ติดตั้ง Agent (ว่าง = หาเองจาก service)

    @classmethod
    def load(cls, env_file: str | os.PathLike | None = ".env") -> "Settings":
        if env_file:
            _load_dotenv(Path(env_file))
        g = os.environ.get
        env = (g("NHSO_ENV", "prod") or "prod").lower()
        if env not in ("test", "prod"):
            raise ValueError("NHSO_ENV ต้องเป็น test หรือ prod")
        s = cls(env=env)
        if env_file:
            s.env_file = Path(env_file)
        if g("SRM_TOKEN_FILE"):
            s.srm_token_file = Path(os.path.expandvars(g("SRM_TOKEN_FILE")))
        s.srm_access_token = g("SRM_ACCESS_TOKEN", "")
        s.srm_api_base = g("SRM_API_BASE") or SRM_API_BASE[env]
        s.srm_portal_base = g("SRM_PORTAL_BASE") or SRM_PORTAL_BASE[env]
        s.srm_rights_path = g("SRM_RIGHTS_PATH", s.srm_rights_path)
        s.srm_refresh_path = g("SRM_REFRESH_PATH", s.srm_refresh_path)
        s.endpoint_token = g("NHSO_ENDPOINT_TOKEN", "")
        s.endpoint_base = g("NHSO_ENDPOINT_BASE") or ENDPOINT_BASE[env]
        s.endpoint_claim_path = g("NHSO_CLAIM_PATH", s.endpoint_claim_path)
        s.endpoint_cancel_path = g("NHSO_CANCEL_PATH", s.endpoint_cancel_path)
        s.endpoint_cancel_method = g("NHSO_CANCEL_METHOD", s.endpoint_cancel_method).upper()
        s.hcode = g("HCODE", "")
        s.source_id = g("SOURCE_ID", "") or s.hcode
        s.recorder_pid = g("RECORDER_PID", "")
        s.timeout = float(g("HTTP_TIMEOUT", s.timeout))
        s.batch_delay = max(0.2, float(g("BATCH_DELAY", s.batch_delay)))
        s.srm_cache_seconds = int(g("SRM_CACHE_SECONDS", s.srm_cache_seconds))
        s.batch_limit = int(g("BATCH_LIMIT", s.batch_limit))
        s.open_mode = "browser" if (g("OPEN_MODE", "window") or "").lower() == "browser" else "window"
        s.agent_url = (g("AGENT_URL", s.agent_url) or s.agent_url).strip()
        s.agent_dir = (g("AGENT_DIR", "") or "").strip()
        if g("DB_PATH"):
            s.db_path = Path(g("DB_PATH"))
        return s


def save_env(path: str | os.PathLike, updates: dict[str, str]) -> None:
    """อัปเดตค่าในไฟล์ .env (แก้บรรทัดเดิม/เพิ่มบรรทัดใหม่ เก็บบรรทัดอื่นไว้ตามเดิม) และตั้งสิทธิไฟล์ให้เจ้าของอ่านได้คนเดียว"""
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
    done = set()
    for i, raw in enumerate(lines):
        key = raw.split("=", 1)[0].strip().lstrip("#").strip() if "=" in raw else ""
        if key in updates and key not in done:
            lines[i] = f"{key}={updates[key]}"
            done.add(key)
    for k, v in updates.items():
        if k not in done:
            lines.append(f"{k}={v}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    for k, v in updates.items():   # ให้ค่าใหม่มีผลทันทีโดยไม่ต้องปิดเปิดโปรแกรม
        os.environ[k] = v
