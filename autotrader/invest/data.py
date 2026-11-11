"""Read-only access to a pilot data directory. The format is the same for synthetic and real data:

    MANIFEST.json   {"synthetic": bool, "sources": {...}, "files": {name: sha256}}
    prices.json     {SYMBOL: {"YYYY-MM-DD": {"open": "123.45", "close": "124.10"}}}   as traded, unadjusted
    actions.json    {"splits": [{symbol, ex_date, num, den}], "dividends": [{symbol, ex_date, pay_date, amount}]}
    universe.json   {"rule": str, "source": str, "members": [{rank, symbol, name, cik}]}
    filings.json    [{symbol, cik, accession, form, accepted_at (ISO, tz), period_end, facts: {...}, passages: [{section, text}]}]

A price that is absent is missing data: callers record a gap, never a substitute.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

FILES = ("prices.json", "actions.json", "universe.json", "filings.json")


class DataError(RuntimeError):
    pass


def file_sha256(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class MarketData:
    def __init__(self, d: Path):
        self.d = Path(d)
        self.manifest = json.loads((self.d / "MANIFEST.json").read_text())
        for name in FILES:
            want = self.manifest["files"].get(name)
            if want is None or file_sha256(self.d / name) != want:
                raise DataError(f"{name}: missing from the manifest or its SHA-256 does not match")
        self.synthetic = bool(self.manifest.get("synthetic"))
        self.prices = json.loads((self.d / "prices.json").read_text())
        acts = json.loads((self.d / "actions.json").read_text())
        self.splits, self.dividends = acts.get("splits", []), acts.get("dividends", [])
        self.universe = json.loads((self.d / "universe.json").read_text())
        self.filings = json.loads((self.d / "filings.json").read_text())
        for f in self.filings:
            if datetime.fromisoformat(f["accepted_at"]).tzinfo is None:
                raise DataError(f"{f['accession']}: acceptance time without a time zone")

    @property
    def manifest_sha256(self) -> str:
        return file_sha256(self.d / "MANIFEST.json")

    @property
    def universe_sha256(self) -> str:
        return file_sha256(self.d / "universe.json")

    def members(self) -> list[dict]:
        return sorted(self.universe["members"], key=lambda m: m["rank"])

    def price(self, symbol: str, day: date, field: str) -> Optional[Decimal]:
        row = self.prices.get(symbol, {}).get(day.isoformat())
        if not row or row.get(field) in (None, ""):
            return None
        v = Decimal(str(row[field]))
        if v <= 0:
            raise DataError(f"{symbol} {day} {field}: non-positive price")
        return v

    def filings_by(self, symbol: str, cutoff: datetime) -> list[dict]:
        """The symbol's filings accepted at or before `cutoff`, newest first."""
        out = [f for f in self.filings if f["symbol"] == symbol and datetime.fromisoformat(f["accepted_at"]) <= cutoff]
        return sorted(out, key=lambda f: (f["accepted_at"], f["accession"]), reverse=True)


def write_data_dir(d: Path, *, synthetic: bool, sources: dict, prices: dict, actions: dict, universe: dict,
                   filings: list) -> str:
    """Write a data directory and its manifest; returns the manifest's SHA-256."""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    for name, obj in (("prices.json", prices), ("actions.json", actions), ("universe.json", universe),
                      ("filings.json", filings)):
        (d / name).write_text(json.dumps(obj, sort_keys=True, indent=1) + "\n")
    man = {"synthetic": synthetic, "sources": sources, "files": {n: file_sha256(d / n) for n in FILES}}
    (d / "MANIFEST.json").write_text(json.dumps(man, sort_keys=True, indent=1) + "\n")
    if synthetic:
        (d / "SYNTHETIC").write_text("Synthetic data for the offline dry run. Not market data.\n")
    return file_sha256(d / "MANIFEST.json")
