"""Phase 2 research: opportunities with better economics (research/PREREGISTRATION-2026-11-07-phase2.md).

Every rule here is the pre-registered one; parameters are constants, never fitted. Simulation rules are
the backtester's pessimistic ones: decide at a bar's close, act at the next bar's open, stop first inside
a bar, gaps fill at the open, positions closed at the 15:50 cutoff.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

import numpy as np
import pandas as pd

from ..analysis.indicators import expected_volume_fraction
from ..backtest.engine import BTConfig, BTTrade, DayInput, FrameView
from ..quant.features import asof
from ..risk.fees import CostModel
from ..strategies import ALL_STRATEGIES, Context
from .dataset import DayFrames, build_frames

HORIZONS = (30, 60, 120, 180)
THRESHOLDS = (0.5, 1.0, 2.0)
FLATTEN_MIN = 380                     # 15:50 ET (minutes after the open)
H1_STRATS = ("momentum", "volume_breakout")


# ------------------------------------------------------------------ costs
def cfg_standard() -> BTConfig:
    return BTConfig()


def cfg_wide() -> BTConfig:
    """H2: opening-minute gap stocks — 10 bps quoted spread, 5 bps slippage per side."""
    return BTConfig(spread_bps=10.0)


def net_pct(day: date, buy_px: float, sell_ref: float, cfg: BTConfig, slip_mult: float = 1.0,
            spread_bps: Optional[float] = None, buy_is_filled_price: bool = False) -> tuple[float, tuple]:
    """(net % of a $20 position, (sec, taf, cat) fee accruals). buy_is_filled_price: the buy price already
    includes costs (passive limit fills); otherwise half the spread + slippage are added to the reference."""
    c = cfg.costs_on(day).with_slippage(cfg.costs.slippage_bps * slip_mult)
    sp = cfg.spread_bps if spread_bps is None else spread_bps
    buy = buy_px if buy_is_filled_price else c.fill_price("buy", buy_px, sp)
    sell = c.fill_price("sell", sell_ref, sp)
    qty = cfg.notional / buy
    fees = c.sell_fees(qty * sell, qty) + c.buy_fees(qty)
    acc = c.sale_breakdown(qty * sell, qty) + c.buy_breakdown(qty)
    pnl = qty * (sell - buy) - fees
    return pnl / cfg.notional * 100, (acc.sec, acc.taf, acc.cat)


# ------------------------------------------------------------------ the hold-to-close simulator
@dataclass
class Path:
    exit_i: int
    exit_px: float
    reason: str
    mfe: float                          # best % move above entry before the exit (bars where the stop didn't hit)
    mae: float                          # worst % move below entry
    hit: dict = field(default_factory=dict)        # threshold % -> reached before the stop
    at: dict = field(default_factory=dict)         # horizon -> (exit_i, ref price, reason) with the same stop


def hold(fv: FrameView, mins: np.ndarray, entry_i: int, entry_px: float, stop: float,
         flatten_min: int = FLATTEN_MIN) -> Path:
    o, h, l = fv["open"].a, fv["high"].a, fv["low"].a
    n = len(mins)
    entry_min = mins[entry_i]
    mfe = mae = 0.0
    hit = {x: False for x in THRESHOLDS}
    at: dict = {}
    for j in range(entry_i, n):
        m = mins[j]
        # horizon exits (open of the first bar at/after entry + h), if not stopped before
        for hz in HORIZONS:
            if hz not in at and m - 1 >= entry_min - 1 + hz:
                at[hz] = (j, float(o[j]), "time")
        if m - 1 >= flatten_min:
            p = Path(j, float(o[j]), "close cutoff", mfe, mae, hit, at)
            for hz in HORIZONS:
                at.setdefault(hz, (j, float(o[j]), "close cutoff"))
            return p
        if o[j] <= stop:
            px, why = float(o[j]), "stop (gap)"
        elif l[j] <= stop:
            px, why = float(stop), "stop"
        else:
            px = None
        if px is not None:
            mae = min(mae, (px / entry_px - 1) * 100)
            for hz in HORIZONS:
                at.setdefault(hz, (j, px, why))
            return Path(j, px, why, mfe, mae, hit, at)
        up = (h[j] / entry_px - 1) * 100
        mfe = max(mfe, up)
        mae = min(mae, (l[j] / entry_px - 1) * 100)
        for x in THRESHOLDS:
            if up >= x:
                hit[x] = True
    j = n - 1
    for hz in HORIZONS:
        at.setdefault(hz, (j, float(fv["close"].a[j]), "data end"))
    return Path(j, float(fv["close"].a[j]), "data end", mfe, mae, hit, at)


@dataclass
class Cand:
    """One pre-registered entry and everything measured about it."""
    hyp: str
    day: date
    symbol: str
    signal_min: int
    entry_i: int
    entry_ref: float                    # reference entry price before costs
    stop: float
    score: float
    path: Path
    trade: BTTrade                      # the account-level trade (exit at stop or cutoff)
    feats: dict = field(default_factory=dict)
    filled: bool = True                 # H3: passive order filled?


def _bt(hyp, sym, day, entry_i, exit_i, ref_entry, ref_exit, reason, net, fee_acc, mins, score, stop_pct) -> BTTrade:
    t = BTTrade(hyp, sym, day, entry_i, exit_i, ref_entry, ref_exit, 20.0, net / 100 * 20.0, net, reason,
                ref_entry=ref_entry, ref_exit=ref_exit, fee_sec=fee_acc[0], fee_taf=fee_acc[1], fee_cat=fee_acc[2])
    t.minute, t.exit_minute = int(mins[entry_i] - 1), int(mins[min(exit_i, len(mins) - 1)] - 1)
    t.score, t.stop_pct = score, stop_pct
    return t


# ------------------------------------------------------------------ P2-H1 (and its random baseline)
def h1_stop(price: float, atr: float) -> float:
    s = min(0.03, max(0.01, 3 * atr / price if price > 0 and math.isfinite(atr) else 0.01))
    return price * (1 - s)


def h1_candidates(df: DayFrames, cfg: BTConfig) -> list[Cand]:
    out = []
    for sym, sf in df.syms.items():
        fv, mins = sf.fv, sf.mins
        close, vwap = fv["close"].a, fv["vwap_s"].a
        first = None
        for i in range(len(mins) - 1):
            mi = mins[i]
            if mi < 30 or mi > 270:       # signal bar between 10:00 and 14:00 ET
                continue
            if not close[i] > vwap[i]:
                continue
            trend = None
            spy = df.day.spy_trend
            if spy is not None and 0 <= int(mi) - 1 < len(spy):
                trend = bool(spy[int(mi) - 1])
            best = None
            for sid in H1_STRATS:
                idea = ALL_STRATEGIES[sid].entry(fv, i, Context(sym, trend, df.day.session_minutes))
                if idea is not None and idea.score >= cfg.min_signal_score:
                    if best is None or idea.score > best[1]:
                        best = (sid, idea.score)
            if best is not None:
                first = (i, best)
                break
        if first is None:
            continue
        i, (sid, score) = first
        c = _h1_from_signal(df, sym, i, score, cfg)
        if c is not None:
            c.feats["strategy"] = sid
            out.append(c)
    return out


def _h1_from_signal(df: DayFrames, sym: str, i: int, score: float, cfg: BTConfig, hyp: str = "P2-H1") -> Optional[Cand]:
    sf = df.syms[sym]
    fv, mins = sf.fv, sf.mins
    if i + 1 >= len(mins) or mins[i + 1] - mins[i] > 3:
        return None
    e = i + 1
    ref = float(fv["open"].a[e])
    stop = h1_stop(float(fv["close"].a[i]), float(fv["atr"].a[i]))
    stop = min(stop, ref * 0.999)
    p = hold(fv, mins, e, ref, stop)
    net, fee = net_pct(df.day.day, ref, p.exit_px, cfg)
    t = _bt(hyp, sym, df.day.day, e, p.exit_i, ref, p.exit_px, p.reason, net, fee, mins, score, (1 - stop / ref) * 100)
    feats = {"rvol": float(fv["rvol"].a[i]), "spy_above_vwap": None, "minute": int(mins[i])}
    spy = df.spy_row(mins[i])
    if spy:
        feats["spy_above_vwap"] = bool(spy.get("close", 0) > spy.get("vwap_s", 1e18))
    return Cand(hyp, df.day.day, sym, int(mins[i]), e, ref, stop, score, p, t, feats)


def h1_random(df: DayFrames, cands: list[Cand], cfg: BTConfig, rng: random.Random) -> list[Cand]:
    """Baseline: the same symbol-days, a random signal minute in 10:00-14:00, the same exit rule."""
    out = []
    for c in cands:
        sf = df.syms[c.symbol]
        idx = [i for i in range(len(sf.mins) - 1) if 30 <= sf.mins[i] <= 270]
        if not idx:
            continue
        r = _h1_from_signal(df, c.symbol, idx[rng.randrange(len(idx))], 0.0, cfg, hyp="H1-random")
        if r is not None:
            out.append(r)
    return out


# ------------------------------------------------------------------ P2-H3 (passive entry for H1)
def h3_from(df: DayFrames, c: Cand, cfg: BTConfig) -> Cand:
    sf = df.syms[c.symbol]
    fv, mins = sf.fv, sf.mins
    i = c.entry_i - 1
    limit = float(fv["close"].a[i]) * (1 - 5 / 10_000)
    lo = fv["low"].a
    j = c.entry_i
    while j < len(mins) and mins[j] <= mins[i] + 10:
        if lo[j] <= limit * (1 - 1 / 10_000):
            stop = min(c.stop, limit * 0.999)
            if lo[j] <= stop:  # filled and stopped in the same bar: assume the worst order
                p = Path(j, float(stop), "stop (fill bar)", 0.0, (stop / limit - 1) * 100,
                         {x: False for x in THRESHOLDS}, {hz: (j, float(stop), "stop") for hz in HORIZONS})
            else:
                p = hold(fv, mins, j + 1, limit, stop) if j + 1 < len(mins) else hold(fv, mins, j, limit, stop)
            net, fee = net_pct(df.day.day, limit, p.exit_px, cfg, buy_is_filled_price=True)
            t = _bt("P2-H3", c.symbol, df.day.day, j, p.exit_i, limit, p.exit_px, p.reason, net, fee, mins, c.score,
                    (1 - stop / limit) * 100)
            return Cand("P2-H3", df.day.day, c.symbol, c.signal_min, j, limit, stop, c.score, p, t, dict(c.feats), True)
        j += 1
    return Cand("P2-H3", df.day.day, c.symbol, c.signal_min, c.entry_i, limit, c.stop, c.score, c.path, c.trade,
                dict(c.feats), False)


# ------------------------------------------------------------------ P2-H2 (stocks in play ORB)
@dataclass
class GapContext:
    """Per-day information known at 9:30: yesterday's close, 14-day average volume, liquidity rank."""
    prev_close: float
    avg_vol: float
    dollar_vol: float
    liquid: bool


def gap_context(daily: dict[str, pd.DataFrame], day: date) -> dict[str, GapContext]:
    rows = {}
    for s, d in daily.items():
        if d.empty:
            continue
        past = d[d.index < day].tail(14)
        if len(past) < 14 or day not in d.index:
            continue
        rows[s] = (float(past["close"].iat[-1]), float(past["volume"].mean()),
                   float((past["volume"] * past["close"]).mean()))
    if not rows:
        return {}
    med = float(np.median([r[2] for r in rows.values()]))
    return {s: GapContext(pc, av, dv, dv >= med) for s, (pc, av, dv) in rows.items()}


def h2_candidates(day: date, minute_bars: dict[str, pd.DataFrame], daily: dict[str, pd.DataFrame],
                  ctx: dict[str, GapContext], session_open: pd.Timestamp, cfg: BTConfig) -> tuple[list[Cand], list[Cand]]:
    """(ORB trades, baseline trades bought at 9:35 without the breakout)."""
    f5 = expected_volume_fraction(5, 390)
    ranked = []
    for s, bars in minute_bars.items():
        g = ctx.get(s)
        d = daily.get(s)
        if g is None or d is None or day not in d.index or bars is None or len(bars) < 30:
            continue
        today_open = float(d.loc[day, "open"])
        if g.prev_close < 5 or not g.liquid or today_open / g.prev_close - 1 < 0.03:
            continue
        mins = ((bars.index - session_open).total_seconds() / 60.0).to_numpy() + 1.0
        first5 = bars[mins <= 5]
        if len(first5) < 3:
            continue
        o1, c5 = float(first5["open"].iat[0]), float(first5["close"].iat[-1])
        orh, orl = float(first5["high"].max()), float(first5["low"].min())
        rvol5 = float(first5["volume"].sum()) / max(1.0, g.avg_vol * f5)
        if not (c5 > o1 and rvol5 >= 2.0) or (orh - orl) / orh > 0.04:
            continue
        ranked.append((rvol5, s, bars, mins, orh, orl, today_open / g.prev_close - 1))
    ranked.sort(key=lambda r: r[0], reverse=True)
    trades, base = [], []
    for rvol5, s, bars, mins, orh, orl, gap in ranked[:2]:
        fv = FrameView(bars)
        o, h = fv["open"].a, fv["high"].a
        feats = {"rvol5": rvol5, "gap": gap * 100, "or_range": (orh - orl) / orh * 100}
        # baseline: buy at the open of the first bar after 9:35
        k = int(np.searchsorted(mins, 6.0))
        if k < len(mins):
            p = hold(fv, mins, k, float(o[k]), orl)
            net, fee = net_pct(day, float(o[k]), p.exit_px, cfg)
            base.append(Cand("H2-at-9:35", day, s, 5, k, float(o[k]), orl, rvol5, p,
                             _bt("H2-at-9:35", s, day, k, p.exit_i, float(o[k]), p.exit_px, p.reason, net, fee, mins, rvol5,
                                 (1 - orl / o[k]) * 100), feats))
        # the ORB: buy-stop at the OR high between 9:35 and 11:00
        for j in range(k, len(mins)):
            if mins[j] - 1 >= 90:
                break
            if h[j] >= orh * (1 + 1 / 10_000):
                ref = max(orh, float(o[j]))
                if ref <= orl:
                    break
                p = hold(fv, mins, j, ref, orl)
                net, fee = net_pct(day, ref, p.exit_px, cfg)
                trades.append(Cand("P2-H2", day, s, int(mins[j]), j, ref, orl, rvol5, p,
                                   _bt("P2-H2", s, day, j, p.exit_i, ref, p.exit_px, p.reason, net, fee, mins, rvol5,
                                       (1 - orl / ref) * 100), feats))
                break
    return trades, base


# ------------------------------------------------------------------ P2-H4 / P2-H5 (daily, research only)
def h4_overnight(daily: dict[str, pd.DataFrame], days: list[date], cfg: BTConfig, tight: bool = False) -> list[dict]:
    out = []
    for s in ("SPY", "QQQ", "IWM", "DIA"):
        d = daily.get(s)
        if d is None:
            continue
        d = d[[x in set(days) for x in d.index]]
        idx = list(d.index)
        for a, b in zip(idx[:-1], idx[1:]):
            buy, sell = float(d.loc[a, "close"]), float(d.loc[b, "open"])
            if tight:
                c2 = BTConfig(spread_bps=1.0, costs=CostModel(slippage_bps=1.0))
                net, _ = net_pct(b, buy, sell, c2)
            else:
                net, _ = net_pct(b, buy, sell, cfg)
            out.append({"day": b, "symbol": s, "gross": (sell / buy - 1) * 100, "net": net})
    return out


def h5_trend(daily: dict[str, pd.DataFrame], days: list[date], cfg: BTConfig, sma: int = 100) -> dict:
    """Daily % returns (of the position's capital) for the trend rule and for buy-and-hold, on `days`."""
    d = daily["SPY"].sort_index()
    closes = d["close"]
    ma = closes.rolling(sma, min_periods=sma).mean()
    eval_days = [x for x in d.index if x in set(days)]
    strat, bh, switches = [], [], 0
    held = False
    first = True
    c = cfg.costs_on(eval_days[0]) if eval_days else cfg.costs
    half = (cfg.spread_bps / 2 + c.slippage_bps) / 100     # % cost of one side
    for k, day in enumerate(eval_days):
        pos = d.index.get_loc(day)
        if pos == 0 or not math.isfinite(ma.iloc[pos - 1]):
            continue
        prev_close, o, cl = float(closes.iloc[pos - 1]), float(d["open"].iloc[pos]), float(closes.iloc[pos])
        want = prev_close > float(ma.iloc[pos - 1])       # decided at yesterday's close, acted on at today's open
        r = 0.0
        if held and want:
            r = (cl / prev_close - 1) * 100
        elif held and not want:                            # sell at the open
            r = (o / prev_close - 1) * 100 - half
            switches += 1
        elif not held and want:                            # buy at the open
            r = (cl / o - 1) * 100 - half
            switches += 1
        held = want
        strat.append((day, r))
        bh.append((day, (cl / prev_close - 1) * 100 - (half if first else 0.0)))
        first = False
    return {"strategy": strat, "buyhold": bh, "switches": switches}


def daily_stats(series: list[tuple[date, float]]) -> dict:
    r = np.array([v for _, v in series]) if series else np.array([0.0])
    eq = np.cumprod(1 + r / 100)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))
    dd = float(((peak[1:] - eq) / peak[1:]).max() * 100) if len(eq) else 0.0
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    return {"days": len(series), "meanDailyPct": float(r.mean()), "totalPct": float((eq[-1] - 1) * 100),
            "sharpe": float(r.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0, "maxDrawdownPct": dd}


# ------------------------------------------------------------------ opportunity profile
def profile(cands: list[Cand], cfg: BTConfig) -> dict:
    """What the opportunity looks like before and after costs (the 'is there anything to capture' view)."""
    if not cands:
        return {"n": 0}
    gross = np.array([c.trade.gross_ret_pct for c in cands])
    net = np.array([c.trade.ret_pct for c in cands])
    mfe = np.array([c.path.mfe for c in cands])
    mae = np.array([c.path.mae for c in cands])
    cost = float(np.mean(gross - net))
    hz = {}
    for h in HORIZONS:
        vals = []
        for c in cands:
            j, px, _ = c.path.at[h]
            n_, _ = net_pct(c.day, c.entry_ref, px, cfg, buy_is_filled_price=c.hyp == "P2-H3")
            vals.append(n_)
        hz[str(h)] = float(np.mean(vals))
    hz["close"] = float(net.mean())
    capt = [c.trade.gross_ret_pct / c.path.mfe for c in cands if c.path.mfe > 0.05]
    reasons: dict[str, list[float]] = {}
    for c in cands:
        reasons.setdefault(c.trade.reason.split(" (")[0], []).append(c.trade.ret_pct)
    return {"n": len(cands), "grossPct": float(gross.mean()), "netPct": float(net.mean()), "costPct": cost,
            "breakEvenGrossPct": cost, "avgMfePct": float(mfe.mean()), "avgMaePct": float(mae.mean()),
            "medianMfePct": float(np.median(mfe)), "reach": {f"+{x}%": float(np.mean([c.path.hit[x] for c in cands]))
                                                             for x in THRESHOLDS},
            "captureRatio": float(np.median(capt)) if capt else None, "byHorizonNetPct": hz,
            "winRate": float((net > 0).mean()), "byExit": {k: {"n": len(v), "netPct": float(np.mean(v))}
                                                            for k, v in sorted(reasons.items())},
            "avgHoldMin": float(np.mean([c.trade.exit_minute - c.trade.minute for c in cands]))}
