"""SQLite persistence layer (WAL). Single-writer, thread-safe enough for our loop."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    user TEXT NOT NULL,
    agent TEXT NOT NULL,
    strategy_version TEXT,
    model TEXT,
    action TEXT NOT NULL,
    result TEXT,
    payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_open TEXT NOT NULL,
    ts_close TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    lot REAL NOT NULL,
    entry_price REAL,
    exit_price REAL,
    sl REAL,
    tp REAL,
    pnl REAL,
    pnl_percent REAL,
    exit_reason TEXT,
    strategy_version TEXT,
    mode TEXT NOT NULL DEFAULT 'DEMO',
    ticket TEXT,
    notes TEXT,
    features_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts_open);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision TEXT NOT NULL,
    signal TEXT,
    strategy_version TEXT,
    risk_result TEXT,
    reason TEXT,
    detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol, ts);

CREATE TABLE IF NOT EXISTS risk_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT,
    reason TEXT NOT NULL,
    checks_failed_json TEXT
);

CREATE TABLE IF NOT EXISTS strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT UNIQUE NOT NULL,
    base_version TEXT,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    rules_json TEXT NOT NULL,
    params_json TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    origin TEXT
);

CREATE TABLE IF NOT EXISTS backtests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    status TEXT NOT NULL,
    metrics_json TEXT,
    baseline_version TEXT,
    detail_json TEXT
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    role TEXT NOT NULL,
    user TEXT,
    text TEXT NOT NULL,
    intent TEXT,
    meta_json TEXT
);

CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    created_by TEXT,
    base_version TEXT NOT NULL,
    proposed_version TEXT NOT NULL,
    rules_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PROPOSAL',
    rationale TEXT
);

CREATE TABLE IF NOT EXISTS strategy_performance (
    version TEXT PRIMARY KEY,
    trades INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    pnl REAL DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS key_value (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    @contextmanager
    def tx(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def execute(self, sql: str, args: tuple = ()) -> None:
        with self.tx() as conn:
            conn.execute(sql, args)

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        with self.tx() as conn:
            conn.executemany(sql, rows)

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, args)
            return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, args: tuple = ()) -> dict | None:
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def kv_get(self, key: str, default=None):
        row = self.query_one("SELECT value_json FROM key_value WHERE key=?", (key,))
        if not row:
            return default
        return json.loads(row["value_json"])

    def kv_set(self, key: str, value) -> None:
        self.execute(
            "INSERT INTO key_value(key, value_json) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def close(self) -> None:
        self._conn.close()
