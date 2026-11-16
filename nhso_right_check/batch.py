"""ตรวจสอบสิทธิแบบกลุ่มจากหน้าจอโปรแกรม

API ของ สปสช. (SRM right-search) ตรวจได้ครั้งละ 1 คน — ไฟล์นี้จึงวนตรวจทีละคนในเบื้องหลัง
พร้อมตัวป้องกันโควตา: ตัดเลขซ้ำ, ตรวจ check digit ก่อน (เลขผิดไม่เรียก API), ใช้ผลเดิมจาก cache,
หน่วงระหว่าง request, หยุดเองเมื่อ สปสช. ตอบ 429 / token หมดอายุ / ผิดพลาดติดกันหลายครั้ง
"""
from __future__ import annotations

import io
import re
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from .srm import SRMClient, SRMError, summarize
from .validation import ValidationError, is_valid_pid, mask_pid

PID_IN_TEXT = re.compile(r"(?<!\d)\d{13}(?!\d)")
MAX_STRIKES = 5            # ผิดพลาดติดกันกี่ครั้งจึงหยุดพัก


# ---------------- อ่านรายชื่อ ----------------
def extract_pids(text: str) -> list[str]:
    """ดึงเลข 13 หลักทุกตัวจากข้อความ (รองรับ 1-1017-00203-45-0) ตามลำดับที่พบ"""
    text = re.sub(r"(?<=\d)-(?=\d)", "", text or "")
    return PID_IN_TEXT.findall(text)


def xlsx_text(data: bytes) -> str:
    """อ่านค่าทุกเซลล์ในไฟล์ .xlsx (ใช้ standard library ไม่ต้องติดตั้ง openpyxl)"""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValidationError("อ่านไฟล์ Excel ไม่ได้ — ต้องเป็น .xlsx (ถ้าเป็น .xls ให้ Save As เป็น .xlsx หรือ CSV)") from e
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(ns + "si"):
            shared.append("".join(t.text or "" for t in si.iter(ns + "t")))
    out: list[str] = []
    for name in sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")):
        for c in ET.fromstring(z.read(name)).iter(ns + "c"):
            t, v = c.get("t"), c.find(ns + "v")
            if t == "s" and v is not None and (v.text or "").isdigit() and int(v.text) < len(shared):
                out.append(shared[int(v.text)])
            elif t == "inlineStr":
                out.append("".join(x.text or "" for x in c.iter(ns + "t")))
            elif v is not None and v.text:
                val = v.text
                if re.fullmatch(r"\d{13}\.0+", val):
                    val = val.split(".")[0]
                out.append(val)
    return "\n".join(out)


def parse_input(text: str = "", data: bytes | None = None, filename: str = "") -> dict:
    """แยกรายชื่อเป็น: เลขที่จะตรวจ (ไม่ซ้ำ) / เลขซ้ำ / เลขผิด check digit"""
    if data is not None:
        if filename.lower().endswith(".xlsx"):
            text = (text or "") + "\n" + xlsx_text(data)
        else:
            for enc in ("utf-8-sig", "cp874", "latin-1"):
                try:
                    text = (text or "") + "\n" + data.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
    found = extract_pids(text)
    seen: set[str] = set()
    pids, invalid, dup = [], [], 0
    for p in found:
        if p in seen:
            dup += 1
            continue
        seen.add(p)
        (pids if is_valid_pid(p) else invalid).append(p)
    return {"pids": pids, "invalid": invalid, "found": len(found), "duplicates": dup}


# ---------------- งานตรวจในเบื้องหลัง ----------------
def _row(i: int, pid: str, status: str, s: dict | None = None, note: str = "", cached: bool = False) -> dict:
    s = s or {}
    return {"no": i, "pid": pid, "status": status, "note": note, "cached": cached,
            **{k: s.get(k, "") for k in (
                "name", "sex", "birthDate", "deathDate", "checkDate", "mainInscl", "rightName", "mainInsclName",
                "rightGroup", "rightGroupName", "subInscl", "subInsclName", "otherFunds", "hospMainOp",
                "hospMainOpName", "hospSub", "hospSubName", "hospMain", "hospMainName", "purchaseProvince",
                "startDateTime", "expireDateTime")}}


class BatchJob:
    """state: ready → running → (paused ↔ running) → done / stopped"""

    def __init__(self, srm: SRMClient, pids: list[str], invalid: list[str], delay: float):
        self.srm, self.delay = srm, delay
        self.pids = pids
        self.rows: list[dict] = []
        # เลขผิด check digit ไม่ต้องเรียก API — ใส่ท้ายไฟล์ผลลัพธ์ให้ตามแก้
        self.invalid_rows = [_row(0, p, "INVALID", note="เลขบัตรไม่ถูกต้อง (check digit) — ไม่ได้ตรวจ")
                             for p in invalid]
        self.idx = 0
        self.state = "ready"
        self.message = ""
        self.api_calls = 0
        self.cache_hits = 0
        self.started = time.time()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ----- control -----
    def start(self) -> None:
        if self.state == "running":
            return
        self._stop.clear()
        self.state, self.message = "running", ""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    # ----- worker -----
    def _pause(self, msg: str) -> None:
        self.state, self.message = "paused", msg

    def _run(self) -> None:
        strikes = 0
        try:
            if not self.srm.s.srm_access_token:
                self.srm.reload_token()
        except SRMError as e:
            return self._pause(str(e))
        while self.idx < len(self.pids):
            if self._stop.is_set():
                self.state, self.message = "stopped", "หยุดตามที่สั่ง — กด “ตรวจต่อ” เพื่อทำต่อจากคนถัดไป"
                return
            pid = self.pids[self.idx]
            no = self.idx + 1
            try:
                raw = self.srm.right_search(pid)
                cached = self.srm.last_cached_age is not None
                s = summarize(raw)
                st = "DEAD" if s.get("deathDate") else "OK"
                row = _row(no, pid, st, s, "ใช้ผลเดิม (ตรวจไปไม่เกิน 10 นาที)" if cached else "", cached)
                strikes = 0
            except SRMError as e:
                cached = False
                if e.status == 429:
                    return self._pause("สปสช. แจ้งว่าเรียกถี่/มากเกินไป (HTTP 429) — พักสักครู่แล้วกด “ตรวจต่อ” "
                                       "หรือใช้ SRM Portal → ตรวจสอบสิทธิแบบกลุ่ม")
                if e.status == 401:
                    return self._pause("token หมดอายุ — เสียบบัตร Login SRM Portal ใหม่ แล้วกด “ตรวจต่อ”")
                if e.status is None and "token" in str(e):
                    return self._pause(str(e).splitlines()[0])
                if e.status == 404:
                    row, strikes = _row(no, pid, "NOTFOUND", note="ไม่พบข้อมูลใน สปสช."), 0
                else:
                    strikes += 1
                    row = _row(no, pid, "ERROR", note=str(e).splitlines()[0])
                    if e.status is None or strikes >= MAX_STRIKES:
                        with self._lock:
                            self.api_calls += e.status is not None
                        return self._pause(f"{str(e).splitlines()[0]} — ตรวจการเชื่อมต่อแล้วกด “ตรวจต่อ” "
                                           f"(คนนี้จะถูกตรวจใหม่)")
            except ValidationError as e:  # กันไว้ (ปกติคัดเลขผิดออกตั้งแต่ตอนอ่านรายชื่อแล้ว)
                cached, row = True, _row(no, pid, "INVALID", note=str(e))
            with self._lock:
                self.rows.append(row)
                self.idx += 1
                if cached:
                    self.cache_hits += 1
                else:
                    self.api_calls += 1
            if not cached and self.idx < len(self.pids):
                self._stop.wait(self.delay)
        self.state, self.message = "done", ""

    # ----- view -----
    def snapshot(self, since: int = 0, masked: bool = True) -> dict:
        with self._lock:
            rows = [dict(r) for r in self.rows[since:]]
            n = len(self.rows)
        if masked:
            for r in rows:
                r["pid"] = mask_pid(r["pid"])
        counts: dict[str, int] = {"INVALID": len(self.invalid_rows)} if self.invalid_rows else {}
        for r in self.rows[:n]:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"state": self.state, "message": self.message, "done": self.idx, "total": len(self.pids),
                "apiCalls": self.api_calls, "cacheHits": self.cache_hits, "counts": counts,
                "rows": rows, "next": n, "elapsed": round(time.time() - self.started)}


    def all_rows(self) -> list[dict]:
        with self._lock:
            return [*self.rows, *self.invalid_rows]


# ---------------- ส่งออก Excel ----------------
STATUS_TH = {"OK": "พบสิทธิ", "DEAD": "เสียชีวิต", "NOTFOUND": "ไม่พบข้อมูล", "ERROR": "ผิดพลาด",
             "INVALID": "เลขบัตรไม่ถูกต้อง"}
COLUMNS = [
    ("ลำดับ", lambda r: r["no"] or "", 7), ("เลขบัตรประชาชน", lambda r: r["pid"], 17),
    ("ผลตรวจ", lambda r: STATUS_TH.get(r["status"], r["status"]), 13), ("ชื่อ-สกุล", lambda r: r["name"], 28),
    ("เพศ", lambda r: r["sex"], 7), ("วันเกิด", lambda r: r["birthDate"], 12),
    ("รหัสสิทธิหลัก", lambda r: r["mainInscl"], 11), ("ชื่อสิทธิ", lambda r: r["rightName"] or r["mainInsclName"], 36),
    ("กลุ่มสิทธิ", lambda r: " ".join(x for x in (r["rightGroup"], r["rightGroupName"]) if x), 24),
    ("สิทธิย่อย", lambda r: " ".join(x for x in (r["subInscl"], r["subInsclName"]) if x), 30),
    ("สิทธิอื่นที่มี", lambda r: r["otherFunds"], 14),
    ("หน่วยบริการประจำ", lambda r: r["hospMainOp"], 10), ("ชื่อหน่วยบริการประจำ", lambda r: r["hospMainOpName"], 30),
    ("หน่วยปฐมภูมิ", lambda r: r["hospSub"], 10), ("ชื่อหน่วยปฐมภูมิ", lambda r: r["hospSubName"], 30),
    ("หน่วยรับส่งต่อ", lambda r: r["hospMain"], 10), ("ชื่อหน่วยรับส่งต่อ", lambda r: r["hospMainName"], 30),
    ("จังหวัดที่ลงทะเบียน", lambda r: r["purchaseProvince"], 16),
    ("วันเริ่มสิทธิ", lambda r: r["startDateTime"], 18), ("วันหมดอายุสิทธิ", lambda r: r["expireDateTime"], 18),
    ("วันเสียชีวิต", lambda r: r["deathDate"], 12), ("วันเวลาที่ตรวจ", lambda r: r["checkDate"], 20),
    ("หมายเหตุ", lambda r: r["note"], 40),
]


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref: str, v, style: int = 0) -> str:
    st = f' s="{style}"' if style else ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f'<c r="{ref}"{st}><v>{v}</v></c>'
    v = str(v or "")
    if v[:1] in ("=", "+", "-", "@"):  # กัน formula injection
        v = "'" + v
    v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", v)
    return f'<c r="{ref}" t="inlineStr"{st}><is><t xml:space="preserve">{escape(v)}</t></is></c>'


def write_xlsx(path: str | Path, rows: list[dict], sheet: str = "ผลตรวจสิทธิ") -> Path:
    """เขียน .xlsx ด้วย standard library — เลขบัตรเป็นข้อความ (ไม่กลายเป็น 1.1E+12), หัวตารางตัวหนา, ตรึงแถวบน"""
    path = Path(path)
    head = "".join(_cell(f"{_col(i)}1", h, 1) for i, (h, _, _) in enumerate(COLUMNS, 1))
    body = [f'<row r="1">{head}</row>']
    for n, r in enumerate(rows, 2):
        body.append(f'<row r="{n}">' + "".join(_cell(f"{_col(i)}{n}", f(r)) for i, (_, f, _) in
                                               enumerate(COLUMNS, 1)) + "</row>")
    cols = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, (_, _, w) in enumerate(COLUMNS, 1))
    last = f"{_col(len(COLUMNS))}{max(1, len(rows) + 1)}"
    ws = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
          '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
          'state="frozen"/></sheetView></sheetViews>'
          f'<cols>{cols}</cols><sheetData>{"".join(body)}</sheetData><autoFilter ref="A1:{last}"/></worksheet>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="2"><font><sz val="11"/><name val="Tahoma"/></font>'
              '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Tahoma"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/>'
              '</fill><fill><patternFill patternType="solid"><fgColor rgb="FF0D4DA1"/></patternFill></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
              '</styleSheet>')
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(sheet)}" sheetId="1" r:id="rId1"/></sheets>'
        '<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'
        f"'{escape(sheet)}'!$A$1:${_col(len(COLUMNS))}${max(1, len(rows) + 1)}</definedName></definedNames></workbook>",
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>',
        "xl/styles.xml": styles,
        "xl/worksheets/sheet1.xml": ws,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return path


def export_folder() -> Path:
    """โฟลเดอร์เก็บไฟล์ผลตรวจ: Downloads ของผู้ใช้ (ถ้าไม่มีใช้ home)"""
    home = Path.home()
    d = home / "Downloads"
    return d if d.is_dir() else home


def export_name() -> str:
    return f"ผลตรวจสิทธิกลุ่ม_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
