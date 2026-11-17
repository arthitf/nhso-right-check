"""Client ตรวจสอบสิทธิผ่าน SRM (API ตรวจสอบสิทธิใหม่ ใช้แทน ucws เดิมที่ยุติ 15 ม.ค. 2570)

GET {SRM}/api/ucws/v1/right-search?pid=xxxxxxxxxxxxx
Authorization: Bearer <access_token จาก token.txt>
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .config import Settings
from .rights import lookup as right_lookup
from .validation import mask_pid, require_pid

log = logging.getLogger(__name__)

# ไฟล์เก็บ token ที่ผู้ใช้วางเอง (ไม่ commit ขึ้น GitHub — อยู่ใน .gitignore)
MANUAL_TOKEN_FILE = ".srm_token.txt"  # nosec B105 - ชื่อไฟล์ ไม่ใช่รหัสผ่าน


class SRMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str = ""


def parse_token_text(text: str) -> TokenPair:
    """อ่าน token.txt — รองรับทั้งแบบ JSON, key=value และ token เปล่าๆ"""
    text = (text or "").strip().lstrip("﻿")
    if not text:
        raise SRMError("ไฟล์ token ว่าง — ให้ Login SRM Portal ด้วย Smart Card ใหม่")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            access = data.get("access_token") or data.get("accessToken") or data.get("token") or ""
            refresh = data.get("refresh_token") or data.get("refreshToken") or ""
            if access:
                return TokenPair(access, refresh)
    except json.JSONDecodeError:
        pass
    kv: dict[str, str] = {}
    for line in text.splitlines():
        for sep in ("=", ":"):
            if sep in line:
                k, _, v = line.partition(sep)
                # token.txt ของ SRM SSO ใช้ access-token= / refresh-token= (ขีดกลาง) → ทำให้เป็นรูปเดียวกัน
                kv[k.strip().strip('"').lower().replace("-", "_")] = v.strip().strip('",')
                break
    access = kv.get("access_token") or kv.get("accesstoken") or kv.get("token")
    if access:
        return TokenPair(access, kv.get("refresh_token") or kv.get("refreshtoken") or "")
    first = text.splitlines()[0].strip()
    if "=" in first and first.split("=", 1)[1].startswith("eyJ"):   # key=JWT ที่ไม่รู้จักชื่อ key
        first = first.split("=", 1)[1].strip()
    if first.lower().startswith("bearer "):
        first = first[7:].strip()
    return TokenPair(first)


# ติดลิมิต (HTTP 429): จำเวลาที่ต้องรอไว้ร่วมกันทุก client ในโปรแกรม เพื่อไม่เรียกซ้ำจนครบเวลา
_BLOCKED_UNTIL = 0.0


def _retry_after(r) -> int:
    """อ่านเวลาที่ต้องรอจาก retryAfterSeconds / Retry-After / ข้อความ 'Retry after N second(s)'"""
    try:
        body = r.json()
        if isinstance(body, dict) and str(body.get("retryAfterSeconds", "")).isdigit():
            return int(body["retryAfterSeconds"])
    except ValueError:
        pass
    h = (getattr(r, "headers", None) or {}).get("Retry-After", "")
    if str(h).isdigit():
        return int(h)
    m = re.search(r"Retry after (\d+)", r.text or "")
    return int(m.group(1)) if m else 0


def _wait_text(sec: float) -> str:
    sec = max(0, int(sec + 0.999))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} ชม. {m} นาที"
    return f"{m} นาที {s} วินาที" if m else f"{s} วินาที"


def _limit_msg(remain: float) -> str:
    until = time.strftime("%H:%M", time.localtime(time.time() + remain))
    return (f"ติดลิมิตการเรียก API ตรวจสอบสิทธิ (HTTP 429) — ต้องรออีก {_wait_text(remain)} (ถึงประมาณ {until} น.) "
            "โปรแกรมจะไม่เรียกซ้ำจนครบเวลา · ระหว่างรอใช้เสียบบัตรผ่าน Agent หรือ SRM Portal → ตรวจสอบสิทธิแบบกลุ่ม")


def rate_limit_remaining() -> float:
    return max(0.0, _BLOCKED_UNTIL - time.time())


class SRMClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None, on_call=None):
        self.s = settings
        self.http = session or requests.Session()
        self._token: TokenPair | None = None
        self._cache: dict[str, tuple[float, dict]] = {}
        self.on_call = on_call          # callback นับจำนวนการเรียก API จริง (สำหรับเตือนโควตา)
        self.last_cached_age: float | None = None

    # ---------- token ----------
    def _load_token(self) -> TokenPair:
        """ลำดับการหา token:
        1) SRM_ACCESS_TOKEN / --token (ใส่เอง)
        2) %userprofile%\\SRM Smart Card Single Sign-On\\token.txt (อัปเดตทุกครั้งที่เสียบบัตร Login SRM)
        3) ไฟล์ token ที่บันทึกไว้ด้วยคำสั่ง `token set` (MANUAL_TOKEN_FILE)
        """
        if self.s.srm_access_token:
            return TokenPair(self.s.srm_access_token.strip())
        for path in (Path(self.s.srm_token_file), Path(MANUAL_TOKEN_FILE)):
            if path.is_file() and path.read_text(encoding="utf-8-sig").strip():
                log.info("ใช้ token จาก %s", path)
                return parse_token_text(path.read_text(encoding="utf-8-sig"))
        raise SRMError(
            f"ไม่พบไฟล์ token: {self.s.srm_token_file}\n"
            "วิธีแก้: (ก) เสียบบัตร → Login SRM Portal → กด 'คลิกเพื่อเข้าใช้งานระบบ SRM Portal' แล้วรันใหม่\n"
            "        (ข) หรือใส่ token เอง: python -m nhso_right_check token set  /  --token <access_token>"
        )

    def reload_token(self) -> TokenPair:
        """อ่าน token ใหม่ (ใช้หลังเสียบบัตร Login ใหม่ระหว่างรัน batch)"""
        self._token = None
        return self.token

    @property
    def token(self) -> TokenPair:
        if self._token is None:
            self._token = self._load_token()
        return self._token

    def refresh(self) -> TokenPair:
        """ขอ access token ใหม่ด้วย refresh_token (POST form-urlencoded)"""
        rt = self.token.refresh_token
        if not rt:
            raise SRMError("access token หมดอายุ และไม่มี refresh_token — ให้ Login SRM Portal ด้วย Smart Card ใหม่", 401)
        url = self.s.srm_portal_base.rstrip("/") + self.s.srm_refresh_path
        try:
            r = self.http.post(url, data={"refresh_token": rt}, timeout=self.s.timeout,
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
        except requests.RequestException as e:
            raise SRMError(f"เชื่อมต่อ SRM Portal ไม่ได้: {type(e).__name__}") from e
        if r.status_code != 200:
            raise SRMError(f"refresh token ไม่สำเร็จ (HTTP {r.status_code})", r.status_code, r.text)
        pair = parse_token_text(r.text)
        if not pair.refresh_token:
            pair.refresh_token = rt
        self._token = pair
        log.info("refresh SRM access token สำเร็จ")
        return pair

    # ---------- API ----------
    def _env_hint(self) -> str:
        if self.s.env == "test":
            return (" · ตอนนี้ตั้งเป็น Test Zone อยู่ token จาก SRM Portal ตัวจริงใช้กับ Test ไม่ได้ "
                    "→ เปลี่ยนเป็น Production ที่แท็บ ตั้งค่า")
        return ""

    def right_search(self, pid: str, force: bool = False) -> dict:
        """ตรวจสอบสิทธิปัจจุบันของผู้รับบริการ คืนค่า JSON ตาม spec

        ถ้าเพิ่งตรวจคนเดิมภายใน srm_cache_seconds จะคืนผลเดิม ไม่เรียก API ซ้ำ (ลดการใช้โควตาหน่วยบริการ)
        """
        pid = require_pid(pid)
        self.last_cached_age = None
        hit = self._cache.get(pid)
        if hit and not force and self.s.srm_cache_seconds > 0:
            age = time.time() - hit[0]
            if age < self.s.srm_cache_seconds:
                self.last_cached_age = age
                log.info("SRM right-search %s -> cache (%.0fs)", mask_pid(pid), age)
                return hit[1]
        remain = rate_limit_remaining()
        if remain > 0:
            raise SRMError(_limit_msg(remain), 429)
        url = self.s.srm_api_base.rstrip("/") + self.s.srm_rights_path
        for attempt in (1, 2):
            try:
                r = self.http.get(url, params={"pid": pid}, timeout=self.s.timeout,
                                  headers={"Authorization": f"Bearer {self.token.access_token}",
                                           "Accept": "application/json"})
            except requests.RequestException as e:
                raise SRMError(f"เชื่อมต่อ SRM ไม่ได้: {type(e).__name__} (ตรวจอินเทอร์เน็ต/proxy/VPN)") from e
            if r.status_code == 401 and attempt == 1:
                old = self.token.access_token
                try:  # เผื่อเพิ่งเสียบบัตร Login ใหม่ → token.txt เปลี่ยนแล้ว
                    if self.reload_token().access_token != old:
                        continue
                except SRMError:
                    pass
                try:
                    self.refresh()
                except SRMError as e:
                    raise SRMError(str(e) + self._env_hint(), e.status, e.body) from e
                continue
            break
        log.info("SRM right-search %s -> HTTP %s", mask_pid(pid), r.status_code)
        if self.on_call:
            self.on_call("srm")
        if r.status_code == 429:
            global _BLOCKED_UNTIL
            wait = _retry_after(r) or 300
            _BLOCKED_UNTIL = max(_BLOCKED_UNTIL, time.time() + wait)
            log.warning("SRM rate limit: รอ %s วินาที", wait)
            raise SRMError(_limit_msg(wait), 429, r.text)
        if r.status_code == 200:
            data = r.json()
            self._cache[pid] = (time.time(), data)
            return data
        messages = {
            400: "ข้อมูลไม่ถูกต้อง (เช่น pid ต้องเป็นตัวเลข 13 หลัก)",
            401: "token ไม่ถูกต้องหรือหมดอายุ — Login SRM Portal ใหม่" + self._env_hint(),
            404: "ไม่พบข้อมูลจากเลขประจำตัวประชาชนที่ส่งไป",
            500: "เกิดข้อผิดพลาดที่ Server สปสช.",
        }
        raise SRMError(messages.get(r.status_code, f"HTTP {r.status_code}"), r.status_code, r.text)


# ---------- helpers แปลงผลลัพธ์ ----------
def _name(obj: Any, key: str = "name") -> str:
    return (obj or {}).get(key, "") if isinstance(obj, dict) else (obj or "")


def main_fund(result: dict) -> dict | None:
    """เลือกกองทุนหลัก (fundType = Y) ถ้าไม่มีให้คืนตัวแรก"""
    funds = result.get("funds") or []
    for f in funds:
        if str(f.get("fundType", "")).upper() == "Y":
            return f
    return funds[0] if funds else None


def summarize(result: dict) -> dict:
    """แปลงผลตรวจสอบสิทธิให้เป็นแถวเดียว (ใช้แสดงผล / เขียน CSV)"""
    f = main_fund(result) or {}
    code = _name(f.get("mainInscl"), "id")
    rt = right_lookup(code)
    others = [_name(x.get("mainInscl"), "id") for x in (result.get("funds") or []) if x is not f]
    return {
        "pid": result.get("pid", ""),
        "name": " ".join(x for x in (result.get("tname"), result.get("fname"), result.get("lname")) if x),
        "sex": _name(result.get("sex")),
        "birthDate": result.get("birthDate", ""),
        "deathDate": result.get("deathDate") or "",
        "checkDate": result.get("checkDate", ""),
        "mainInscl": _name(f.get("mainInscl"), "id"),
        "mainInsclName": _name(f.get("mainInscl")) or rt["name"],
        "rightName": rt["name"],
        "rightEn": rt["en"],
        "rightGroup": rt["groupId"],
        "rightGroupName": rt["groupName"],
        "otherFunds": ",".join(c for c in others if c),
        "subInscl": _name(f.get("subInscl"), "id"),
        "subInsclName": _name(f.get("subInscl")),
        "startDateTime": f.get("startDateTime", ""),
        "expireDateTime": f.get("expireDateTime", ""),
        "paidModel": f.get("paidModel", ""),
        "hospMainOp": _name(f.get("hospMainOp"), "hcode"),
        "hospMainOpName": _name(f.get("hospMainOp"), "hname"),
        "hospSub": _name(f.get("hospSub"), "hcode"),
        "hospSubName": _name(f.get("hospSub"), "hname"),
        "hospMain": _name(f.get("hospMain"), "hcode"),
        "hospMainName": _name(f.get("hospMain"), "hname"),
        "purchaseProvince": _name(f.get("purchaseProvince")),
        "fundCount": len(result.get("funds") or []),
    }


def write_private(path: str | Path, text: str) -> None:
    """เขียนไฟล์ความลับ (token) ให้อ่านได้เฉพาะเจ้าของไฟล์ (macOS/Linux: chmod 600)"""
    p = Path(path)
    p.write_text(text, encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
