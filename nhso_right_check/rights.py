"""ตารางรหัสสิทธิ (vr_l_right) และกลุ่มสิทธิหลัก (vr_l_right_main)

ใช้แปลรหัสสิทธิจาก SRM เช่น UCS / WEL / OFC / SSS / BKK / E45 เป็นชื่อสิทธิ + กลุ่มสิทธิ
แก้/เพิ่มรหัสได้ที่ nhso_right_check/data/*.csv (UTF-8)
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data"

# รหัสที่ spec ปิดสิทธิ (แฟม 6) ระบุไว้ แต่ไม่มีในตาราง vr_l_right
EXTRA = {
    "OTH": "ไม่ใช้สิทธิ",
    "COR": "สิทธิคู่สัญญา",
    "NULL": "สิทธิว่าง",
}


@lru_cache(maxsize=1)
def _main() -> dict[str, dict]:
    with open(DATA / "vr_l_right_main.csv", encoding="utf-8") as f:
        return {r["main_id"]: r for r in csv.DictReader(f)}


@lru_cache(maxsize=1)
def _rights() -> dict[str, dict]:
    with open(DATA / "vr_l_right.csv", encoding="utf-8") as f:
        return {r["right_id"].upper(): r for r in csv.DictReader(f) if r["right_id"]}


def lookup(code: str | None) -> dict:
    """คืน {id, name, en, groupId, groupName, groupEn, known}"""
    code = (code or "").strip().upper()
    r = _rights().get(code)
    if r:
        g = _main().get(r["right_main"], {})
        return {"id": code, "name": r["right_name"], "en": r["right_desc"], "groupId": r["right_main"],
                "groupName": g.get("main_desc", ""), "groupEn": g.get("main_en_desc", ""), "known": True}
    if code in EXTRA:
        return {"id": code, "name": EXTRA[code], "en": "", "groupId": "", "groupName": "", "groupEn": "", "known": True}
    return {"id": code, "name": "", "en": "", "groupId": "", "groupName": "", "groupEn": "", "known": False}


def is_known(code: str | None) -> bool:
    return lookup(code)["known"]


def all_rights() -> list[dict]:
    """รายการสิทธิทั้งหมด (เรียงตาม right_order) สำหรับหน้าตารางสิทธิ"""
    out = [lookup(code) for code in _rights()]
    out += [lookup(code) for code in EXTRA]
    return out


def groups() -> list[dict]:
    return [{"id": k, "name": v["main_desc"], "en": v["main_en_desc"]} for k, v in _main().items()]
