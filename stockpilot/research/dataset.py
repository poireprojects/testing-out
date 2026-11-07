"""Research datasets built from cached days: point-in-time features, labels, pre-registered rules.

Labels are trades simulated with the backtester's own pessimistic fill rules (`_simulate_exit` /
`_fill_trade`): buy at the open of the bar after the decision, stop-first inside a bar, gaps fill at
the open, flat before the close, costs from the dated fee schedule. Features come only from bars that
had completed at the decision time.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

import numpy as np

from ..analysis.features import feature_frame
from ..backtest.engine import BTConfig, BTTrade, DayInput, FrameView, _fill_trade, _simulate_exit
from ..quant.features import asof, feature_vector, row_at, strategy_vector

INDEX_ETFS = {"SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "SMH"}


@dataclass
class SymFrame:
    f: object
    fv: FrameView
    mins: np.ndarray          # minutes elapsed when each bar completed (bar i completes at mins[i])


@dataclass
class DayFrames:
    day: DayInput
    syms: dict[str, SymFrame]
    breadth: np.ndarray       # per session minute: share of symbols above their open (as of that minute)
    spy: Optional[SymFrame] = None

    @property
    def flatten_minute(self) -> int:
        return self.day.session_minutes - 10

    def spy_row(self, minute: float) -> dict:
        if self.spy is None:
            return {}
        j = asof(self.spy.mins, minute)
        return row_at(self.spy.fv, j) if j >= 0 else {}

    def features(self, sym: str, i: int) -> list[float]:
        sf = self.syms[sym]
        m = float(sf.mins[i])
        b = self.breadth[int(min(max(m, 0), len(self.breadth) - 1))]
        return feature_vector(row_at(sf.fv, i), self.spy_row(m), b, m, self.day.session_minutes)


def build_frames(day: DayInput) -> DayFrames:
    syms = {}
    for sym, bars in day.bars.items():
        if bars is None or len(bars) < 40:
            continue
        f = feature_frame(bars, day.session_open.to_pydatetime(), day.baselines.get(sym), day.session_minutes)
        fv = FrameView(f)
        syms[sym] = SymFrame(None, fv, fv["minute"].a.copy())  # numpy view only; the DataFrame is dropped
    m = day.session_minutes
    up = np.zeros(m + 1)
    cnt = np.zeros(m + 1)
    for sf in syms.values():
        o = sf.fv["open"].a[0]
        c = sf.fv["close"].a
        idx = np.searchsorted(sf.mins, np.arange(m + 1), side="right") - 1
        ok = idx >= 0
        up[ok] += (c[idx[ok]] > o)
        cnt[ok] += 1
    breadth = np.where(cnt > 0, up / np.maximum(cnt, 1), np.nan)
    return DayFrames(day, syms, breadth, syms.get("SPY"))


def _trade(df: DayFrames, sym: str, label: str, i: int, stop_frac: float, cfg: BTConfig, max_hold: int,
           exit_rule: Optional[Callable] = None, target_frac: Optional[float] = None) -> Optional[BTTrade]:
    """Decide at the close of bar i, buy at the next bar's open, simulate with the backtester's rules."""
    sf = df.syms[sym]
    n = len(sf.mins)
    if i + 1 >= n:
        return None
    entry_i = i + 1
    if sf.mins[entry_i] - sf.mins[i] > 3:  # thin IEX data: the next bar is too far away
        return None
    fl_i = int(np.searchsorted(sf.mins, df.flatten_minute))
    if entry_i >= fl_i:
        return None
    ref = float(sf.fv["open"].a[entry_i])
    stop = ref * (1 - stop_frac)
    target = ref * (1 + target_frac) if target_frac else ref * 1e6

    class _Rule:  # adapts an exit function to the backtester's strategy interface
        def exit(self, fv, j, entry_px, entry_index):
            return exit_rule(fv, j) if exit_rule else None
    ex_i, ex_px, why = _simulate_exit(sf.fv, _Rule(), entry_i, ref, stop, target, max_hold, fl_i, cfg,
                                      use_strategy_exit=exit_rule is not None)
    t = _fill_trade(label, sym, df.day.day, entry_i, ex_i, ref, ex_px, why, cfg)
    t.minute, t.exit_minute = int(sf.mins[entry_i] - 1), int(sf.mins[min(ex_i, n - 1)] - 1)
    t.signal_px = float(sf.fv["close"].a[i])
    t.stop_pct = stop_frac * 100
    return t


# ------------------------------------------------------------------ H4: dense sample
DENSE_MINUTES = tuple(range(30, 331, 30))   # 10:00 ... 15:00 ET (minute = elapsed when the bar completes)
DENSE_HOLD = 28                              # exits before the next decision time


@dataclass
class Sample:
    day: date
    symbol: str
    minute: int
    x: list[float]
    trade: BTTrade
    extra: dict = field(default_factory=dict)


def dense_samples(df: DayFrames, cfg: BTConfig) -> list[Sample]:
    out = []
    for sym, sf in df.syms.items():
        atr, close = sf.fv["atr"].a, sf.fv["close"].a
        for M in DENSE_MINUTES:
            i = asof(sf.mins, M)
            if i < 0 or M - sf.mins[i] > 2:
                continue
            stop_frac = max(1.5 * atr[i] / close[i], 0.004) if close[i] > 0 and math.isfinite(atr[i]) else 0.004
            t = _trade(df, sym, "dense", i, min(stop_frac, 0.03), cfg, DENSE_HOLD)
            if t is not None:
                out.append(Sample(df.day.day, sym, M, df.features(sym, i), t,
                                  {"mom30": float(sf.fv["mom30"].a[i])}))
    return out


# ------------------------------------------------------------------ H5: existing strategy signals
def strategy_samples(df: DayFrames, trades: list[BTTrade]) -> list[Sample]:
    out = []
    for t in trades:
        if t.random_baseline or t.day != df.day.day or t.symbol not in df.syms:
            continue
        i = t.entry_i - 1
        x = df.features(t.symbol, i) + strategy_vector(t.strategy, t.stop_pct, t.target_pct, t.agreeing, t.score)
        out.append(Sample(t.day, t.symbol, int(df.syms[t.symbol].mins[i]), x, t))
    return out


# ------------------------------------------------------------------ H1-H3: pre-registered rules
def h1_mean_reversion(df: DayFrames, cfg: BTConfig) -> list[BTTrade]:
    out = []
    for sym, sf in df.syms.items():
        a = sf.fv
        mins = sf.mins
        busy = -1

        def exit_rule(fv, j):
            return "back above EMA20" if fv["close"].a[j] > fv["ema20"].a[j] else None
        for i in range(len(mins) - 1):
            M = mins[i]
            if i <= busy or M < 30 or M > 330:
                continue
            c, vw, m30, rsi, atr = (a["close"].a[i], a["vwap_s"].a[i], a["mom30"].a[i], a["rsi"].a[i], a["atr"].a[i])
            if not all(math.isfinite(v) for v in (c, vw, m30, rsi, atr)):
                continue
            if not (c <= vw * 0.99 and m30 <= -1.0 and rsi <= 30):
                continue
            spy_chg = df.spy_row(M).get("chg_open_pct", math.nan)
            if not (math.isfinite(spy_chg) and spy_chg > -1.0):
                continue
            t = _trade(df, sym, "h1_mean_reversion", i, min(max(1.5 * atr / c, 0.004), 0.03), cfg, 45, exit_rule)
            if t is not None:
                t.score = float(-a["dist_vwap_pct"].a[i])
                out.append(t)
                busy = t.exit_i
    return out


def h2_relative_strength(df: DayFrames, cfg: BTConfig) -> list[BTTrade]:
    spy = df.spy_row(60)
    spy_chg = spy.get("chg_open_pct", math.nan)
    if not math.isfinite(spy_chg):
        return []
    ranked = []
    for sym, sf in df.syms.items():
        if sym in INDEX_ETFS:
            continue
        i = asof(sf.mins, 60)
        if i < 0 or 60 - sf.mins[i] > 2:
            continue
        c, vw, ch = sf.fv["close"].a[i], sf.fv["vwap_s"].a[i], sf.fv["chg_open_pct"].a[i]
        if math.isfinite(ch) and ch - spy_chg >= 1.0 and c > vw:
            ranked.append((ch - spy_chg, sym, i))
    out = []
    for ex, sym, i in sorted(ranked, reverse=True)[:2]:
        t = _trade(df, sym, "h2_relative_strength", i, 0.02, cfg, 10_000)
        if t is not None:
            t.score = float(ex)
            out.append(t)
    return out


def h3_market_intraday_momentum(df: DayFrames, cfg: BTConfig) -> list[BTTrade]:
    if df.spy is None:
        return []
    base = df.day.baselines.get("SPY")
    i30 = asof(df.spy.mins, 30)
    i360 = asof(df.spy.mins, 360)
    if base is None or not base.prev_close or i30 < 0 or i360 < 0:
        return []
    first = df.spy.fv["close"].a[i30] / base.prev_close - 1
    if first <= 0:
        return []
    t = _trade(df, "SPY", "h3_market_momentum", i360, 0.02, cfg, 10_000)
    if t is not None:
        t.score = float(first * 100)
    return [t] if t is not None else []


# ------------------------------------------------------------------ slot replay ($100 account, 2 slots)
def slot_replay(cands: list[BTTrade], cfg: BTConfig,
                select: Optional[Callable[[date, int, list[BTTrade], int], list[BTTrade]]] = None) -> list[BTTrade]:
    """Same capital rules as the live app: max positions, one position per symbol, cooldown after an exit.
    `select(day, minute, candidates, free)` picks; default = highest score first."""
    taken: list[BTTrade] = []
    by_day: dict[date, list[BTTrade]] = {}
    for t in cands:
        by_day.setdefault(t.day, []).append(t)
    for day, ts in sorted(by_day.items()):
        open_until: dict[str, int] = {}
        last_exit: dict[str, int] = {}
        for mi in sorted({t.minute for t in ts}):
            for s, e in list(open_until.items()):
                if e <= mi:
                    last_exit[s] = e
                    del open_until[s]
            free = cfg.max_positions - len(open_until)
            if free <= 0:
                continue
            here = [t for t in ts if t.minute == mi and t.symbol not in open_until
                    and mi - last_exit.get(t.symbol, -10_000) >= cfg.cooldown_minutes]
            if not here:
                continue
            picks = select(day, mi, here, free) if select else sorted(here, key=lambda t: t.score, reverse=True)
            for t in picks[:free]:
                taken.append(t)
                open_until[t.symbol] = max(t.exit_minute, mi + 1)
    return taken


def random_select(seed: int = 3):
    rng = random.Random(seed)

    def pick(day, minute, cands, free):
        c = list(cands)
        rng.shuffle(c)
        return c[:free]
    return pick
