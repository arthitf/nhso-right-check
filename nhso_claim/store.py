"""บันทึกประวัติการปิดสิทธิลง SQLite — จำเป็นสำหรับการยกเลิก (ต้องใช้ seq + transactionId)"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS claim_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT NOT NULL,
    env              TEXT NOT NULL,
    action           TEXT NOT NULL,          -- CLAIM | CANCEL
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
"""


class ClaimStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def log(self, *, env: str, action: str, request: dict, response: dict | None, success: bool) -> int:
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
        return self.conn.execute(
            "SELECT * FROM claim_log WHERE action='CLAIM' AND success=1 AND transaction_id=? ORDER BY id DESC LIMIT 1",
            (transaction_id,),
        ).fetchone()

    def recent(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM claim_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def close(self) -> None:
        self.conn.close()
