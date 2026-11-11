"""Prices and corporate actions: normalize a vendor's unadjusted daily bars and its action lists.

Raw formats (one file per symbol, plus one actions file):

    {"symbol": "ABC", "adjustment": "raw", "bars": [{"t": "YYYY-MM-DD", "o": "12.34", "c": "12.56"}, ...]}
    {"cash_dividends": [{symbol, ex_date, payable_date, rate}], "forward_splits": [{symbol, ex_date, old_rate,
     new_rate}], "reverse_splits": [...], "name_changes": [{old_symbol, new_symbol, process_date}], <other kinds>}

Rules:
* only unadjusted bars are accepted (`adjustment` must be "raw"). A split inside a series must show up as a price
  step at its ex-date; a series that moves across a split ex-date by less than half the split's ratio already looks
  split-adjusted and is rejected, because the ledger applies splits itself and would otherwise adjust twice;
* a symbol change merges the old and new symbols' bars under the registered symbol; two different bars for one day
  are refused;
* dividends keep ex and pay dates (a pay date before its ex-date is refused); splits keep their ratio;
* any other action kind (mergers, spin-offs, delistings, redemptions, ...) is not supported: it is listed in
  `unsupported`, and the pilot treats it as a data gap for a held stock and as a reproducible exclusion for a
  candidate. Nothing is modelled that the code does not implement.
"""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Optional

from .raw import RawFile

SUPPORTED = ("cash_dividends", "forward_splits", "reverse_splits", "name_changes")


class MarketError(ValueError):
    pass


def normalize(bars_files: list[RawFile], actions_file: Optional[RawFile], registered: list[str]) -> dict:
    acts = actions_file.json() if actions_file else {}
    renames = {r["old_symbol"]: r["new_symbol"] for r in acts.get("name_changes", [])}
    canon = {}
    for sym in registered:
        canon[sym] = sym
        for old, new in renames.items():
            if new == sym:
                canon[old] = sym
            if old == sym:
                canon[new] = sym

    def reg(s: str) -> Optional[str]:
        return canon.get(s)

    prices: dict[str, dict] = {}
    prov = []
    for f in bars_files:
        j = f.json()
        if j.get("adjustment") != "raw":
            raise MarketError(f"{f.relpath}: bars are '{j.get('adjustment')}', not unadjusted ('raw')")
        sym = reg(j["symbol"])
        if sym is None:
            continue
        series = prices.setdefault(sym, {})
        for b in j["bars"]:
            row = {"open": str(Decimal(str(b["o"]))), "close": str(Decimal(str(b["c"])))}
            old = series.get(b["t"])
            if old is not None and old != row:
                raise MarketError(f"{sym} {b['t']}: two different bars (symbol change overlap)")
            series[b["t"]] = row
        prov.append(f.provenance() | {"symbol": j["symbol"], "registered_as": sym})
    splits, dividends, unsupported = [], [], []
    for d in acts.get("cash_dividends", []):
        sym = reg(d["symbol"])
        if sym is None:
            continue
        if d["payable_date"] < d["ex_date"]:
            raise MarketError(f"{sym} dividend pays {d['payable_date']} before its ex-date {d['ex_date']}")
        dividends.append({"symbol": sym, "ex_date": d["ex_date"], "pay_date": d["payable_date"],
                          "amount": str(Decimal(str(d["rate"])))})
    for kind in ("forward_splits", "reverse_splits"):
        for s in acts.get(kind, []):
            sym = reg(s["symbol"])
            if sym is None:
                continue
            splits.append({"symbol": sym, "ex_date": s["ex_date"], "num": int(s["new_rate"]), "den": int(s["old_rate"])})
    for kind, rows in acts.items():
        if kind in SUPPORTED:
            continue
        for r in rows:
            sym = reg(r.get("symbol") or r.get("acquiree_symbol") or r.get("old_symbol") or "")
            if sym is not None:
                unsupported.append({"symbol": sym, "kind": kind,
                                    "date": r.get("effective_date") or r.get("ex_date") or r.get("process_date"),
                                    "detail": {k: v for k, v in r.items() if k != "symbol"}})
    for s in splits:                                          # double-adjustment check
        series = prices.get(s["symbol"], {})
        days = sorted(series)
        before = [d for d in days if d < s["ex_date"]]
        if s["ex_date"] in series and before:
            observed = Decimal(series[s["ex_date"]]["open"]) / Decimal(series[before[-1]]["close"])
            expected = Decimal(s["den"]) / Decimal(s["num"])
            if abs(math.log(float(observed))) < abs(math.log(float(expected))) / 2:
                raise MarketError(f"{s['symbol']}: no price step at the {s['num']}:{s['den']} split on {s['ex_date']} "
                                  "(the series looks already split-adjusted; refusing double adjustment)")
    changes = [{"old_symbol": o, "new_symbol": n, "registered_as": reg(n) or reg(o)} for o, n in renames.items()
               if reg(n) or reg(o)]
    return {"prices": prices, "actions": {"splits": sorted(splits, key=lambda x: (x["ex_date"], x["symbol"])),
                                          "dividends": sorted(dividends, key=lambda x: (x["ex_date"], x["symbol"])),
                                          "unsupported": sorted(unsupported, key=lambda x: (str(x["date"]), x["symbol"])),
                                          "symbol_changes": changes},
            "provenance": prov + ([actions_file.provenance()] if actions_file else [])}
