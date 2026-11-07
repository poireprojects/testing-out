"""SQLite persistence. One database file per trading mode (demo / paper / live).

Orders are written *before* they are sent to the broker (write-ahead), so a crash
between "decided to trade" and "broker answered" can be reconciled on restart
using the deterministic client_order_id.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS app_state(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  strategy TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  managed INTEGER NOT NULL DEFAULT 1,
  session_date TEXT NOT NULL,
  created_at TEXT NOT NULL,
  entry_qty REAL NOT NULL DEFAULT 0,
  entry_price REAL,
  entry_time TEXT,
  entry_notional REAL,
  stop_price REAL,
  initial_stop REAL,
  target_price REAL,
  highest_price REAL,
  max_hold_until TEXT,
  software_stop INTEGER NOT NULL DEFAULT 0,
  exit_qty REAL NOT NULL DEFAULT 0,
  exit_price REAL,
  exit_time TEXT,
  exit_reason TEXT,
  realized_pnl REAL,
  fees REAL NOT NULL DEFAULT 0,
  decision_mode TEXT,
  ai_decision_id INTEGER,
  signal_id INTEGER,
  reasoning TEXT,
  signal_json TEXT,
  last_review_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS ix_trades_session ON trades(session_date);

CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_id INTEGER,
  purpose TEXT NOT NULL,
  client_order_id TEXT NOT NULL UNIQUE,
  broker_order_id TEXT,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  type TEXT NOT NULL,
  qty REAL,
  notional REAL,
  limit_price REAL,
  stop_price REAL,
  status TEXT NOT NULL,
  filled_qty REAL NOT NULL DEFAULT 0,
  filled_avg_price REAL,
  created_at TEXT NOT NULL,
  submitted_at TEXT,
  updated_at TEXT,
  reject_reason TEXT,
  attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS ix_orders_trade ON orders(trade_id);

CREATE TABLE IF NOT EXISTS fills(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL,
  trade_id INTEGER,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  qty REAL NOT NULL,
  price REAL NOT NULL,
  time TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  time TEXT NOT NULL,
  symbol TEXT NOT NULL,
  strategy TEXT NOT NULL,
  score REAL,
  price REAL,
  stop_price REAL,
  target_price REAL,
  reasons TEXT,
  features TEXT,
  outcome TEXT,
  outcome_detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_signals_time ON signals(time);

CREATE TABLE IF NOT EXISTS ai_decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  time TEXT NOT NULL,
  kind TEXT NOT NULL,
  symbol TEXT,
  strategy TEXT,
  provider TEXT,
  model TEXT,
  action TEXT,
  confidence REAL,
  evidence_quality TEXT,
  reasoning TEXT,
  risks TEXT,
  valid INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  raw TEXT,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0,
  latency_ms INTEGER DEFAULT 0,
  signal_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_ai_time ON ai_decisions(time);

CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  time TEXT NOT NULL,
  level TEXT NOT NULL,
  category TEXT NOT NULL,
  title TEXT NOT NULL,
  message TEXT,
  symbol TEXT,
  ref_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_events_time ON events(time);

CREATE TABLE IF NOT EXISTS equity_snapshots(
  time TEXT PRIMARY KEY,
  session_date TEXT,
  equity REAL,
  cash REAL,
  settled_cash REAL,
  buying_power REAL,
  realized_today REAL,
  unrealized REAL,
  positions_value REAL
);

CREATE TABLE IF NOT EXISTS daily_summary(
  session_date TEXT PRIMARY KEY,
  start_equity REAL,
  end_equity REAL,
  realized REAL,
  fees REAL,
  trades INTEGER,
  wins INTEGER,
  losses INTEGER,
  ai_cost REAL,
  benchmark_close REAL
);

CREATE TABLE IF NOT EXISTS settlements(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_id INTEGER,
  amount REAL NOT NULL,
  trade_date TEXT NOT NULL,
  settle_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shadow_trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id INTEGER,
  symbol TEXT NOT NULL,
  strategy TEXT NOT NULL,
  verdict TEXT NOT NULL,
  entry_time TEXT NOT NULL,
  entry_price REAL NOT NULL,
  stop_price REAL NOT NULL,
  target_price REAL NOT NULL,
  max_hold_until TEXT NOT NULL,
  highest REAL,
  status TEXT NOT NULL,
  exit_time TEXT,
  exit_price REAL,
  exit_reason TEXT,
  return_pct REAL
);

CREATE TABLE IF NOT EXISTS decision_cycles(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  time TEXT NOT NULL,
  regime TEXT,
  summary TEXT,
  mode TEXT,
  data TEXT
);

CREATE TABLE IF NOT EXISTS backtest_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  time TEXT NOT NULL,
  source TEXT NOT NULL,
  params TEXT,
  results TEXT
);

CREATE TABLE IF NOT EXISTS strategy_evidence(
  strategy TEXT PRIMARY KEY,
  verdict TEXT NOT NULL,
  source TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  details TEXT
);

CREATE TABLE IF NOT EXISTS volume_baseline(
  symbol TEXT PRIMARY KEY,
  avg_daily_volume REAL,
  prev_close REAL,
  avg_range_pct REAL,
  as_of TEXT
);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def from_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Database:
    def __init__(self, path: Path | str, mode: str):
        self.path = str(path)
        self.mode = mode
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            row = self._conn.execute("SELECT value FROM meta WHERE key='mode'").fetchone()
            if row is None:
                self._conn.execute("INSERT INTO meta(key, value) VALUES('mode', ?)", (mode,))
                self._conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema', ?)", (str(SCHEMA_VERSION),))
            elif row["value"] != mode:
                raise RuntimeError(f"database {self.path} belongs to mode {row['value']!r}, not {mode!r}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------- primitives
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
        with self._lock:
            r = self._conn.execute(sql, tuple(params)).fetchone()
            return dict(r) if r else None

    def scalar(self, sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
        with self._lock:
            r = self._conn.execute(sql, tuple(params)).fetchone()
        return default if r is None or r[0] is None else r[0]

    def insert(self, table: str, **fields: Any) -> int:
        cols = ", ".join(fields)
        qs = ", ".join("?" for _ in fields)
        with self._lock:
            cur = self._conn.execute(f"INSERT INTO {table}({cols}) VALUES({qs})", tuple(fields.values()))
            return int(cur.lastrowid)

    def update(self, table: str, row_id: int, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*fields.values(), row_id))

    def transaction(self):
        return _Tx(self)

    # ------------------------------------------------------------- app state
    def get_state(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM app_state WHERE key=?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    def set_state(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO app_state(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), utcnow_iso()),
        )

    # ------------------------------------------------------------- events
    def log_event(self, level: str, category: str, title: str, message: str = "", symbol: str | None = None,
                  ref_id: int | None = None, time: Optional[datetime] = None) -> int:
        return self.insert("events", time=to_iso(time) or utcnow_iso(), level=level, category=category,
                           title=title, message=message, symbol=symbol, ref_id=ref_id)

    # ------------------------------------------------------------- trades
    def open_trades(self, managed_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM trades WHERE status IN ('pending_entry','open','closing')"
        if managed_only:
            sql += " AND managed=1"
        return self.query(sql + " ORDER BY id")

    def trade(self, trade_id: int) -> Optional[dict]:
        return self.query_one("SELECT * FROM trades WHERE id=?", (trade_id,))

    def orders_for_trade(self, trade_id: int) -> list[dict]:
        return self.query("SELECT * FROM orders WHERE trade_id=? ORDER BY id", (trade_id,))

    def active_orders(self) -> list[dict]:
        return self.query(
            "SELECT * FROM orders WHERE status NOT IN "
            "('filled','canceled','expired','rejected','replaced','abandoned','done_for_day','stopped','suspended') ORDER BY id"
        )

    def closed_trades(self, limit: int = 1000) -> list[dict]:
        return self.query("SELECT * FROM trades WHERE status='closed' ORDER BY exit_time DESC LIMIT ?", (limit,))


class _Tx:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db._lock.acquire()
        self.db._conn.execute("BEGIN")
        return self.db

    def __exit__(self, exc_type, exc, tb):
        try:
            self.db._conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db._lock.release()
        return False
