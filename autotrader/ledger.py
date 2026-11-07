"""Append-only, hash-chained decision ledger (JSON lines).

Each record carries the previous record's hash, so any edit, deletion or reordering breaks verification.
Rules enforced at write time:
* one decision per (event, decider): a second one is refused (no re-rolling a model until it says "buy");
* an outcome must reference an existing decision, and the first price it uses must come after that decision's
  as-of time; in forward mode also after the moment the decision was written (wall clock);
* a ledger holds one mode only ("replay" on already-examined history, or "forward"), so replayed results can
  never be mixed into forward evidence.
Committing the ledger to git adds an independent timestamp: a forward decision committed before the market
opened cannot have used that day's prices.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

GENESIS = "0" * 64
MODES = ("replay", "forward")


class LedgerError(RuntimeError):
    pass


class DuplicateDecision(LedgerError):
    pass


def _hash(prev: str, body: dict) -> str:
    return hashlib.sha256((prev + json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Ledger:
    def __init__(self, path: Path, mode: str):
        if mode not in MODES:
            raise LedgerError(f"unknown ledger mode {mode!r}")
        self.path = Path(path)
        self.mode = mode
        self.records: list[dict] = []
        self.decisions: dict[tuple[str, str], dict] = {}
        self.outcomes: set[int] = set()
        self.last = GENESIS
        if self.path.exists():
            ok, problems = self.verify()
            if not ok:
                raise LedgerError("ledger failed verification: " + "; ".join(problems[:3]))
            for r in self.records:
                self._index(r)
            first = next((r for r in self.records if r["type"] == "ledger_open"), None)
            if first and first["payload"]["mode"] != mode:
                raise LedgerError(f"this ledger is a {first['payload']['mode']} ledger, not {mode}")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.append("ledger_open", {"mode": mode})

    # ------------------------------------------------------------------ io
    def verify(self) -> tuple[bool, list[str]]:
        problems, prev, recs = [], GENESIS, []
        if not self.path.exists():
            return True, []
        for i, line in enumerate(self.path.read_text().splitlines()):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                problems.append(f"line {i + 1}: not JSON")
                continue
            body = {k: r[k] for k in ("seq", "type", "recorded_at", "payload")}
            if r.get("prev") != prev:
                problems.append(f"record {r.get('seq')}: chain broken (prev hash mismatch)")
            if _hash(r.get("prev", ""), body) != r.get("hash"):
                problems.append(f"record {r.get('seq')}: content altered (hash mismatch)")
            if r.get("seq") != i:
                problems.append(f"record at line {i + 1}: sequence {r.get('seq')} out of order")
            prev = r.get("hash", "")
            recs.append(r)
        self.records, self.last = recs, prev if recs else GENESIS
        return not problems, problems

    def append(self, type_: str, payload: dict, now: Optional[datetime] = None) -> dict:
        body = {"seq": len(self.records), "type": type_, "recorded_at": (now or utcnow()).isoformat(), "payload": payload}
        rec = body | {"prev": self.last, "hash": _hash(self.last, body)}
        line = json.dumps(rec, sort_keys=True, default=str)
        with self.path.open("a") as f:          # an append that fails raises: the caller must stop (safe state)
            f.write(line + "\n")
            f.flush()
        self.records.append(json.loads(line))
        self.last = rec["hash"]
        self._index(self.records[-1])
        return self.records[-1]

    def _index(self, r: dict) -> None:
        if r["type"] == "decision":
            p = r["payload"]
            self.decisions[(p["event_id"], p["decider"])] = r
        elif r["type"] == "outcome":
            self.outcomes.add(r["payload"]["decision_seq"])

    # ------------------------------------------------------------------ guarded writes
    def record_decision(self, event_id: str, decider: str, pack_sha256: str, decision_time: datetime,
                        decision: dict, now: Optional[datetime] = None) -> dict:
        key = (event_id, decider)
        if key in self.decisions:
            raise DuplicateDecision(f"{decider} already decided {event_id} (record {self.decisions[key]['seq']})")
        now = now or utcnow()
        if self.mode == "forward" and decision_time > now:
            raise LedgerError("a forward decision cannot be dated in the future")
        return self.append("decision", {"event_id": event_id, "decider": decider, "pack_sha256": pack_sha256,
                                        "decision_time": decision_time.isoformat(), "mode": self.mode,
                                        "decision": decision}, now)

    def record_outcome(self, decision_seq: int, outcome: dict, data_from: datetime,
                       now: Optional[datetime] = None) -> dict:
        dec = next((r for r in self.records if r["seq"] == decision_seq and r["type"] == "decision"), None)
        if dec is None:
            raise LedgerError(f"no decision with sequence {decision_seq}")
        if decision_seq in self.outcomes:
            raise LedgerError(f"decision {decision_seq} already has an outcome")
        as_of = datetime.fromisoformat(dec["payload"]["decision_time"])
        if data_from <= as_of:
            raise LedgerError("look-ahead: the outcome uses prices from before the decision")
        if self.mode == "forward" and data_from <= datetime.fromisoformat(dec["recorded_at"]):
            raise LedgerError("a forward outcome must use prices that did not exist when the decision was written")
        return self.append("outcome", {"decision_seq": decision_seq, "data_from": data_from.isoformat(), **outcome}, now)

    def of_type(self, type_: str) -> list[dict]:
        return [r for r in self.records if r["type"] == type_]
