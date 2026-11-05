"""Technical indicators. All functions are causal: output[i] depends only on input[:i+1]."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..clock import ET


def ema(x: pd.Series, span: int) -> pd.Series:
    return x.ewm(span=span, adjust=False, min_periods=1).mean()


def sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=1).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI (0..100). 50 while there is not enough history."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(~((avg_gain == 0.0) & (avg_loss == 0.0)), 50.0)
    return out.fillna(50.0)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.fillna(high - low)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return true_range(high, low, close).ewm(alpha=1.0 / n, adjust=False, min_periods=1).mean()


def session_dates(index: pd.DatetimeIndex) -> np.ndarray:
    return index.tz_convert(ET).date


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-weighted average price, reset at the start of each trading day."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    # Prefer each bar's own VWAP (Alpaca provides it); fall back to the typical price.
    px = df["vwap"].where(df["vwap"] > 0, tp) if "vwap" in df.columns else tp
    pv = px * df["volume"]
    keys = session_dates(df.index)
    cum_pv = pv.groupby(keys).cumsum()
    cum_v = df["volume"].groupby(keys).cumsum()
    return (cum_pv / cum_v.replace(0.0, np.nan)).fillna(tp)


def prior_high(high: pd.Series, n: int) -> pd.Series:
    """Highest high of the previous n bars (excluding the current bar)."""
    return high.shift(1).rolling(n, min_periods=max(2, n // 2)).max()


def pct_change(x: pd.Series, n: int) -> pd.Series:
    return (x / x.shift(n) - 1.0) * 100.0


def realized_vol_pct(close: pd.Series, n: int = 30) -> pd.Series:
    """Rolling std of 1-bar log returns, scaled to a 390-minute day, in percent."""
    lr = np.log(close).diff()
    return lr.rolling(n, min_periods=5).std() * np.sqrt(390.0) * 100.0


_PROFILE_CACHE: dict[int, np.ndarray] = {}


def expected_volume_fraction(minutes_elapsed: float, session_minutes: int = 390) -> float:
    """Share of a typical day's volume that has traded after `minutes_elapsed` (U-shaped profile)."""
    prof = _PROFILE_CACHE.get(session_minutes)
    if prof is None:
        x = np.arange(session_minutes, dtype=float)
        w = 1.0 + 2.6 * np.exp(-x / 22.0) + 1.6 * np.exp(-(session_minutes - 1 - x) / 28.0)
        prof = np.cumsum(w / w.sum())
        _PROFILE_CACHE[session_minutes] = prof
    k = int(max(1, min(session_minutes, round(minutes_elapsed))))
    return float(prof[k - 1])


def relative_volume(cum_volume: float, avg_daily_volume: float, minutes_elapsed: float,
                    session_minutes: int = 390) -> float:
    """Today's volume so far vs. what a typical day has traded by this time. 1.0 = normal."""
    if avg_daily_volume <= 0 or minutes_elapsed <= 0:
        return 0.0
    expected = avg_daily_volume * expected_volume_fraction(minutes_elapsed, session_minutes)
    return float(cum_volume / expected) if expected > 0 else 0.0
