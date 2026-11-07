"""Point-in-time features for the statistical model — ONE definition shared by research and the live app.

Every input must be known at the close of the decision bar: the symbol's own feature-frame row (causal by
construction, see analysis/features.py), SPY's row at the same minute, and market breadth computed from
bars up to that minute. Correlated indicators are kept to a small set on purpose (no "five agreeing
indicators" that are really one signal).
"""
from __future__ import annotations

import math
from typing import Mapping, Optional

import numpy as np

FEATURE_VERSION = "f1"

# name -> how it is computed (shown in the UI and the report)
FEATURES: dict[str, str] = {
    "mom5": "return over the last 5 minutes (%)",
    "mom15": "return over the last 15 minutes (%)",
    "mom30": "return over the last 30 minutes (%)",
    "dist_vwap": "distance from the session VWAP (%)",
    "rsi": "RSI(14) on 1-minute closes",
    "rvol": "volume so far vs a normal day at this time (×)",
    "vol_ratio": "last bar's volume vs the previous 20 bars (×)",
    "rv30": "30-minute realized volatility (daily %)",
    "chg_open": "change since today's open (%)",
    "chg_prev": "change since yesterday's close (%)",
    "rel_spy": "change since open minus SPY's (%)",
    "spy_above_vwap": "SPY above its VWAP (1/0)",
    "spy_chg_open": "SPY change since open (%)",
    "breadth": "share of watched stocks up since their open (0-1)",
    "tod": "time of day (fraction of the session elapsed)",
}
STRATEGY_FEATURES = ["is_momentum", "is_trend", "is_orb", "is_vwap", "is_volume_breakout",
                     "stop_pct", "target_pct", "agreeing", "score"]


def _f(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return math.nan
    return v if math.isfinite(v) else math.nan


def feature_vector(row: Mapping, spy: Optional[Mapping], breadth: Optional[float], minute: float,
                   session_minutes: int = 390) -> list[float]:
    """row/spy: feature-frame values (keys as in analysis.features) for the decision bar."""
    spy = spy or {}
    chg_open = _f(row.get("chg_open_pct"))
    spy_chg = _f(spy.get("chg_open_pct"))
    spy_close, spy_vwap = _f(spy.get("close")), _f(spy.get("vwap_s"))
    above = (1.0 if spy_close > spy_vwap else 0.0) if math.isfinite(spy_close) and math.isfinite(spy_vwap) else math.nan
    return [_f(row.get("mom5")), _f(row.get("mom15")), _f(row.get("mom30")), _f(row.get("dist_vwap_pct")),
            _f(row.get("rsi")), _f(row.get("rvol")), _f(row.get("vol_ratio")), _f(row.get("rv30")), chg_open,
            _f(row.get("chg_prev_pct")), chg_open - spy_chg if math.isfinite(spy_chg) else math.nan, above, spy_chg,
            _f(breadth), float(minute) / max(1, session_minutes)]


def strategy_vector(strategy: str, stop_pct: float, target_pct: float, agreeing: int, score: float) -> list[float]:
    return [float(strategy == s) for s in ("momentum", "trend", "orb", "vwap", "volume_breakout")] + \
           [float(stop_pct), float(target_pct), float(agreeing), float(score)]


def row_at(fv, i: int) -> dict:
    """Plain dict of one bar's feature-frame values from a FrameView (research) — same keys live uses."""
    keys = ("close", "vwap_s", "mom5", "mom15", "mom30", "dist_vwap_pct", "rsi", "rvol", "vol_ratio", "rv30",
            "chg_open_pct", "chg_prev_pct")
    return {k: (float(fv[k].a[i]) if k in fv.cols else math.nan) for k in keys}


def asof(mins: np.ndarray, minute: float) -> int:
    """Index of the last bar completed at or before `minute` (-1 if none)."""
    return int(np.searchsorted(mins, minute, side="right")) - 1
