"""Phase 3 rules, exactly as research/PREREGISTRATION-2026-11-07-phase3.md freezes them (research only).

P3-H1 industry relative strength: on the last trading day of each month, rank by 12-1 momentum (ties broken
by 6-1). Hold the top 3 equally weighted, bought at the NEXT trading day's close.
P3-H2 turn of the month: hold the market from the close of trading day -2 of a month to the close of
trading day +3 of the next month; cash otherwise.
"""
from __future__ import annotations

from itertools import combinations
from typing import Optional

import numpy as np
import pandas as pd

from .factors import momentum, month_ends

H1_VERSION = "p3-h1-industry-rs-v1 (12-1 momentum, top 3, monthly, 1-day lag)"
H2_VERSION = "p3-h2-turn-of-month-v1 (close of day -2 to close of day +3)"


# ------------------------------------------------------------------ P3-H1
def h1_scores(prices: pd.DataFrame, lookback: int = 252, skip: int = 21, tie_lookback: int = 126) -> pd.DataFrame:
    """12-1 momentum plus a tiny 6-1 term that only matters for exact ties."""
    return momentum(prices, lookback, skip) + 1e-9 * momentum(prices, tie_lookback, skip).fillna(0.0)


def h1_targets(prices: pd.DataFrame, top_k: int = 3, lookback: int = 252, skip: int = 21, lag: int = 1,
               first_decision: Optional[pd.Timestamp] = None) -> tuple[dict, list]:
    """Execution-day -> weights, plus the decision log (decision day, execution day, picks)."""
    sc = h1_scores(prices, lookback, skip)
    idx = prices.index
    pos = {d: i for i, d in enumerate(idx)}
    targets, log = {}, []
    for d in month_ends(idx):
        if first_decision is not None and d < first_decision:
            continue
        row = sc.loc[d].dropna()
        if len(row) < top_k or pos[d] + lag >= len(idx):
            continue
        picks = list(row.nlargest(top_k).index)
        ex = idx[pos[d] + lag]
        targets[ex] = pd.Series(1.0 / top_k, index=picks)
        log.append({"decision": d, "execution": ex, "picks": picks})
    return targets, log


def equal_weight_targets(prices: pd.DataFrame, exec_days) -> dict:
    out = {}
    for ex in exec_days:
        avail = prices.loc[ex].dropna().index
        out[ex] = pd.Series(1.0 / len(avail), index=avail)
    return out


def holding_returns(prices: pd.DataFrame, exec_days: list) -> pd.DataFrame:
    """Buy-and-hold return of each asset between consecutive execution days (for the random baseline)."""
    ex = list(exec_days)
    rows = {}
    for a, b in zip(ex[:-1], ex[1:]):
        rows[a] = prices.loc[b] / prices.loc[a] - 1.0
    return pd.DataFrame(rows).T


def random_topk_percentiles(prices: pd.DataFrame, log: list, top_k: int = 3) -> dict:
    """Where H1's picks rank among every possible top-k combination each month (gross, before costs)."""
    ex = [e["execution"] for e in log]
    hr = holding_returns(prices, ex)
    pct, h1_mean, all_mean = [], [], []
    for e in log[:-1]:
        r = hr.loc[e["execution"]].dropna()
        if len(r) < top_k:
            continue
        combo = np.array([r[list(c)].mean() for c in combinations(r.index, top_k)])
        mine = float(r[e["picks"]].mean())
        pct.append(float((combo < mine).mean()))
        h1_mean.append(mine)
        all_mean.append(float(combo.mean()))
    return {"months": len(pct), "avgPercentile": float(np.mean(pct)) if pct else None,
            "h1GrossMonthlyPct": float(np.mean(h1_mean) * 100) if pct else None,
            "randomGrossMonthlyPct": float(np.mean(all_mean) * 100) if pct else None}


# ------------------------------------------------------------------ P3-H2
def tom_windows(idx: pd.DatetimeIndex, enter: int = -2, exit_: int = 3) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """(entry close, exit close) per month, using only days that actually traded; windows that would run
    past the end of the data (or across a hole in it) are dropped."""
    s = pd.Series(range(len(idx)), index=idx)
    months = s.groupby([idx.year, idx.month])
    keys = list(months.groups.keys())
    out = []
    for k, nk in zip(keys[:-1], keys[1:]):
        cur, nxt = months.get_group(k), months.get_group(nk)
        if (nk[0] * 12 + nk[1]) - (k[0] * 12 + k[1]) != 1 or len(cur) < -enter or len(nxt) < exit_:
            continue
        a, b = cur.index[enter], nxt.index[exit_ - 1]
        if (b - a).days > 20:                     # a hole in the data (e.g. holdout or vault removed)
            continue
        out.append((a, b))
    return out


def h2_targets(idx: pd.DatetimeIndex, asset: str = "market") -> tuple[dict, list]:
    targets, wins = {}, tom_windows(idx)
    for a, b in wins:
        targets[a] = pd.Series({asset: 1.0})
        targets[b] = pd.Series({asset: 0.0})
    return targets, wins


def tom_day_flags(idx: pd.DatetimeIndex, windows: list) -> pd.Series:
    """True on the days whose return a TOM window earns (the day after entry through the exit day)."""
    f = pd.Series(False, index=idx)
    pos = {d: i for i, d in enumerate(idx)}
    for a, b in windows:
        f.iloc[pos[a] + 1: pos[b] + 1] = True
    return f


def window_returns(daily_ret: pd.Series, windows: list) -> pd.Series:
    """Compounded return of each holding window (entry close -> exit close), indexed by entry day."""
    idx = daily_ret.index
    pos = {d: i for i, d in enumerate(idx)}
    out = {}
    for a, b in windows:
        out[a] = float((1.0 + daily_ret.iloc[pos[a] + 1: pos[b] + 1]).prod() - 1.0)
    return pd.Series(out)


def random_window_baseline(excess: pd.Series, windows: list, draws: int = 1000, seed: int = 11) -> dict:
    """Same number of days per month held at random 4-day stretches of the same month (gross excess)."""
    rng = np.random.default_rng(seed)
    idx = excess.index
    by_month = pd.Series(range(len(idx)), index=idx).groupby([idx.year, idx.month])
    month_pos = [g.values for _, g in by_month]
    lens = [len(m) for m in month_pos]
    vals = excess.values
    means = []
    for _ in range(draws):
        tot = []
        for m, L in zip(month_pos, lens):
            if L < 5:
                continue
            st = rng.integers(0, L - 3)
            tot.append(np.prod(1 + vals[m[st: st + 4]]) - 1)
        means.append(np.mean(tot))
    means = np.array(means)
    return {"draws": draws, "meanWindowExcessPct": float(means.mean() * 100),
            "p95WindowExcessPct": float(np.quantile(means, 0.95) * 100)}


# ------------------------------------------------------------------ shared evaluation helpers
def decade_signs(monthly: pd.Series) -> dict:
    yrs = monthly.index.year
    dec = np.where(yrs < 1930, 1930, (yrs // 10) * 10)
    g = monthly.groupby(dec).mean()
    return {"byDecadePct": {f"{k}s": float(v * 100) for k, v in g.items()}, "positive": int((g > 0).sum()),
            "of": int(len(g))}


def block_signs(monthly: pd.Series, years: int = 3) -> dict:
    yrs = monthly.index.year
    start = yrs.min()
    blk = (yrs - start) // years
    g = monthly.groupby(blk).mean()
    labels = {int(k): f"{start + k * years}-{start + k * years + years - 1}" for k in g.index}
    return {"byBlockPct": {labels[int(k)]: float(v * 100) for k, v in g.items()}, "positive": int((g > 0).sum()),
            "of": int(len(g))}


def top_months_share(monthly_excess: pd.Series, k: int = 5) -> Optional[float]:
    tot = float(monthly_excess.sum())
    if tot <= 0:
        return None
    return float(monthly_excess.nlargest(k).sum() / tot)
