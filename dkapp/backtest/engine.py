"""Bar-by-bar intraday backtester.

Realism rules (to avoid flattering results):
* No look-ahead: a signal on bar i (using only bars <= i) is filled at the OPEN of bar i+1.
* Every buy pays half the spread plus slippage; every sale pays half the spread, slippage
  and regulatory fees (rounded up to the cent, like the broker).
* If a bar touches both the stop and the target, the stop is assumed to hit first.
* If price gaps through the stop, the fill is at the (worse) open, not the stop.
* Positions are force-closed before the close (same rule as live trading).
* Strategies use fixed parameters — nothing is tuned on the test period.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

import numpy as np
import pandas as pd

from ..analysis.features import Baseline, feature_frame
from ..risk.fees import CostModel
from ..strategies import ALL_STRATEGIES, Context


class _Col:
    __slots__ = ("a",)

    def __init__(self, a: np.ndarray):
        self.a = a

    @property
    def iat(self):
        return self.a

    @property
    def iloc(self):
        return self.a


class FrameView:
    """numpy-backed stand-in for a feature DataFrame (same `f[col].iat[i]` / `.iloc[a:b]` API, ~20× faster)."""

    def __init__(self, df: pd.DataFrame):
        self.index = df.index
        self._n = len(df)
        self.cols = {c: _Col(df[c].to_numpy(dtype=float)) for c in df.columns}

    def __getitem__(self, c: str) -> _Col:
        return self.cols[c]

    def __len__(self) -> int:
        return self._n

    @property
    def empty(self) -> bool:
        return self._n == 0


@dataclass
class BTTrade:
    strategy: str
    symbol: str
    day: date
    entry_i: int
    exit_i: int
    entry_px: float
    exit_px: float
    notional: float
    pnl: float
    ret_pct: float
    reason: str
    random_baseline: bool = False


@dataclass
class BTConfig:
    notional: float = 20.0
    spread_bps: float = 3.0
    flatten_minutes_before_close: int = 10
    last_entry_minutes_before_close: int = 30
    no_entries_first_minutes: int = 5
    min_stop_pct: float = 0.4
    max_stop_pct: float = 3.0
    min_signal_score: float = 0.55
    costs: CostModel = field(default_factory=CostModel)
    random_baseline_draws: int = 3
    seed: int = 11


@dataclass
class DayInput:
    day: date
    session_open: pd.Timestamp
    session_minutes: int
    bars: dict[str, pd.DataFrame]          # symbol -> 1-min bars for the day
    baselines: dict[str, Baseline]         # computed from PRIOR days only
    spy_trend: Optional[np.ndarray] = None  # per-minute "SPY above VWAP" (known at each bar close)


def _simulate_exit(fv: FrameView, strat, start_i: int, entry_px: float, stop: float, target: float,
                   max_hold: int, flatten_i: int, cfg: BTConfig, use_strategy_exit: bool = True) -> tuple[int, float, str]:
    o, h, l = fv["open"].a, fv["high"].a, fv["low"].a
    n = len(fv)
    entry_i = start_i
    stop_now = stop
    risk_r = max(entry_px - stop, entry_px * 0.002)
    hi = entry_px
    pending_exit = None
    for j in range(start_i, n):
        if pending_exit is not None:  # strategy exit decided at previous close -> next open
            return j, o[j], pending_exit
        if j >= flatten_i:
            return j, o[j], "end of day"
        if j - entry_i >= max_hold:
            return j, o[j], "time"
        if o[j] <= stop_now:
            return j, o[j], "stop (gap)"
        if l[j] <= stop_now:
            return j, stop_now, "stop"
        if h[j] >= target:
            return j, target, "target"
        hi = max(hi, h[j])
        if hi >= entry_px + risk_r:
            stop_now = max(stop_now, entry_px * 1.0005)
        if hi >= entry_px + 1.5 * risk_r:
            stop_now = max(stop_now, hi - risk_r)
        if use_strategy_exit and j > entry_i:
            why = strat.exit(fv, j, entry_px, entry_i)
            if why:
                pending_exit = "strategy: " + why
    j = n - 1
    return j, fv["close"].a[j], "data end"


def _fill_trade(strategy: str, symbol: str, day: date, entry_i: int, exit_i: int, ref_entry: float, ref_exit: float,
                reason: str, cfg: BTConfig, rb: bool = False) -> BTTrade:
    buy = cfg.costs.fill_price("buy", ref_entry, cfg.spread_bps)
    sell = cfg.costs.fill_price("sell", ref_exit, cfg.spread_bps)
    qty = cfg.notional / buy
    fees = cfg.costs.sell_fees(qty * sell, qty)
    pnl = qty * (sell - buy) - fees
    return BTTrade(strategy, symbol, day, entry_i, exit_i, buy, sell, cfg.notional, pnl, pnl / cfg.notional * 100,
                   reason, rb)


def run_day(day: DayInput, strategies: list[str], cfg: BTConfig, rng: random.Random) -> list[BTTrade]:
    out: list[BTTrade] = []
    m = day.session_minutes
    flatten_i = m - cfg.flatten_minutes_before_close
    last_entry_i = m - cfg.last_entry_minutes_before_close
    for sym, bars in day.bars.items():
        if bars is None or len(bars) < 40:
            continue
        f = feature_frame(bars, day.session_open.to_pydatetime(), day.baselines.get(sym), m)
        fv = FrameView(f)
        # bar index -> minute-of-session index (bars can be missing on thin IEX data)
        mins = (fv["minute"].a - 1).astype(int)
        close, vwap = fv["close"].a, fv["vwap_s"].a
        n = len(fv)
        for sid in strategies:
            strat = ALL_STRATEGIES[sid]
            busy_until = -1
            for i in range(n - 1):
                mi = mins[i]
                if i <= busy_until or mi < cfg.no_entries_first_minutes or mi >= last_entry_i:
                    continue
                if not close[i] > vwap[i]:  # every strategy requires price above VWAP — cheap pre-filter
                    continue
                trend = None
                if day.spy_trend is not None and 0 <= mi < len(day.spy_trend):
                    trend = bool(day.spy_trend[mi])
                idea = strat.entry(fv, i, Context(sym, trend, m))
                if idea is None or idea.score < cfg.min_signal_score:
                    continue
                ref = close[i]
                stop_pct = (ref - idea.stop_price) / ref * 100
                if stop_pct > cfg.max_stop_pct:
                    continue
                stop = min(idea.stop_price, ref * (1 - cfg.min_stop_pct / 100))
                entry_i = i + 1
                entry_ref = fv["open"].a[entry_i]
                # flatten index in bar terms
                fl_i = int(np.searchsorted(mins, flatten_i))
                ex_i, ex_px, why = _simulate_exit(fv, strat, entry_i, entry_ref, stop, idea.target_price,
                                                  idea.max_hold_minutes, fl_i, cfg)
                out.append(_fill_trade(sid, sym, day.day, entry_i, ex_i, entry_ref, ex_px, why, cfg))
                # random-entry baseline: same symbol/day, same stop/target distance and hold, random time
                eligible = np.nonzero((mins >= cfg.no_entries_first_minutes) & (mins < last_entry_i))[0]
                eligible = eligible[eligible < n - 2]
                for _ in range(cfg.random_baseline_draws if len(eligible) else 0):
                    ri = int(eligible[rng.randrange(len(eligible))]) + 1
                    rref = fv["open"].a[ri]
                    rs = rref * (1 - max(stop_pct, cfg.min_stop_pct) / 100)
                    rt = rref * (1 + (idea.target_price / ref - 1))
                    rx_i, rx_px, rwhy = _simulate_exit(fv, strat, ri, rref, rs, rt, idea.max_hold_minutes, fl_i, cfg,
                                                       use_strategy_exit=False)
                    out.append(_fill_trade(sid, sym, day.day, ri, rx_i, rref, rx_px, rwhy, cfg, rb=True))
                busy_until = ex_i
    return out


# ------------------------------------------------------------------ statistics

def bootstrap_p_value(returns: list[float], draws: int = 2000, seed: int = 3) -> float:
    """P(mean <= 0) under resampling. Small values = evidence the average trade is positive."""
    if len(returns) < 5:
        return 1.0
    rng = np.random.default_rng(seed)
    arr = np.asarray(returns, dtype=float)
    idx = rng.integers(0, len(arr), size=(draws, len(arr)))
    means = arr[idx].mean(axis=1)
    return float((means <= 0).mean())


def t_stat(returns: list[float]) -> float:
    n = len(returns)
    if n < 3:
        return 0.0
    arr = np.asarray(returns, dtype=float)
    sd = arr.std(ddof=1)
    return float(arr.mean() / (sd / math.sqrt(n))) if sd > 0 else 0.0


def summarize(trades: list[BTTrade]) -> dict:
    rets = [t.ret_pct for t in trades]
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gl = -sum(losses)
    eq = np.cumsum(pnls) if pnls else np.array([0.0])
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
    dd = float((peak[1:] - eq).max()) if len(eq) else 0.0
    return {
        "trades": len(trades),
        "winRate": len(wins) / len(trades) if trades else 0.0,
        "avgRetPct": float(np.mean(rets)) if rets else 0.0,
        "totalPnl": float(sum(pnls)),
        "profitFactor": float(sum(wins) / gl) if gl > 0 else (999.0 if wins else 0.0),
        "avgWin": float(np.mean(wins)) if wins else 0.0,
        "avgLoss": float(np.mean(losses)) if losses else 0.0,
        "maxDrawdown": dd,
        "tStat": t_stat(rets),
        "pValue": bootstrap_p_value(rets),
    }


@dataclass
class Verdict:
    verdict: str  # evidence | no_edge | insufficient
    explanation: str


def judge(in_sample: dict, out_sample: dict, oos_random: dict, min_oos_trades: int = 30) -> Verdict:
    if out_sample["trades"] < min_oos_trades:
        return Verdict("insufficient", f"only {out_sample['trades']} out-of-sample trades (need {min_oos_trades}) — "
                                       "not enough to tell luck from skill")
    checks = [
        (in_sample["avgRetPct"] > 0, "lost money on the training period"),
        (out_sample["avgRetPct"] > 0, "lost money on the unseen test period after costs"),
        (out_sample["profitFactor"] >= 1.15, f"test-period profit factor {out_sample['profitFactor']:.2f} < 1.15"),
        (out_sample["pValue"] < 0.10, f"test-period results could easily be luck (p={out_sample['pValue']:.2f})"),
        (out_sample["avgRetPct"] > oos_random["avgRetPct"],
         "did not beat random entries with the same stops and targets"),
    ]
    failed = [msg for ok, msg in checks if not ok]
    if failed:
        return Verdict("no_edge", "; ".join(failed))
    return Verdict("evidence", f"positive after costs on unseen data ({out_sample['avgRetPct']:.3f}% per trade, "
                               f"p={out_sample['pValue']:.2f}) and better than random entries")


def run_backtest(days: list[DayInput], strategies: list[str], cfg: BTConfig, oos_fraction: float = 0.4,
                 progress: Optional[Callable[[float, str], None]] = None) -> dict:
    rng = random.Random(cfg.seed)
    days = sorted(days, key=lambda d: d.day)
    split = max(1, int(len(days) * (1 - oos_fraction)))
    all_trades: list[BTTrade] = []
    for k, d in enumerate(days):
        all_trades.extend(run_day(d, strategies, cfg, rng))
        if progress:
            progress((k + 1) / len(days), f"simulated {d.day.isoformat()}")
    split_day = days[split].day if split < len(days) else days[-1].day
    results = {}
    for sid in strategies:
        real = [t for t in all_trades if t.strategy == sid and not t.random_baseline]
        rand = [t for t in all_trades if t.strategy == sid and t.random_baseline]
        is_t = [t for t in real if t.day < split_day]
        oos_t = [t for t in real if t.day >= split_day]
        oos_r = [t for t in rand if t.day >= split_day]
        s_is, s_oos, s_rand = summarize(is_t), summarize(oos_t), summarize(oos_r)
        v = judge(s_is, s_oos, s_rand)
        curve = list(np.round(np.cumsum([t.pnl for t in real]), 4)) if real else []
        results[sid] = {"name": ALL_STRATEGIES[sid].name, "inSample": s_is, "outOfSample": s_oos,
                        "randomBaseline": s_rand, "all": summarize(real), "verdict": v.verdict,
                        "explanation": v.explanation, "curve": [float(x) for x in curve[-400:]]}
    return {"days": len(days), "firstDay": days[0].day.isoformat() if days else "",
            "lastDay": days[-1].day.isoformat() if days else "", "splitDay": split_day.isoformat() if days else "",
            "strategies": results, "config": {"notional": cfg.notional, "spreadBps": cfg.spread_bps,
                                              "slippageBps": cfg.costs.slippage_bps}}
