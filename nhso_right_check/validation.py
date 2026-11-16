"""ตรวจความถูกต้องของข้อมูลก่อนส่ง API (ลด error จากฝั่ง สปสช.)"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

PID_RE = re.compile(r"^\d{13}$")
HCODE_RE = re.compile(r"^\d{5}$")


class ValidationError(ValueError):
    pass


def is_valid_pid(pid: str) -> bool:
    """ตรวจเลขบัตรประชาชน 13 หลัก รวม check digit (mod 11)"""
    if not pid or not PID_RE.match(pid):
        return False
    total = sum(int(d) * (13 - i) for i, d in enumerate(pid[:12]))
    return (11 - total % 11) % 10 == int(pid[12])


def require_pid(pid: str, label: str = "pid") -> str:
    pid = (pid or "").strip().replace("-", "").replace(" ", "")
    if not PID_RE.match(pid):
        raise ValidationError(f"{label} ต้องเป็นตัวเลข 13 หลัก")
    if not is_valid_pid(pid):
        raise ValidationError(f"{label} check digit ไม่ถูกต้อง")
    return pid


def require_hcode(hcode: str) -> str:
    hcode = (hcode or "").strip()
    if not HCODE_RE.match(hcode):
        raise ValidationError("hcode ต้องเป็นตัวเลข 5 หลัก")
    return hcode


def to_amount(value, label: str) -> Decimal:
    """จำนวนเงิน: หน้าทศนิยมไม่เกิน 8 หลัก ทศนิยมไม่เกิน 2 หลัก, ไม่ติดลบ"""
    try:
        d = Decimal(str(value).replace(",", "").strip() or "0")
    except InvalidOperation as e:
        raise ValidationError(f"{label} ไม่ใช่ตัวเลข: {value!r}") from e
    if d < 0:
        raise ValidationError(f"{label} ติดลบไม่ได้")
    sign, digits, exp = d.normalize().as_tuple()
    decimals = -exp if exp < 0 else 0
    integer_digits = len(digits) - decimals if len(digits) > decimals else 0
    if decimals > 2:
        raise ValidationError(f"{label} ทศนิยมเกิน 2 หลัก")
    if integer_digits > 8:
        raise ValidationError(f"{label} หน้าทศนิยมเกิน 8 หลัก")
    return d.quantize(Decimal("0.01"))


def mask_pid(pid: str) -> str:
    """ปิดเลขบัตรบางส่วนเวลาแสดงผล/เขียน log"""
    if not pid or len(pid) < 13:
        return pid
    return f"{pid[:1]}-xxxx-xxxxx-{pid[10:12]}-{pid[12]}"
