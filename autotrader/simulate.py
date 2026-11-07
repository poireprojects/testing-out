"""Simulated execution with the phase-4 cost conventions, including its integrity corrections.

* Entry: the quote after the decision plus latency, paying half the quoted spread plus 5 bps slippage.
* Exits on 15-minute bars that start after entry. The bar that straddles the entry time can't tell whether its
  low came before or after the fill: if it reaches the stop the trade is treated as stopped (the pessimistic
  reading) and flagged; a target is never credited inside that bar.
* If a bar reaches both the stop and the target, the stop is assumed first.
* Time exit at the exit quote, paying half the spread plus slippage; without a quote, the bar's open, flagged.
* Fees (SEC, TAF, CAT) are summed per day per fee type over one decider's trades, rounded up to a cent once.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Optional, Protocol

import pandas as pd

from stockpilot.risk.fees import CostModel

from .risk import Quote

SLIP_BPS = 5.0


class PriceProvider(Protocol):
    def quote(self, symbol: str, t: datetime) -> Optional[Quote]: ...

    def bars15(self, symbol: str, day: date) -> pd.DataFrame: ...


def _ceil_cent(x: float) -> float:
    return math.ceil(round(x * 100, 9)) / 100 if x > 0 else 0.0


def fill_entry(q: Quote) -> float:
    hs = q.spread_bps / 2
    return q.mid * (1 + (hs + SLIP_BPS) / 1e4)


def simulate_exit(entry_time: datetime, entry_px: float, entry_hs_bps: float, stop: float, target: Optional[float],
                  exit_time: datetime, bars: pd.DataFrame, exit_quote: Optional[Quote]) -> dict:
    flags: list[str] = []
    if bars is not None and not bars.empty:
        window = bars[(bars.index + timedelta(minutes=15) > entry_time) & (bars.index < exit_time)]
        for t, r in window.iterrows():
            straddles = t < entry_time
            if r["low"] <= stop:
                if straddles:
                    flags.append("stop reached in the bar that straddles entry: counted as stopped (pessimistic)")
                    return {"exit_ref": stop, "reason": "stop", "exit_at": entry_time, "flags": flags, "hs_bps": entry_hs_bps}
                return {"exit_ref": min(stop, float(r["open"])), "reason": "stop", "exit_at": t, "flags": flags,
                        "hs_bps": entry_hs_bps}
            if target and not straddles and r["high"] >= target:
                return {"exit_ref": max(target, float(r["open"])), "reason": "target", "exit_at": t, "flags": flags,
                        "hs_bps": entry_hs_bps}
    if exit_quote is not None:
        return {"exit_ref": exit_quote.mid, "reason": "time", "exit_at": exit_quote.time, "flags": flags,
                "hs_bps": exit_quote.spread_bps / 2}
    if bars is not None and not bars.empty:
        later = bars[bars.index >= exit_time]
        if not later.empty:
            flags.append("no exit quote: used the next bar's open")
            return {"exit_ref": float(later["open"].iloc[0]), "reason": "time", "exit_at": later.index[0],
                    "flags": flags, "hs_bps": entry_hs_bps}
    return {"exit_ref": None, "reason": "no exit price", "exit_at": None, "flags": flags + ["unscorable"], "hs_bps": None}


def fee_parts(day: date, notional: float, entry_px: float, exit_px: float) -> dict:
    cm = CostModel.on(day)
    qty = notional / entry_px
    sb, bb = cm.sale_breakdown(notional * exit_px / entry_px, qty), cm.buy_breakdown(qty)
    return {"sec": sb.sec, "taf": sb.taf, "cat": sb.cat + bb.cat}


def daily_fees(trades: list[dict]) -> list[float]:
    """Dollar fee per trade: each fee type summed over the day's trades, rounded up once, shared pro rata."""
    tot = {k: sum(t["fee_parts"][k] for t in trades) for k in ("sec", "taf", "cat")}
    out = []
    for t in trades:
        out.append(sum(_ceil_cent(tot[k]) * t["fee_parts"][k] / tot[k] for k in tot if tot[k] > 0))
    return out


def complete_day(opened: list[dict], prices: PriceProvider, exit_quote_time) -> list[dict]:
    """Close every simulated position opened on one day for one decider; returns outcomes with net results."""
    done = []
    for o in opened:
        day = o["entry_time"].date()
        ex_t = o["exit_time"]
        q_exit = prices.quote(o["symbol"], exit_quote_time(day, ex_t))
        res = simulate_exit(o["entry_time"], o["entry_px"], o["entry_hs_bps"], o["stop_price"], o.get("target_price"),
                            ex_t, prices.bars15(o["symbol"], day), q_exit)
        if res["exit_ref"] is None:
            done.append(o | {"scored": False, **res})
            continue
        exit_px = res["exit_ref"] * (1 - (res["hs_bps"] + SLIP_BPS) / 1e4)
        done.append(o | res | {"scored": True, "exit_px": exit_px,
                               "fee_parts": fee_parts(day, o["notional"], o["entry_px"], exit_px)})
    scored = [d for d in done if d["scored"]]
    for d, fee in zip(scored, daily_fees(scored) if scored else []):
        before = (d["exit_px"] / d["entry_px"] - 1) * 100
        d.update({"gross_pct": (d["exit_ref"] / d["entry_mid"] - 1) * 100, "fees_usd": fee,
                  "net_pct": before - fee / d["notional"] * 100,
                  "pnl_usd": d["notional"] * before / 100 - fee})
    return done
