"""SQLite 存储层：一张 jobs 表足够撑起 W1。"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "app.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    arxiv_id   TEXT NOT NULL,
    title      TEXT,
    state      TEXT NOT NULL DEFAULT 'queued',   -- queued/running/done/failed
    stage      TEXT,
    progress   REAL NOT NULL DEFAULT 0,
    workdir    TEXT,
    pdf_path   TEXT,
    error      TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
"""

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(_SCHEMA)
            _conn.commit()
        return _conn


def execute(sql: str, args: tuple[Any, ...] = ()) -> None:
    c = conn()
    with _lock:
        c.execute(sql, args)
        c.commit()


def query(sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    c = conn()
    with _lock:
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def one(sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = query(sql, args)
    return rows[0] if rows else None
