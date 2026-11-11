"""Read-only access to a pilot data directory. The format is the same for synthetic and real data:

    MANIFEST.json   {"synthetic": bool, "sources": {...}, "files": {name: sha256}}
    prices.json     {SYMBOL: {"YYYY-MM-DD": {"open": "123.45", "close": "124.10"}}}   as traded, unadjusted
    actions.json    {"splits": [{symbol, ex_date, num, den}], "dividends": [{symbol, ex_date, pay_date, amount}]}
    universe.json   {"rule": str, "source": str, "members": [{rank, symbol, name, cik}]}
    filings.json    [{symbol, cik, accession, form, accepted_at (ISO, tz), period_end, facts: {...}, passages: [...]}]
    index.json      {"symbols": {SYMBOL: {cik, retrieved_at, source, filings: [{accession, form, accepted_at, items}]}},
                     "price_exceptions": [{symbol, date, reason, source}]}

`filings.json` is what was collected; `index.json` is what EDGAR's submission index says exists (written by the
collector, never derived from `filings.json`). Coverage compares the two, so a missing download is a collection
failure, never evidence that a company has no usable financial information. `index.json` may be absent: then
every symbol is a collection failure.

A price that is absent is missing data: callers record a gap or a collection failure, never a substitute.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

FILES = ("prices.json", "actions.json", "universe.json", "filings.json")
OPTIONAL = ("index.json",)
PERIODIC = ("10-K", "10-Q")
FACTS = ("shares_outstanding", "revenue_ttm_musd", "net_income_ttm_musd", "eps_diluted_ttm")


class DataError(RuntimeError):
    pass


def file_sha256(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class MarketData:
    def __init__(self, d: Path):
        self.d = Path(d)
        self.manifest = json.loads((self.d / "MANIFEST.json").read_text())
        for name in FILES + OPTIONAL:
            want = self.manifest["files"].get(name)
            if name in OPTIONAL and want is None and not (self.d / name).exists():
                continue
            if want is None or file_sha256(self.d / name) != want:
                raise DataError(f"{name}: missing from the manifest or its SHA-256 does not match")
        self.synthetic = bool(self.manifest.get("synthetic"))
        self.prices = json.loads((self.d / "prices.json").read_text())
        acts = json.loads((self.d / "actions.json").read_text())
        self.splits, self.dividends = acts.get("splits", []), acts.get("dividends", [])
        self.universe = json.loads((self.d / "universe.json").read_text())
        self.filings = json.loads((self.d / "filings.json").read_text())
        p = self.d / "index.json"
        self.index = json.loads(p.read_text()) if p.exists() else {"symbols": {}, "price_exceptions": []}
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
        """The symbol's collected filings accepted at or before `cutoff`, newest first."""
        out = [f for f in self.filings if f["symbol"] == symbol and datetime.fromisoformat(f["accepted_at"]) <= cutoff]
        return sorted(out, key=lambda f: (datetime.fromisoformat(f["accepted_at"]), f["accession"]), reverse=True)

    def coverage(self, symbol: str, cutoff: datetime, day: date) -> dict:
        """What the pack needs for `symbol` at `cutoff`, and whether it was collected.

        status `complete`; `unavailable` (public evidence legitimately absent, with a reproducible reason: the index
        shows no 10-K or 10-Q by the cutoff, or the price source records no trading); or `collection_failure`
        (something that exists, or may exist, was not collected)."""
        idx = (self.index.get("symbols") or {}).get(symbol)
        out = {"symbol": symbol, "required": [], "index_retrieved_at": None, "index_source": None}
        if idx is None:
            return out | {"status": "collection_failure", "reason": "no EDGAR submission index was collected"}
        out |= {"index_retrieved_at": idx["retrieved_at"], "index_source": idx.get("source")}
        if datetime.fromisoformat(idx["retrieved_at"]) < cutoff:
            return out | {"status": "collection_failure",
                          "reason": "the submission index was retrieved before the cutoff, so filings accepted after "
                                    "it are unknown"}
        at = lambda f: datetime.fromisoformat(f["accepted_at"])  # noqa: E731  (offsets differ: compare times)
        listed = sorted((f for f in idx["filings"] if at(f) <= cutoff), key=lambda f: (at(f), f["accession"]))
        periodic = [f for f in listed if f["form"] in PERIODIC]
        if not periodic:
            return out | {"status": "unavailable",
                          "reason": f"the EDGAR index (retrieved {idx['retrieved_at']}) lists no 10-K or 10-Q "
                                    "accepted by the cutoff"}
        last = periodic[-1]
        required = [last] + [f for f in listed if f["form"] == "8-K" and "2.02" in (f.get("items") or "")
                             and at(f) > at(last)]
        out["required"] = [f["accession"] for f in required]
        have = {f["accession"]: f for f in self.filings if f["symbol"] == symbol}
        for f in required:
            got = have.get(f["accession"])
            if got is None:
                return out | {"status": "collection_failure",
                              "reason": f"{f['form']} {f['accession']} is in the index but was not collected"}
            if not got.get("passages"):
                return out | {"status": "collection_failure",
                              "reason": f"{f['form']} {f['accession']} was collected without its text"}
            if f is last and any((got.get("facts") or {}).get(k) in (None, "") for k in FACTS):
                return out | {"status": "collection_failure",
                              "reason": f"{f['form']} {f['accession']} was collected without its XBRL facts"}
        if self.price(symbol, day, "close") is None:
            exc = next((e for e in self.index.get("price_exceptions") or []
                        if e["symbol"] == symbol and e["date"] == day.isoformat()), None)
            if exc is None:
                return out | {"status": "collection_failure", "reason": f"no close was collected for {day}"}
            return out | {"status": "unavailable", "reason": f"no close for {day}: {exc['reason']} ({exc['source']})"}
        return out | {"status": "complete", "reason": ""}


def write_data_dir(d: Path, *, synthetic: bool, sources: dict, prices: dict, actions: dict, universe: dict,
                   filings: list, index: Optional[dict] = None) -> str:
    """Write a data directory and its manifest; returns the manifest's SHA-256. Without `index` no index file is
    written, and every symbol's research inputs count as a collection failure."""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    objs = [("prices.json", prices), ("actions.json", actions), ("universe.json", universe), ("filings.json", filings)]
    if index is not None:
        objs.append(("index.json", index))
    elif (d / "index.json").exists():
        (d / "index.json").unlink()
    for name, obj in objs:
        (d / name).write_text(json.dumps(obj, sort_keys=True, indent=1) + "\n")
    man = {"synthetic": synthetic, "sources": sources, "files": {n: file_sha256(d / n) for n, _ in objs}}
    (d / "MANIFEST.json").write_text(json.dumps(man, sort_keys=True, indent=1) + "\n")
    if synthetic:
        (d / "SYNTHETIC").write_text("Synthetic data for the offline dry run. Not market data.\n")
    return file_sha256(d / "MANIFEST.json")
