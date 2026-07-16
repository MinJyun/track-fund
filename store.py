"""快照儲存：原始檔落 data/raw/，正規化資料進 data/holdings.db (SQLite)。"""
import sqlite3
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent / "data"
DB_PATH = BASE / "holdings.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS fund_day (
    etf          TEXT NOT NULL,
    data_date    TEXT NOT NULL,   -- YYYY-MM-DD，投信揭露的資料日期
    nav          REAL,
    units        REAL,            -- 已發行受益權單位總數
    total_assets REAL,
    fetched_at   TEXT,
    PRIMARY KEY (etf, data_date)
);
CREATE TABLE IF NOT EXISTS holding (
    etf       TEXT NOT NULL,
    data_date TEXT NOT NULL,
    code      TEXT NOT NULL,
    name      TEXT,
    shares    REAL,
    amount    REAL,
    weight    REAL,
    PRIMARY KEY (etf, data_date, code)
);
"""


def connect() -> sqlite3.Connection:
    BASE.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def save_snapshot(conn: sqlite3.Connection, snap: dict):
    """寫入一天的快照（重抓同一天會整批覆蓋），並保存原始回應。"""
    etf, dd = snap["etf"], snap["data_date"]
    raw_dir = BASE / "raw" / dd
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{etf}.{snap['raw_ext']}").write_bytes(snap["raw"])

    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO fund_day VALUES (?,?,?,?,?,?)",
            (etf, dd, snap["nav"], snap["units"], snap["total_assets"],
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.execute("DELETE FROM holding WHERE etf=? AND data_date=?",
                     (etf, dd))
        conn.executemany(
            "INSERT INTO holding VALUES (?,?,?,?,?,?,?)",
            [(etf, dd, h["code"], h["name"], h["shares"], h["amount"],
              h["weight"]) for h in snap["holdings"]],
        )
