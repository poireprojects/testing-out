"""The budget journal: one transactional record of every paid generation in one experiment.

* **Bound to one experiment identity** (autotrader/experiment.py), not to an output directory. Its path is derived
  from the identity under a fixed state directory, so a new output directory finds the same journal and is refused
  instead of starting a fresh budget. The output directory it was opened with is recorded; another one is refused.
* **SQLite, one writer.** Every state change is its own committed transaction (synchronous=FULL), and an exclusive
  OS lock on a side file is held for as long as the journal is open: a second runner is refused at once.
* **Integer money.** All amounts are micro-dollars (1e-6 USD) in Python integers: $4/MTok input is exactly 4 per
  token, $20/MTok output exactly 20. Every value is checked to be a non-negative integer; ceilings are checked on
  every reservation.
* **One generation per event.** `event_id` is the primary key. States:
    reserved    the maximum possible charge is stored; nothing has been sent (safe to dispatch on resume)
    dispatched  written immediately before the request leaves the process; the outcome is unknown until settled
    settled     the response was complete and its usage valid: the exact charge from the price sheet
    held        anything else (timeout, crash, refusal, truncation, missing or unexpected usage, wrong model,
                transport or API error): the maximum charge is kept, and the event is never sent again
  On open, a `dispatched` row (the process died with a request in flight) becomes `held`.
* **Preauthorized.** Before the first send the whole run's per-event maxima are stored and must fit the ceiling;
  a reservation must equal its preauthorized maximum. The run fails before any send if it cannot fit.
* **Damage is fatal.** SQLite's integrity check and a per-row digest (over the row and the experiment id) are
  verified on open; any mismatch, unknown state or inconsistent value stops the run.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = "1"
STATES = ("reserved", "dispatched", "settled", "held")
DEFAULT_STATE_ROOT = Path.home() / ".local" / "state" / "stockpilot-research" / "phase5-dev-eval"
ROW_FIELDS = ("event_id", "seq", "state", "bound_micro", "charge_micro", "request_sha256", "request_bytes",
              "reserved_at", "dispatched_at", "finished_at", "latency_ms", "provider_request_id", "served_model",
              "stop_reason", "usage_json", "answer_text", "answer_sha256", "outcome", "detail")


class JournalError(RuntimeError):
    """The journal cannot be used safely: stop, send nothing."""


class JournalLocked(JournalError):
    pass


class DamagedJournal(JournalError):
    pass


class BudgetRefused(JournalError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _int(name: str, v, lo: int = 0, hi: Optional[int] = None) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise BudgetRefused(f"{name} must be an integer number of micro-dollars, not {v!r}")
    if v < lo or (hi is not None and v > hi):
        raise BudgetRefused(f"{name}={v} is outside [{lo}, {hi}]")
    return v


def journal_path(state_root: Path, experiment_id: str) -> Path:
    return Path(state_root) / f"{experiment_id}.sqlite3"


@dataclass
class Attempt:
    event_id: str
    seq: int
    state: str
    bound_micro: int
    charge_micro: Optional[int]
    request_sha256: str
    request_bytes: int
    reserved_at: Optional[str] = None
    dispatched_at: Optional[str] = None
    finished_at: Optional[str] = None
    latency_ms: Optional[int] = None
    provider_request_id: Optional[str] = None
    served_model: Optional[str] = None
    stop_reason: Optional[str] = None
    usage_json: Optional[str] = None
    answer_text: Optional[str] = None
    answer_sha256: Optional[str] = None
    outcome: Optional[str] = None
    detail: Optional[str] = None

    @property
    def committed_micro(self) -> int:
        """What counts against the ceiling: the exact charge once settled, otherwise the maximum."""
        return self.charge_micro if self.state == "settled" else self.bound_micro


class Journal:
    def __init__(self, state_root: Path, identity: dict, experiment_id: str, out_dir: Path, *, ceiling_micro: int,
                 per_call_max_micro: int, events: list[str], create: bool):
        self.experiment_id = experiment_id
        self.path = journal_path(state_root, experiment_id)
        self.out_dir = str(Path(out_dir).resolve())
        self.ceiling = _int("ceiling", ceiling_micro, 1)
        self.per_call_max = _int("per_call_max", per_call_max_micro, 1, self.ceiling)
        self.events = list(events)
        if len(set(self.events)) != len(self.events):
            raise BudgetRefused("duplicate event ids")
        Path(state_root).mkdir(parents=True, exist_ok=True)
        exists = self.path.exists()
        if exists and create:
            raise JournalError(f"a journal already exists for this experiment ({self.path}); resume it, never reset it")
        if not exists and not create:
            raise JournalError(f"no journal for this experiment at {self.path}: refusing to resume (a missing journal "
                               "would reset the budget)")
        self._lock_f = open(str(self.path) + ".lock", "a+")
        try:
            fcntl.flock(self._lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_f.close()
            raise JournalLocked("another runner holds this experiment's journal") from exc
        try:
            self.db = sqlite3.connect(self.path, isolation_level=None)
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA journal_mode=DELETE")
            if create:
                self._create(identity)
            self._check(identity)
            self._recover()
        except BaseException:
            self.close()
            raise

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, state_root, identity, experiment_id, out_dir, **kw) -> "Journal":
        return cls(state_root, identity, experiment_id, out_dir, create=True, **kw)

    @classmethod
    def resume(cls, state_root, identity, experiment_id, out_dir, **kw) -> "Journal":
        return cls(state_root, identity, experiment_id, out_dir, create=False, **kw)

    def close(self) -> None:
        db = getattr(self, "db", None)
        if db is not None:
            db.close()
            self.db = None
        f = getattr(self, "_lock_f", None)
        if f is not None and not f.closed:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _tx(self):
        return _Tx(self.db)

    def _create(self, identity: dict) -> None:
        with self._tx() as c:
            c.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
            c.execute(f"""CREATE TABLE attempts (
                event_id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK (state IN {STATES}),
                bound_micro INTEGER NOT NULL CHECK (bound_micro >= 0),
                charge_micro INTEGER CHECK (charge_micro IS NULL OR charge_micro >= 0),
                request_sha256 TEXT NOT NULL, request_bytes INTEGER NOT NULL CHECK (request_bytes > 0),
                reserved_at TEXT, dispatched_at TEXT, finished_at TEXT, latency_ms INTEGER,
                provider_request_id TEXT, served_model TEXT, stop_reason TEXT, usage_json TEXT,
                answer_text TEXT, answer_sha256 TEXT, outcome TEXT, detail TEXT, row_digest TEXT NOT NULL)""")
            meta = {"schema_version": SCHEMA_VERSION, "experiment_id": self.experiment_id,
                    "identity": json.dumps(identity, sort_keys=True), "out_dir": self.out_dir,
                    "ceiling_micro": str(self.ceiling), "per_call_max_micro": str(self.per_call_max),
                    "events": json.dumps(self.events), "created_at": _now()}
            c.executemany("INSERT INTO meta VALUES (?, ?)", sorted(meta.items()))

    def meta(self) -> dict:
        return dict(self.db.execute("SELECT k, v FROM meta").fetchall())

    def _check(self, identity: dict) -> None:
        ok = self.db.execute("PRAGMA integrity_check").fetchone()
        if not ok or ok[0] != "ok":
            raise DamagedJournal(f"SQLite integrity check failed: {ok}")
        try:
            m = self.meta()
        except sqlite3.DatabaseError as exc:
            raise DamagedJournal(f"journal unreadable: {exc}") from exc
        expect = {"schema_version": SCHEMA_VERSION, "experiment_id": self.experiment_id,
                  "identity": json.dumps(identity, sort_keys=True), "ceiling_micro": str(self.ceiling),
                  "per_call_max_micro": str(self.per_call_max), "events": json.dumps(self.events)}
        for k, v in expect.items():
            if m.get(k) != v:
                raise JournalError(f"journal {k} does not match this experiment: refusing to resume")
        if m.get("out_dir") != self.out_dir:
            raise JournalError(f"this experiment's journal belongs to output directory {m.get('out_dir')}, not "
                               f"{self.out_dir}: a new directory would reset the experiment")
        for a in self.attempts(verify=True):
            if a.event_id not in self.events:
                raise DamagedJournal(f"journal holds an unknown event {a.event_id}")
        self._check_totals()

    def _check_totals(self) -> None:
        rows = self.attempts(verify=False)
        plan = self.plan()
        for a in rows:
            _int("bound", a.bound_micro, 0, self.per_call_max)
            if a.state == "settled":
                _int("charge", a.charge_micro, 0, a.bound_micro)
            elif a.state == "held" and a.charge_micro != a.bound_micro:
                raise DamagedJournal(f"{a.event_id}: a hold must keep the maximum charge")
            if plan and plan.get(a.event_id) != a.bound_micro:
                raise DamagedJournal(f"{a.event_id}: reserved maximum differs from its preauthorized maximum")
        if self.committed_micro() > self.ceiling:
            raise DamagedJournal("committed charges exceed the ceiling")

    def _recover(self) -> None:
        """A request that was in flight when the process died has an unknown outcome: hold it, never resend."""
        for a in self.attempts(verify=False):
            if a.state == "dispatched":
                self._write(a.event_id, state="held", charge_micro=a.bound_micro, finished_at=_now(),
                            outcome="unknown_after_restart",
                            detail="the process stopped after dispatch and before settlement")

    # ------------------------------------------------------------------ rows
    def _digest(self, row: dict) -> str:
        body = {k: row.get(k) for k in ROW_FIELDS}
        return hashlib.sha256((self.experiment_id + json.dumps(body, sort_keys=True)).encode()).hexdigest()

    def attempts(self, verify: bool = True) -> list[Attempt]:
        cur = self.db.execute(f"SELECT {', '.join(ROW_FIELDS)}, row_digest FROM attempts ORDER BY seq")
        out = []
        for r in cur.fetchall():
            row = dict(zip(ROW_FIELDS, r[:-1]))
            if verify and self._digest(row) != r[-1]:
                raise DamagedJournal(f"journal row {row['event_id']} was altered or damaged")
            if row["state"] not in STATES:
                raise DamagedJournal(f"unknown state {row['state']!r}")
            out.append(Attempt(**row))
        return out

    def get(self, event_id: str) -> Optional[Attempt]:
        return next((a for a in self.attempts() if a.event_id == event_id), None)

    def committed_micro(self) -> int:
        return sum(a.committed_micro for a in self.attempts(verify=False))

    def settled_micro(self) -> int:
        return sum(a.charge_micro for a in self.attempts(verify=False) if a.state == "settled")

    def held_micro(self) -> int:
        return sum(a.bound_micro for a in self.attempts(verify=False) if a.state in ("held", "dispatched", "reserved"))

    def _write(self, event_id: str, **fields) -> Attempt:
        with self._tx() as c:
            r = c.execute(f"SELECT {', '.join(ROW_FIELDS)} FROM attempts WHERE event_id = ?", (event_id,)).fetchone()
            if r is None:
                raise JournalError(f"no reservation for {event_id}")
            row = dict(zip(ROW_FIELDS, r)) | fields
            sets = ", ".join(f"{k} = ?" for k in fields)
            c.execute(f"UPDATE attempts SET {sets}, row_digest = ? WHERE event_id = ?",
                      (*fields.values(), self._digest(row), event_id))
        return Attempt(**row)

    # ------------------------------------------------------------------ budget
    def plan(self) -> dict[str, int]:
        v = self.meta().get("plan_bounds")
        return json.loads(v) if v else {}

    def preauthorize(self, bounds: dict[str, int]) -> int:
        """Store every event's maximum charge before any send. Refused unless the whole fixed run fits."""
        if set(bounds) != set(self.events):
            raise BudgetRefused("a maximum must be given for exactly the experiment's events")
        for k, v in bounds.items():
            _int(f"maximum for {k}", v, 1, self.per_call_max)
        total = sum(bounds.values())
        if total > self.ceiling:
            raise BudgetRefused(f"the whole run could cost up to {total} micro-USD, above the ceiling {self.ceiling}: "
                                "nothing is sent")
        old = self.plan()
        if old and old != bounds:
            raise BudgetRefused("the preauthorized maxima cannot be changed")
        if not old:
            with self._tx() as c:
                c.execute("INSERT INTO meta VALUES ('plan_bounds', ?)", (json.dumps(bounds, sort_keys=True),))
        return total

    def reserve(self, event_id: str, request_sha256: str, request_bytes: int, bound_micro: int) -> Attempt:
        if event_id not in self.events:
            raise BudgetRefused(f"{event_id} is not an event of this experiment")
        plan = self.plan()
        if not plan:
            raise BudgetRefused("the run has not been preauthorized: nothing is sent")
        _int("bound", bound_micro, 1, self.per_call_max)
        _int("request_bytes", request_bytes, 1)
        if plan[event_id] != bound_micro:
            raise BudgetRefused(f"{event_id}: maximum {bound_micro} differs from its preauthorized {plan[event_id]}")
        with self._tx() as c:
            if c.execute("SELECT 1 FROM attempts WHERE event_id = ?", (event_id,)).fetchone():
                raise BudgetRefused(f"{event_id} already has its one attempt")
            rows = c.execute("SELECT state, bound_micro, charge_micro FROM attempts").fetchall()
            committed = sum(ch if st == "settled" else b for st, b, ch in rows)
            if committed + bound_micro > self.ceiling:
                raise BudgetRefused(f"reserving {bound_micro} would pass the ceiling (committed {committed})")
            if len(rows) >= len(self.events):
                raise BudgetRefused("every event already has its attempt")
            row = {"event_id": event_id, "seq": len(rows), "state": "reserved", "bound_micro": bound_micro,
                   "charge_micro": None, "request_sha256": request_sha256, "request_bytes": request_bytes,
                   "reserved_at": _now()}
            full = {k: row.get(k) for k in ROW_FIELDS}
            c.execute(f"INSERT INTO attempts ({', '.join(ROW_FIELDS)}, row_digest) VALUES "
                      f"({', '.join('?' * len(ROW_FIELDS))}, ?)", (*full.values(), self._digest(full)))
        return Attempt(**{k: full[k] for k in ROW_FIELDS})

    def mark_dispatched(self, event_id: str, request_sha256: str) -> Attempt:
        a = self.get(event_id)
        if a is None or a.state != "reserved":
            raise JournalError(f"{event_id} cannot be dispatched from state {a.state if a else 'none'}")
        if a.request_sha256 != request_sha256:
            raise JournalError(f"{event_id}: the request differs from the one reserved")
        return self._write(event_id, state="dispatched", dispatched_at=_now())

    def settle(self, event_id: str, charge_micro: int, *, latency_ms: int, provider_request_id, served_model,
               stop_reason, usage: dict, answer_text: str) -> Attempt:
        a = self.get(event_id)
        if a is None or a.state != "dispatched":
            raise JournalError(f"{event_id} cannot be settled from state {a.state if a else 'none'}")
        _int("charge", charge_micro, 0, a.bound_micro)
        return self._write(event_id, state="settled", charge_micro=charge_micro, finished_at=_now(),
                           latency_ms=int(latency_ms), provider_request_id=provider_request_id,
                           served_model=served_model, stop_reason=stop_reason,
                           usage_json=json.dumps(usage, sort_keys=True), answer_text=answer_text,
                           answer_sha256=hashlib.sha256(answer_text.encode()).hexdigest(), outcome="ok")

    def hold(self, event_id: str, outcome: str, detail: str = "", *, latency_ms: Optional[int] = None,
             provider_request_id=None, served_model=None, stop_reason=None, usage: Optional[dict] = None,
             answer_text: Optional[str] = None) -> Attempt:
        a = self.get(event_id)
        if a is None or a.state not in ("reserved", "dispatched"):
            raise JournalError(f"{event_id} cannot be held from state {a.state if a else 'none'}")
        return self._write(event_id, state="held", charge_micro=a.bound_micro, finished_at=_now(),
                           latency_ms=None if latency_ms is None else int(latency_ms),
                           provider_request_id=provider_request_id, served_model=served_model,
                           stop_reason=stop_reason, usage_json=None if usage is None else json.dumps(usage, sort_keys=True),
                           answer_text=answer_text,
                           answer_sha256=None if answer_text is None else hashlib.sha256(answer_text.encode()).hexdigest(),
                           outcome=outcome, detail=detail[:500])

    def summary(self) -> dict:
        rows = self.attempts()
        by = {s: sum(a.state == s for a in rows) for s in STATES}
        return {"experiment_id": self.experiment_id, "journal": str(self.path), "ceiling_micro": self.ceiling,
                "preauthorized_micro": sum(self.plan().values()), "attempts": len(rows), "states": by,
                "settled_micro": self.settled_micro(), "held_micro": self.held_micro(),
                "committed_micro": self.committed_micro(),
                "outcomes": {o: sum(a.outcome == o for a in rows) for o in sorted({a.outcome for a in rows if a.outcome})}}


class _Tx:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN EXCLUSIVE")
        return self.db

    def __exit__(self, et, ev, tb):
        self.db.execute("COMMIT" if et is None else "ROLLBACK")
        return False


def open_journal(state_root: Path, identity: dict, experiment_id: str, out_dir: Path, **kw) -> Journal:
    """Resume this experiment's journal if it exists, otherwise create it. An output directory that already
    records another experiment, or that has results but no journal, is refused."""
    out = Path(out_dir)
    marker = out / "experiment.json"
    exists = journal_path(state_root, experiment_id).exists()
    if marker.exists():
        rec = json.loads(marker.read_text())
        if rec.get("experiment_id") != experiment_id:
            raise JournalError("this output directory belongs to another experiment identity")
        if not exists:
            raise JournalError("the output directory has results but the experiment's journal is missing: refusing")
    elif (out / "ledger.jsonl").exists():
        raise JournalError("the output directory already holds decisions but no experiment marker: refusing")
    j = Journal.resume(state_root, identity, experiment_id, out, **kw) if exists else \
        Journal.create(state_root, identity, experiment_id, out, **kw)
    out.mkdir(parents=True, exist_ok=True)
    if not marker.exists():
        tmp = marker.with_suffix(".tmp")
        tmp.write_text(json.dumps({"experiment_id": experiment_id, "journal": str(j.path)}, indent=1))
        os.replace(tmp, marker)
    return j
