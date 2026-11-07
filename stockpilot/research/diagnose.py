"""Why did the existing strategies lose? Separate signal information, exits, timing, regime and costs.

Works on per-trade values precomputed while each day's frames were in memory (`trade_paths`), so a
year of days never has to be held at once."""
from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np

from ..backtest.engine import BTConfig, BTTrade, replay_portfolio, with_daily_rounding
from ..risk.fees import CostModel
from ..strategies import ALL_STRATEGIES
from .dataset import DayFrames, Sample, random_select, slot_replay
from .stats import day_block_bootstrap

HORIZONS = (15, 30, 60)


def fwd_gross(df: DayFrames, sym: str, entry_i: int, h: int) -> Optional[float]:
    """Gross % change from the entry bar's open to the open h minutes later (capped at the flatten time)."""
    sf = df.syms.get(sym)
    if sf is None or entry_i >= len(sf.mins):
        return None
    target = min(sf.mins[entry_i] + h, df.flatten_minute)
    j = min(int(np.searchsorted(sf.mins, target)), len(sf.mins) - 1)
    o = sf.fv["open"].a
    return float((o[j] / o[entry_i] - 1) * 100) if o[entry_i] > 0 else None


def trade_paths(df: DayFrames, t: BTTrade) -> dict:
    """Forward moves after the entry and the best/worst excursion while the trade was open."""
    sf = df.syms[t.symbol]
    a, b = t.entry_i, max(t.entry_i, min(t.exit_i, len(sf.mins) - 1))
    out = {f"fwd{h}": fwd_gross(df, t.symbol, t.entry_i, h) for h in HORIZONS}
    out["mfe"] = float((np.max(sf.fv["high"].a[a:b + 1]) / t.ref_entry - 1) * 100)
    out["mae"] = float((np.min(sf.fv["low"].a[a:b + 1]) / t.ref_entry - 1) * 100)
    return out


def key(t: BTTrade) -> tuple:
    return (t.day, t.symbol, t.strategy, t.entry_i)


def _mean(x) -> float:
    x = [v for v in x if v is not None]
    return float(np.mean(x)) if len(x) else 0.0


def diagnose(trades: list[BTTrade], paths: dict[tuple, dict], dense: list[Sample], dense_paths: dict[tuple, dict],
             cfg: BTConfig, regimes: dict[date, str], spy_by_day: dict) -> dict:
    real = [t for t in trades if not t.random_baseline]
    cost = CostModel().round_trip_cost_pct(cfg.notional, 100.0, cfg.spread_bps)
    out: dict = {"breakEvenPct": cost}

    # 1) does the entry signal carry information? forward gross moves vs the same-time unconditional average
    uncond = {h: _mean([dense_paths.get(key(s.trade), {}).get(f"fwd{h}") for s in dense]) for h in HORIZONS}
    info = {}
    for sid in ALL_STRATEGIES:
        ts = [t for t in real if t.strategy == sid]
        row = {"trades": len(ts), "realizedGrossPct": _mean([t.gross_ret_pct for t in ts]),
               "realizedNetPct": _mean([t.ret_pct for t in ts])}
        for h in HORIZONS:
            pairs = [(t.day, paths[key(t)][f"fwd{h}"]) for t in ts if paths.get(key(t), {}).get(f"fwd{h}") is not None]
            bs = day_block_bootstrap([v - uncond[h] for _, v in pairs], [d for d, _ in pairs])
            row[f"fwd{h}GrossPct"] = _mean([v for _, v in pairs])
            row[f"fwd{h}VsUnconditional"] = {"diff": bs["mean"], "ci90": [bs["lo"], bs["hi"]]}
        # 2) exits: did the exit rules give back moves the entries found?
        pp = [paths[key(t)] for t in ts if key(t) in paths]
        row["avgMfePct"] = _mean([p["mfe"] for p in pp])
        row["avgMaePct"] = _mean([p["mae"] for p in pp])
        row["mfeAboveCostButLost"] = (sum(1 for t in ts if key(t) in paths and paths[key(t)]["mfe"] > cost
                                          and t.gross_ret_pct < 0) / len(ts)) if ts else 0.0
        reasons: dict[str, list[float]] = {}
        for t in ts:
            reasons.setdefault(t.reason.split(":")[0].split(" (")[0], []).append(t.gross_ret_pct)
        row["byExit"] = {k: {"n": len(v), "grossPct": _mean(v)} for k, v in sorted(reasons.items())}
        row["avgHoldMinutes"] = _mean([t.exit_minute - t.minute for t in ts])
        info[sid] = row
    out["unconditionalFwdGrossPct"] = uncond
    out["strategies"] = info

    # 3) time of day
    buckets = [(0, 30, "9:30-10:00"), (30, 60, "10:00-10:30"), (60, 120, "10:30-11:30"), (120, 240, "11:30-13:30"),
               (240, 330, "13:30-15:00"), (330, 391, "15:00-close")]
    out["timeOfDay"] = {lab: {"trades": len(ts), "grossPct": _mean([t.gross_ret_pct for t in ts]),
                              "netPct": _mean([t.ret_pct for t in ts])}
                        for a, b, lab in buckets for ts in [[t for t in real if a <= t.minute < b]]}

    # 4) market regime (labels known only after the day — reporting only, never used to trade)
    out["regimes"] = {lab: {"trades": len(ts), "grossPct": _mean([t.gross_ret_pct for t in ts]),
                            "netPct": _mean([t.ret_pct for t in ts])}
                      for lab in sorted(set(regimes.values())) for ts in [[t for t in real if regimes.get(t.day) == lab]]}

    # 5) are moves big enough to pay for costs?
    moves = np.abs([s.trade.gross_ret_pct for s in dense])
    out["moveVsCost"] = {"medianAbs30mGrossPct": float(np.median(moves)) if len(moves) else 0.0,
                         "shareAbove": float((moves > cost).mean()) if len(moves) else 0.0,
                         "meanDense30mGrossPct": _mean([s.trade.gross_ret_pct for s in dense]),
                         "meanDense30mNetPct": _mean([s.trade.ret_pct for s in dense])}

    # 6) does the ranker choose better than simple baselines on the same slots?
    def pf(ts):
        ts2 = with_daily_rounding(ts, cfg)
        return {"trades": len(ts2), "netPct": _mean([t.ret_pct for t in ts2]),
                "grossPct": _mean([t.gross_ret_pct for t in ts2]), "totalPnl": float(sum(t.pnl for t in ts2))}
    out["selection"] = {"ranker": pf(replay_portfolio(real, cfg, True, spy_by_day)),
                        "firstCome": pf(replay_portfolio(real, cfg, False, spy_by_day)),
                        "randomPick": pf(slot_replay(real, cfg, random_select(11))),
                        "noTrade": {"trades": 0, "netPct": 0.0, "grossPct": 0.0, "totalPnl": 0.0}}
    return out
