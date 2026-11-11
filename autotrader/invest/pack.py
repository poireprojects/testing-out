"""The monthly evidence pack: everything a decision may use, frozen and hashed before the decision.

Built at the cutoff (the close of the last session before the fill month) from:
* the AI portfolio's holdings, valued at the cutoff close, and the theses recorded when they were bought;
* for each universe member, passages from its newest filings accepted at or before the cutoff (`P` items) and a
  valuation row computed by code from the cutoff close and the newest 10-K/10-Q's facts (`V` items);
* a coverage record for every universe member and every held stock (`MarketData.coverage`): what EDGAR's index
  says was required, whether it was collected, and the reason for any exclusion.
Every item carries the time it became available; the pack refuses anything later than the cutoff (no look-ahead).
The pack cannot be built (`InputsIncomplete`, so no model is asked) when VOO or a held stock has no cutoff close, or
when any symbol's research inputs failed to collect. A candidate is excluded only for a reproducible reason
(`unavailable`: no 10-K/10-Q by the cutoff in the index, or a recorded no-trading exception), never for a missing
download. Once frozen (its hash in the ledger), a pack is read back from its file and never rebuilt from newer data.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from stockpilot.clock import ET

from ..evidence import Evidence, LookAheadError
from .accounting import Book
from .data import FACTS, MarketData
from .registration import BENCHMARK, FROZEN, Slot, canonical_json, config_sha256

TWO = Decimal("0.01")


class InputsIncomplete(RuntimeError):
    """The pack cannot be built from the data available now (a research-input failure)."""

    def __init__(self, msg: str, coverage: Optional[list] = None):
        super().__init__(msg)
        self.coverage = coverage or []


def _cut(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    head = text[:limit + 1]
    return head[:head.rfind(" ")].rstrip() if " " in head else text[:limit]


def _fmt_int(x: Decimal) -> str:
    return f"{int(x.to_integral_value()):,}"


@dataclass
class MonthPack:
    slot: int
    month: str
    cutoff: datetime
    deadline: datetime
    built_at: datetime
    synthetic: bool
    cash: str
    investable: str
    voo_close: str
    holdings: list[dict]
    candidates: list[dict]
    prior_theses: list[dict]
    items: list[Evidence] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)
    inputs: dict = field(default_factory=dict)

    def __post_init__(self):
        ids = [e.id for e in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate evidence ids")
        for e in self.items:
            if e.available_at > self.cutoff:
                raise LookAheadError(f"{e.id} ({e.source}) became available at {e.available_at.isoformat()}, after the "
                                     f"cutoff {self.cutoff.isoformat()}")
        if self.built_at < self.cutoff:
            raise ValueError("a pack cannot be built before its cutoff")

    def get(self, eid: str) -> Optional[Evidence]:
        return next((e for e in self.items if e.id == eid), None)

    def holding(self, symbol: str) -> Optional[dict]:
        return next((h for h in self.holdings if h["symbol"] == symbol), None)

    def eligible(self) -> list[str]:
        return [c["symbol"] for c in self.candidates if c["eligible"]]

    def record(self) -> dict:
        return {"experiment": FROZEN["experiment"], "config_sha256": config_sha256(), "slot": self.slot,
                "month": self.month, "cutoff": self.cutoff.isoformat(), "deadline": self.deadline.isoformat(),
                "built_at": self.built_at.isoformat(), "synthetic": self.synthetic, "cash": self.cash,
                "investable": self.investable, "voo_close": self.voo_close, "holdings": self.holdings,
                "candidates": self.candidates, "prior_theses": self.prior_theses,
                "items": [e.record() for e in self.items], "coverage": self.coverage, "inputs": self.inputs}

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.record())).hexdigest()

    def prompt_text(self) -> str:
        lim = FROZEN["limits"]
        out = [f"MONTHLY DECISION {self.month} (slot {self.slot} of {FROZEN['months']})."
               + (" SYNTHETIC DATA: invented companies and prices for an offline test." if self.synthetic else ""),
               f"Everything below was available by the cutoff, {self.cutoff.astimezone(ET):%Y-%m-%d %H:%M %Z}. "
               f"Trades fill at the next session's open; your answer must be frozen before "
               f"{self.deadline.astimezone(ET):%Y-%m-%d %H:%M %Z}.",
               f"Limits: {BENCHMARK} {lim['voo_min_pct']}-{lim['voo_max_pct']}% of the portfolio; at most "
               f"{lim['max_stocks']} individual stocks, each set to at most {lim['stock_max_pct']}%; long only.",
               f"\nPORTFOLIO at the cutoff close: cash {self.cash} USD; cash plus positions {self.investable} USD.",
               f"{BENCHMARK} close {self.voo_close} USD."]
        for h in self.holdings:
            out.append(f"  {h['symbol']}: {h['qty']} shares at {h['close']} = {h['value']} USD ({h['weight_pct']}%)")
        if not self.holdings:
            out.append("  (no positions)")
        for t in self.prior_theses:
            out.append(f"  thesis recorded for {t['symbol']} in {t['month']}: {t['mispricing']} "
                       f"Invalidation: {t['invalidation']}")
        out.append("\nCANDIDATES (frozen universe, rank order):")
        for c in self.candidates:
            out.append(f"  {c['rank']}. {c['symbol']} {c['name']}" + ("" if c["eligible"] else f" (not eligible: {c['why']})"))
        for e in self.items:
            out.append(f"\n[{e.id}] {e.kind} | {e.source} | available {e.available_at.astimezone(ET).strftime('%Y-%m-%d %H:%M %Z')}"
                       f"\n{e.text}")
        return "\n".join(out)


def _valuation(sym: str, close: Decimal, f: dict, cutoff: datetime) -> tuple[str, dict]:
    fx = f["facts"]
    sh, rev, ni, eps = (Decimal(str(fx[k])) for k in FACTS)
    mcap = close * sh / Decimal(1_000_000)
    pe = f"{(close / eps).quantize(TWO)}" if eps > 0 else "not meaningful (EPS at or below zero)"
    ps = f"{(mcap / rev).quantize(TWO)}" if rev > 0 else "not meaningful"
    acc = datetime.fromisoformat(f["accepted_at"]).astimezone(ET)
    text = (f"Valuation of {sym} at the {cutoff.astimezone(ET):%Y-%m-%d} close (computed by code): price {close} USD; "
            f"shares outstanding {_fmt_int(sh)} ({f['form']} {f['accession']}, accepted {acc:%Y-%m-%d %H:%M %Z}); "
            f"market value {_fmt_int(mcap)} million USD; revenue, trailing four quarters {_fmt_int(rev)} million USD; "
            f"net income, trailing four quarters {_fmt_int(ni)} million USD; diluted EPS, trailing four quarters "
            f"{eps} USD; P/E {pe}; price/sales {ps}.")
    return text, {"close": str(close), "market_value_musd": str(mcap.quantize(TWO)), "pe": pe, "ps": ps}


def build_pack(slot: Slot, book: Book, data: MarketData, built_at: datetime, prior_theses: list[dict]) -> MonthPack:
    cutoff, day = slot.cutoff.close, slot.cutoff.day
    lim = FROZEN["evidence"]
    voo = data.price(BENCHMARK, day, "close")
    if voo is None:
        raise InputsIncomplete(f"no {BENCHMARK} close was collected for {day}")
    closes = {}
    for s in book.held():
        c = data.price(s, day, "close")
        if c is None:
            raise InputsIncomplete(f"no close was collected for held {s} on {day}")
        closes[s] = c
    members = data.members()
    symbols = [m["symbol"] for m in members] + [s for s in book.stocks() if s not in {m["symbol"] for m in members}]
    coverage = [data.coverage(s, cutoff, day) for s in symbols]
    failed = [c for c in coverage if c["status"] == "collection_failure"]
    if failed:
        raise InputsIncomplete("research inputs incomplete for " + "; ".join(f"{c['symbol']}: {c['reason']}"
                                                                           for c in failed), coverage)
    inv = book.investable(closes)
    holdings = []
    for s in book.stocks():
        v = book.qty[s] * closes[s]
        holdings.append({"symbol": s, "qty": str(book.qty[s]), "close": str(closes[s]), "value": str(v.quantize(TWO)),
                         "weight_pct": str((v / inv * 100).quantize(TWO))})
    if BENCHMARK in closes:
        v = book.qty[BENCHMARK] * closes[BENCHMARK]
        holdings.append({"symbol": BENCHMARK, "qty": str(book.qty[BENCHMARK]), "close": str(voo),
                         "value": str(v.quantize(TWO)), "weight_pct": str((v / inv * 100).quantize(TWO))})
    items, cands = [], []
    cov = {c["symbol"]: c for c in coverage}
    for m in members:
        sym = m["symbol"]
        if cov[sym]["status"] != "complete":
            cands.append({"rank": m["rank"], "symbol": sym, "name": m["name"], "eligible": False,
                          "why": cov[sym]["reason"], "passages": 0})
            continue
        fl = data.filings_by(sym, cutoff)
        n = 0
        for f in fl:
            for k, p in enumerate(f.get("passages") or []):
                if n >= lim["max_passages_per_candidate"]:
                    break
                items.append(Evidence(f"{sym}/{f['accession']}/P{k + 1}", "filing",
                                      f"{'SYNTHETIC ' if data.synthetic else ''}SEC {f['form']} {f['accession']} "
                                      f"({m['name']}), {p.get('section', 'text')}",
                                      datetime.fromisoformat(f["accepted_at"]), _cut(p["text"], lim["max_passage_chars"])))
                n += 1
        periodic = next(f for f in fl if f["accession"] == cov[sym]["required"][0])
        text, _ = _valuation(sym, data.price(sym, day, "close"), periodic, cutoff)
        items.append(Evidence(f"V.{sym}", "valuation", f"computed from the {day} close and {periodic['accession']}",
                              cutoff, text))
        cands.append({"rank": m["rank"], "symbol": sym, "name": m["name"], "eligible": True, "why": "", "passages": n})
    inputs = {"data_manifest_sha256": data.manifest_sha256, "universe_sha256": data.universe_sha256}
    return MonthPack(slot.index, slot.month, cutoff, slot.deadline, built_at, data.synthetic, str(book.cash),
                     str(inv.quantize(TWO)), str(voo), holdings, cands, prior_theses, items, coverage, inputs)


def pack_from_record(rec: dict) -> MonthPack:
    items = [Evidence(i["id"], i["kind"], i["source"], datetime.fromisoformat(i["available_at"]), i["text"], i.get("url"))
             for i in rec["items"]]
    return MonthPack(rec["slot"], rec["month"], datetime.fromisoformat(rec["cutoff"]),
                     datetime.fromisoformat(rec["deadline"]), datetime.fromisoformat(rec["built_at"]), rec["synthetic"],
                     rec["cash"], rec["investable"], rec["voo_close"], rec["holdings"], rec["candidates"],
                     rec["prior_theses"], items, rec.get("coverage", []), rec.get("inputs", {}))


def load_pack(path, expect_sha256: str) -> MonthPack:
    """A frozen pack, read back from its file; refused if the file is missing or not the one the ledger froze."""
    pack = pack_from_record(json.loads(open(path).read()))
    if pack.sha256 != expect_sha256:
        raise ValueError(f"{path}: not the pack frozen in the ledger (SHA-256 mismatch)")
    return pack
