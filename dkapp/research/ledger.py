"""Data-use ledger: which market days have already influenced development.

A period counts as an honest holdout only if no result computed on it has been looked at. The build 20
study published results for every day from 2026-09-11 to 2026-11-05 (train, validation AND its "unseen"
test), so from now on those days are EXAMINED. New research may use them for development and
walk-forward checks, but must never call them unseen. Genuinely new evidence comes from days after the
ledger's `forward_from` date (forward paper/shadow trading) or from history that is downloaded and
registered here before anyone looks at results on it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

LEDGER_PATH = Path(__file__).resolve().parents[2] / "research" / "ledger.json"

EXAMINED = "examined"      # results were computed and looked at: development data only
RESERVED = "reserved"      # registered as a holdout before any result was computed: may be used ONCE
FORWARD = "forward"        # after forward_from: observed live in paper/shadow, never backtested before


class ReservedData(PermissionError):
    """Refused: the requested days are a reserved holdout (or the holdout was already used)."""


@dataclass
class Period:
    start: date
    end: date
    status: str
    by: str
    note: str = ""

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


@dataclass
class DataLedger:
    periods: list[Period] = field(default_factory=list)
    forward_from: Optional[date] = None

    @classmethod
    def load(cls, path: Path = LEDGER_PATH) -> "DataLedger":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        ps = [Period(date.fromisoformat(p["start"]), date.fromisoformat(p["end"]), p["status"], p["by"],
                     p.get("note", "")) for p in raw.get("periods", [])]
        ff = raw.get("forwardFrom")
        return cls(ps, date.fromisoformat(ff) if ff else None)

    def status(self, d: date) -> str:
        """examined wins over reserved: once looked at, a reserved day is no longer a holdout."""
        st = [p.status for p in self.periods if p.contains(d)]
        if EXAMINED in st:
            return EXAMINED
        if RESERVED in st:
            return RESERVED
        if self.forward_from is not None and d >= self.forward_from:
            return FORWARD
        return "unregistered"

    def classify(self, days: Iterable[date]) -> dict[str, list[date]]:
        out: dict[str, list[date]] = {}
        for d in sorted(set(days)):
            out.setdefault(self.status(d), []).append(d)
        return out

    def label(self, days: Iterable[date]) -> str:
        """How a result on these days may be described."""
        c = self.classify(days)
        if not c:
            return "no data"
        if set(c) == {FORWARD}:
            return "forward (not seen before)"
        if set(c) == {RESERVED}:
            return "reserved holdout (first use)"
        if EXAMINED in c:
            return "development data (already examined — not an unseen test)"
        return "unregistered history (register it before relying on it)"

    def may_call_unseen(self, days: Iterable[date]) -> bool:
        c = self.classify(days)
        return bool(c) and set(c) <= {RESERVED, FORWARD}


def assert_not_called_unseen(ledger: DataLedger, days: Iterable[date], claim: str) -> None:
    """Raise if a report is about to call examined data 'unseen'."""
    if "unseen" in claim.lower() and not ledger.may_call_unseen(days):
        raise ValueError(f"refusing to label {ledger.label(days)} as unseen")
