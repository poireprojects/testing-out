"""Per-bar feature frame used by every strategy (live and in backtests).

`feature_frame` is causal: the row for bar i only uses bars 0..i. A unit test checks
that truncating the input never changes earlier rows (no look-ahead bias).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from . import indicators as ind

OPENING_RANGE_MINUTES = 15


@dataclass
class Baseline:
    """Per-symbol context from previous days (never today's data)."""
    avg_daily_volume: float = 0.0
    prev_close: float = 0.0
    avg_range_pct: float = 0.0


def feature_frame(bars: pd.DataFrame, session_open: datetime, baseline: Optional[Baseline] = None,
                  session_minutes: int = 390) -> pd.DataFrame:
    """bars: today's 1-minute bars (UTC index) for one symbol, starting at the open."""
    if bars is None or bars.empty:
        return pd.DataFrame()
    b = baseline or Baseline()
    f = bars.copy()
    c, h, l, v = f["close"], f["high"], f["low"], f["volume"]
    mins = ((f.index - pd.Timestamp(session_open).tz_convert("UTC")).total_seconds() / 60.0).astype(float)
    f["minute"] = np.asarray(mins) + 1.0  # minutes elapsed once this bar completes
    f["ema9"] = ind.ema(c, 9)
    f["ema20"] = ind.ema(c, 20)
    f["ema50"] = ind.ema(c, 50)
    f["rsi"] = ind.rsi(c, 14)
    f["atr"] = ind.atr(h, l, c, 14)
    f["vwap_s"] = ind.session_vwap(f)
    f["mom5"] = ind.pct_change(c, 5)
    f["mom15"] = ind.pct_change(c, 15)
    f["mom30"] = ind.pct_change(c, 30)
    f["vol_avg20"] = v.shift(1).rolling(20, min_periods=5).mean()
    f["vol_ratio"] = (v / f["vol_avg20"]).replace([np.inf, -np.inf], np.nan)
    f["hh30"] = ind.prior_high(h, 30)
    f["cum_vol"] = v.cumsum()
    f["rvol"] = [ind.relative_volume(cv, b.avg_daily_volume, m, session_minutes)
                 for cv, m in zip(f["cum_vol"].to_numpy(), f["minute"].to_numpy())]
    f["dist_vwap_pct"] = (c / f["vwap_s"] - 1.0) * 100.0
    f["rv30"] = ind.realized_vol_pct(c, 30)
    day_open = float(f["open"].iloc[0])
    f["chg_open_pct"] = (c / day_open - 1.0) * 100.0
    f["chg_prev_pct"] = (c / b.prev_close - 1.0) * 100.0 if b.prev_close > 0 else np.nan
    # Opening range: only known once its window has completed.
    in_or = f["minute"] <= OPENING_RANGE_MINUTES
    or_high = h.where(in_or).cummax()
    or_low = l.where(in_or).cummin()
    or_done = f["minute"] >= OPENING_RANGE_MINUTES
    f["or_high"] = or_high.ffill().where(or_done)
    f["or_low"] = or_low.ffill().where(or_done)
    return f


def latest_features(f: pd.DataFrame) -> dict:
    """Plain dict of the newest bar's features for display and for the AI prompt."""
    if f is None or f.empty:
        return {}
    r = f.iloc[-1]

    def g(k, nd=2):
        v = r.get(k)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return None if not np.isfinite(v) else round(v, nd)

    return {
        "price": g("close", 4),
        "minute_of_session": g("minute", 0),
        "change_from_open_pct": g("chg_open_pct"),
        "change_from_prev_close_pct": g("chg_prev_pct"),
        "vwap": g("vwap_s", 4),
        "distance_from_vwap_pct": g("dist_vwap_pct"),
        "rsi14": g("rsi", 1),
        "ema9": g("ema9", 4),
        "ema20": g("ema20", 4),
        "ema50": g("ema50", 4),
        "atr14": g("atr", 4),
        "momentum_5m_pct": g("mom5"),
        "momentum_15m_pct": g("mom15"),
        "momentum_30m_pct": g("mom30"),
        "relative_volume": g("rvol"),
        "last_bar_volume_vs_avg": g("vol_ratio"),
        "opening_range_high": g("or_high", 4),
        "opening_range_low": g("or_low", 4),
        "prior_30m_high": g("hh30", 4),
        "volatility_daily_pct": g("rv30"),
    }
