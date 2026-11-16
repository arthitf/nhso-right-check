"""บันทึกประวัติการปิดสิทธิลง SQLite — จำเป็นสำหรับการยกเลิก (ต้องใช้ seq + transactionId)"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS claim_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT NOT NULL,
    env              TEXT NOT NULL,
    action           TEXT NOT NULL,          -- CLAIM | CANCEL | AGENT_CLAIM (ปิดสิทธิผ่านเสียบบัตร)
    transaction_id   TEXT NOT NULL,
    pid              TEXT,
    hcode            TEXT,
    claim_service    TEXT,
    main_inscl       TEXT,
    privilege_amount REAL,
    paid_amount      REAL,
    seq              TEXT,
    authen_code      TEXT,
    success          INTEGER NOT NULL,
    data_error       TEXT,
    request_json     TEXT,
    response_json    TEXT
);
CREATE INDEX IF NOT EXISTS ix_claim_trans ON claim_log(transaction_id);
CREATE INDEX IF NOT EXISTS ix_claim_pid ON claim_log(pid);
CREATE TABLE IF NOT EXISTS api_usage (
    day   TEXT NOT NULL,
    kind  TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, kind)
);
"""


class ClaimStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        # หน้าจอโปรแกรมเรียกจากหลาย thread (เซิร์ฟเวอร์หน้าจอ + งานตรวจแบบกลุ่ม) → ใช้ connection เดียว + lock
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)

    def log(self, *, env: str, action: str, request: dict, response: dict | None, success: bool) -> int:
        with self.lock:
            response = response or {}
            cur = self.conn.execute(
                """INSERT INTO claim_log (created_at, env, action, transaction_id, pid, hcode, claim_service,
                   main_inscl, privilege_amount, paid_amount, seq, authen_code, success, data_error,
                   request_json, response_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    datetime.now().isoformat(timespec="seconds"), env, action,
                    str(request.get("transactionId", "")), request.get("pid"), request.get("hcode"),
                    request.get("claimServiceCode"), request.get("mainInsclCode"),
                    request.get("privilegeAmount"), request.get("paidAmount"),
                    str(response.get("seq", "") or ""), response.get("authenCode"), int(success),
                    response.get("dataError"), json.dumps(request, ensure_ascii=False),
                    json.dumps(response, ensure_ascii=False),
                ),
            )
            self.conn.commit()
            return cur.lastrowid

    def find_claim(self, transaction_id: str) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM claim_log WHERE action='CLAIM' AND success=1 AND transaction_id=? ORDER BY id DESC LIMIT 1",
                (transaction_id,),
            ).fetchone()

    def count_call(self, kind: str) -> None:
        """นับจำนวนการเรียก API จริงต่อวัน (ไว้ดูการใช้โควตา)"""
        with self.lock:
            day = datetime.now().strftime("%Y-%m-%d")
            self.conn.execute("INSERT INTO api_usage(day, kind, count) VALUES (?,?,1) "
                              "ON CONFLICT(day, kind) DO UPDATE SET count = count + 1", (day, kind))
            self.conn.commit()

    def usage_today(self) -> dict:
        with self.lock:
            day = datetime.now().strftime("%Y-%m-%d")
            return {r["kind"]: r["count"] for r in
                    self.conn.execute("SELECT kind, count FROM api_usage WHERE day=?", (day,))}

    def find_duplicate(self, pid: str, service_code: str, service_ms: int) -> sqlite3.Row | None:
        """หาการปิดสิทธิสำเร็จที่ซ้ำ: คนเดิม + รหัสบริการเดิม + วันที่รับบริการเดียวกัน (ยังไม่ถูกยกเลิก)"""
        with self.lock:
            day = datetime.fromtimestamp(service_ms / 1000).strftime("%Y-%m-%d")
            for r in self.conn.execute(
                "SELECT * FROM claim_log WHERE action IN ('CLAIM','AGENT_CLAIM') AND success=1 AND pid=? "
                "AND claim_service=? "
                "ORDER BY id DESC", (pid, service_code)
            ):
                req = json.loads(r["request_json"] or "{}")
                ms = req.get("serviceDateTime")
                if ms and datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d") == day:
                    cancelled = self.conn.execute(
                        "SELECT 1 FROM claim_log WHERE action='CANCEL' AND success=1 AND transaction_id=?",
                        (r["transaction_id"],)).fetchone()
                    if not cancelled:
                        return r
            return None

    def recent(self, limit: int = 20) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute("SELECT * FROM claim_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def close(self) -> None:
        with self.lock:
            self.conn.close()
