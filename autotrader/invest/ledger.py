"""Keyed, append-only, hash-chained ledger for the investing pilot (JSON lines).

Same chain as autotrader/ledger.py: each record's hash covers its content and the previous record's hash, so an
edit, deletion or reordering breaks `verify()`. On top of that, every record has a unique key (for example
`fill|ai|2031-02-03`):
* writing a key that exists with the same type and payload is a no-op, so rerunning a month adds nothing and
  leaves the file byte for byte unchanged;
* writing it with different content is refused (`LedgerConflict`): a frozen decision is never replaced, and a fill
  computed from prices that later changed is never silently rewritten;
* a ledger holds one mode: `dry_run` (synthetic data, fixture decisions) or `forward` (genuine decisions only), so
  fixture decisions can never be mixed into forward evidence.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..ledger import GENESIS, _hash

MODES = ("dry_run", "forward")


class LedgerError(RuntimeError):
    pass


class LedgerConflict(LedgerError):
    """A key already holds different content."""


def normalize(payload) -> dict:
    """The payload exactly as it will be stored (Decimals and dates become strings)."""
    return json.loads(json.dumps(payload, sort_keys=True, default=str))


class InvestLedger:
    def __init__(self, path: Path, mode: str, identity: dict, opened_at: datetime):
        if mode not in MODES:
            raise LedgerError(f"unknown ledger mode {mode!r}")
        self.path, self.mode = Path(path), mode
        self.records: list[dict] = []
        self.index: dict[str, dict] = {}
        self.last = GENESIS
        self.appended = 0
        if self.path.exists():
            ok, problems = self.verify()
            if not ok:
                raise LedgerError("ledger failed verification: " + "; ".join(problems[:3]))
            head = self.index.get("open")
            if head is None:
                raise LedgerError("ledger has no open record")
            if head["payload"]["mode"] != mode:
                raise LedgerError(f"this is a {head['payload']['mode']} ledger, not {mode}")
            if head["payload"]["identity"] != normalize(identity):
                raise LedgerError("this ledger belongs to another experiment identity")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.put("open", "open", {"mode": mode, "identity": identity}, opened_at)

    def verify(self) -> tuple[bool, list[str]]:
        problems, prev, recs, index = [], GENESIS, [], {}
        for i, line in enumerate(self.path.read_text().splitlines()):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                problems.append(f"line {i + 1}: not JSON")
                continue
            body = {k: r.get(k) for k in ("seq", "key", "type", "recorded_at", "payload")}
            if r.get("prev") != prev:
                problems.append(f"record {r.get('seq')}: chain broken (prev hash mismatch)")
            if _hash(r.get("prev", ""), body) != r.get("hash"):
                problems.append(f"record {r.get('seq')}: content altered (hash mismatch)")
            if r.get("seq") != i:
                problems.append(f"record at line {i + 1}: sequence {r.get('seq')} out of order")
            if r.get("key") in index:
                problems.append(f"record {r.get('seq')}: duplicate key {r.get('key')}")
            prev = r.get("hash", "")
            recs.append(r)
            index[r.get("key")] = r
        self.records, self.index, self.last = recs, index, (prev if recs else GENESIS)
        return not problems, problems

    def put(self, key: str, type_: str, payload: dict, recorded_at: datetime) -> tuple[dict, bool]:
        """(record, created). Idempotent for identical content; refuses different content under the same key."""
        p = normalize(payload)
        old = self.index.get(key)
        if old is not None:
            if old["type"] == type_ and old["payload"] == p:
                return old, False
            raise LedgerConflict(f"{key}: already recorded with different content (record {old['seq']}); "
                                 "a ledger entry is never replaced")
        if recorded_at.tzinfo is None:
            raise LedgerError("recorded_at must be timezone-aware")
        body = {"seq": len(self.records), "key": key, "type": type_,
                "recorded_at": recorded_at.astimezone(timezone.utc).isoformat(), "payload": p}
        rec = body | {"prev": self.last, "hash": _hash(self.last, body)}
        with self.path.open("a") as f:          # a failed append raises: the run stops in a safe state
            f.write(json.dumps(rec, sort_keys=True) + "\n")
            f.flush()
        self.records.append(rec)
        self.index[key] = rec
        self.last = rec["hash"]
        self.appended += 1
        return rec, True

    def get(self, key: str) -> Optional[dict]:
        return self.index.get(key)

    def of_type(self, type_: str) -> list[dict]:
        return [r for r in self.records if r["type"] == type_]


def read_records(path: Path) -> list[dict]:
    """Records of a ledger file after verification (for offline reports)."""
    led = InvestLedger.__new__(InvestLedger)
    led.path = Path(path)
    ok, problems = led.verify()
    if not ok:
        raise LedgerError("ledger failed verification: " + "; ".join(problems[:3]))
    return led.records
