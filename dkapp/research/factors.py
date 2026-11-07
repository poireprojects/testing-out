"""Point-in-time factors on daily price indices (rows = days, columns = assets).

Every value at row t uses prices up to and including t only. Forward returns start AFTER t, so pairing a
factor at t with `forward_returns(..., lag=1)` assumes the trade happens at the next close.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def momentum(prices: pd.DataFrame, lookback: int, skip: int = 0) -> pd.DataFrame:
    """Return from t-lookback to t-skip (e.g. 252/21 = the classic '12-1 month' momentum)."""
    return prices.shift(skip) / prices.shift(lookback) - 1.0


def volatility(returns: pd.DataFrame, window: int = 63) -> pd.DataFrame:
    return returns.rolling(window, min_periods=int(window * 0.8)).std() * np.sqrt(252)


def trend(prices: pd.DataFrame, window: int = 200) -> pd.DataFrame:
    """Distance from the moving average (above 0 = in an uptrend)."""
    return prices / prices.rolling(window, min_periods=window).mean() - 1.0


def breadth(prices: pd.DataFrame, window: int = 200) -> pd.Series:
    """Share of assets above their own moving average (market breadth)."""
    t = trend(prices, window)
    return (t > 0).sum(axis=1) / t.notna().sum(axis=1).replace(0, np.nan)


def forward_returns(prices: pd.DataFrame, horizon: int, lag: int = 0) -> pd.DataFrame:
    """Return from the close of t+lag to the close of t+lag+horizon (aligned to row t)."""
    return prices.shift(-(lag + horizon)) / prices.shift(-lag) - 1.0


def month_ends(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = pd.Series(idx, index=idx)
    return pd.DatetimeIndex(s.groupby([idx.year, idx.month]).max().values)


def week_ends(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = pd.Series(idx, index=idx)
    iso = idx.isocalendar()
    return pd.DatetimeIndex(s.groupby([iso.year.values, iso.week.values]).max().sort_values().values)


def rank_ic(factor: pd.DataFrame, fwd: pd.DataFrame, dates: pd.DatetimeIndex, min_assets: int = 5) -> pd.Series:
    """Cross-sectional Spearman correlation per date (the 'information coefficient')."""
    out = {}
    for d in dates:
        if d not in factor.index or d not in fwd.index:
            continue
        a, b = factor.loc[d], fwd.loc[d]
        ok = a.notna() & b.notna()
        if ok.sum() < min_assets:
            continue
        out[d] = float(a[ok].rank().corr(b[ok].rank()))
    return pd.Series(out, dtype=float)


def ic_summary(ic: pd.Series, horizon_days: int, sampling_days: int) -> dict:
    """Mean IC with a t-stat corrected for overlapping horizons (Newey-West style effective sample)."""
    ic = ic.dropna()
    n = len(ic)
    if n < 3:
        return {"n": n}
    overlap = max(1.0, horizon_days / max(1, sampling_days))
    sd = float(ic.std(ddof=1))
    t = float(ic.mean() / (sd / np.sqrt(n / overlap))) if sd > 0 else 0.0
    return {"n": n, "meanIC": float(ic.mean()), "icIR": float(ic.mean() / sd) if sd > 0 else 0.0, "tStat": t,
            "positiveShare": float((ic > 0).mean())}


def quantile_spread(factor: pd.DataFrame, fwd: pd.DataFrame, dates: pd.DatetimeIndex, q: int = 5) -> dict:
    """Mean forward return of the top and bottom quantile and of the equal-weight average (per period)."""
    top, bot, avg = [], [], []
    for d in dates:
        if d not in factor.index or d not in fwd.index:
            continue
        a, b = factor.loc[d], fwd.loc[d]
        ok = a.notna() & b.notna()
        if ok.sum() < q * 2:
            continue
        r = a[ok].rank(pct=True)
        top.append(float(b[ok][r > 1 - 1.0 / q].mean()))
        bot.append(float(b[ok][r <= 1.0 / q].mean()))
        avg.append(float(b[ok].mean()))
    if not top:
        return {"n": 0}
    top, bot, avg = np.array(top), np.array(bot), np.array(avg)
    return {"n": len(top), "topPct": float(top.mean() * 100), "bottomPct": float(bot.mean() * 100),
            "allPct": float(avg.mean() * 100), "topMinusAllPct": float((top - avg).mean() * 100),
            "topMinusBottomPct": float((top - bot).mean() * 100)}


def top_k_turnover(factor: pd.DataFrame, dates: pd.DatetimeIndex, k: int) -> float:
    """Average share of a top-k equal-weight portfolio replaced at each rebalance (one-way)."""
    prev, turns = None, []
    for d in dates:
        if d not in factor.index:
            continue
        row = factor.loc[d].dropna()
        if len(row) < k:
            continue
        cur = set(row.nlargest(k).index)
        if prev is not None:
            turns.append(len(cur - prev) / k)
        prev = cur
    return float(np.mean(turns)) if turns else float("nan")
