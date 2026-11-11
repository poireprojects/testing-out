"""The monthly evidence pack: everything a decision may use, frozen and hashed before the decision.

Built at the cutoff (the close of the last session before the fill month) from:
* the AI portfolio's holdings, valued at the cutoff close, and the theses recorded when they were bought;
* for each universe member, passages from its newest filings accepted at or before the cutoff (`P` items) and a
  valuation row computed by code from the cutoff close and the newest filing's facts (`V` items).
Every item carries the time it became available; the pack refuses anything later than the cutoff (no look-ahead).
A missing VOO or holding price at the cutoff makes the pack impossible (`InputsIncomplete`); a candidate without a
price or facts is listed as not eligible to buy that month.
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
from .data import MarketData
from .registration import BENCHMARK, FROZEN, Slot, canonical_json, config_sha256

TWO = Decimal("0.01")


class InputsIncomplete(RuntimeError):
    """The pack cannot be built from the data available now."""


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
                "items": [e.record() for e in self.items]}

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
            out.append(f"  thesis recorded for {t['symbol']} in {t['month']}: {t['why_outperform_voo']} "
                       f"Invalidation: {t['invalidation']}")
        out.append("\nCANDIDATES (frozen universe, rank order):")
        for c in self.candidates:
            out.append(f"  {c['rank']}. {c['symbol']} {c['name']}" + ("" if c["eligible"] else f" (not eligible: {c['why']})"))
        for e in self.items:
            out.append(f"\n[{e.id}] {e.kind} | {e.source} | available {e.available_at.astimezone(ET).strftime('%Y-%m-%d %H:%M %Z')}"
                       f"\n{e.text}")
        return "\n".join(out)


def _valuation(sym: str, close: Decimal, f: dict, cutoff: datetime) -> Optional[tuple[str, dict]]:
    fx = f.get("facts") or {}
    need = ("shares_outstanding", "revenue_ttm_musd", "net_income_ttm_musd", "eps_diluted_ttm")
    if any(fx.get(k) in (None, "") for k in need):
        return None
    sh, rev, ni, eps = (Decimal(str(fx[k])) for k in need)
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
        raise InputsIncomplete(f"no {BENCHMARK} close for {day}")
    closes = {}
    for s in book.held():
        c = data.price(s, day, "close")
        if c is None:
            raise InputsIncomplete(f"no close for held {s} on {day}")
        closes[s] = c
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
    for m in data.members():
        sym = m["symbol"]
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
        close = data.price(sym, day, "close")
        facts = next((f for f in fl if f.get("facts")), None)
        val = _valuation(sym, close, facts, cutoff) if close is not None and facts else None
        why = "no close at the cutoff" if close is None else ("no filing with facts by the cutoff" if not facts
                                                             else "incomplete facts")
        if val:
            items.append(Evidence(f"V.{sym}", "valuation", f"computed from the {day} close and {facts['accession']}",
                                  cutoff, val[0]))
        cands.append({"rank": m["rank"], "symbol": sym, "name": m["name"], "eligible": val is not None,
                      "why": "" if val else why, "passages": n})
    return MonthPack(slot.index, slot.month, cutoff, slot.deadline, built_at, data.synthetic, str(book.cash),
                     str(inv.quantize(TWO)), str(voo), holdings, cands, prior_theses, items)


def load_pack_record(path) -> dict:
    return json.loads(open(path).read())
