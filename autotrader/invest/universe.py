"""The disclosed candidate-universe rule (registration FROZEN["universe"]), applied once before launch.

Input: the investments of Vanguard 500 Index Fund's most recent public N-PORT-P report (EDGAR), as parsed rows
{name, lei, cusip, asset_cat, value_usd}, and a reviewed CUSIP -> {symbol, cik} map (N-PORT lists CUSIPs, not
tickers; every mapped row is checked by a person against EDGAR's company list before the freeze). Rule:
* common equity only (`asset_cat == "EC"`);
* one security per issuer (same LEI, or the same name when no LEI is given): the class with the larger value;
* rank by value, largest first; ties by CUSIP; keep the first `size`.
"""
from __future__ import annotations

from decimal import Decimal

from .registration import FROZEN


class UniverseError(ValueError):
    pass


def select_universe(holdings: list[dict], cusip_map: dict[str, dict], size: int = FROZEN["universe"]["size"]) -> list[dict]:
    def order(h: dict) -> tuple:
        return -Decimal(str(h["value_usd"])), h["cusip"]

    best: dict[str, dict] = {}
    for h in holdings:
        if h.get("asset_cat") != "EC":
            continue
        issuer = (h.get("lei") or "").strip() or " ".join(h["name"].upper().split())
        if issuer not in best or order(h) < order(best[issuer]):
            best[issuer] = h
    ranked = sorted(best.values(), key=order)[:size]
    out = []
    for i, h in enumerate(ranked):
        m = cusip_map.get(h["cusip"])
        if not m:
            raise UniverseError(f"CUSIP {h['cusip']} ({h['name']}) has no reviewed symbol mapping")
        out.append({"rank": i + 1, "symbol": m["symbol"], "name": h["name"], "cik": m["cik"], "cusip": h["cusip"],
                    "value_usd": str(h["value_usd"])})
    return out
