"""Phase 3 research review — DEVELOPMENT DATA ONLY (exploratory; no hypothesis is tested here).

    VENV-RESEARCH/bin/python scripts/research_phase3_review.py P3_DATA_DIR P2_DAILY_DIR OUT_DIR

P3_DATA_DIR: output of scripts/download_phase3.py (dev/ is read; holdA/ is never opened here).
P2_DAILY_DIR: Alpaca IEX daily bars from phase 2 (2022-11-01 .. 2026-11-05, examined) for the stock-level check.
Needs alphalens-reloaded (requirements-research.txt) for the independent IC cross-check.

Every factor examined here counts as a trial in the phase-3 multiple-testing budget (see review.json "trials").
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockpilot.research.academic import load_series, market_total_return, price_index, segments  # noqa: E402
from stockpilot.research.factors import (forward_returns, ic_summary, momentum, month_ends, quantile_spread,  # noqa: E402
                                    rank_ic, top_k_turnover, trend, volatility, week_ends, breadth)
from stockpilot.risk.fees import CostModel  # noqa: E402

SPREAD_BPS, SLIP_BPS = 3.0, 5.0          # the app's standard cost model (quoted spread, slippage per side)
PER_SIDE = (SPREAD_BPS / 2 + SLIP_BPS) / 1e4
TRIALS: list[str] = []


def seg_frames(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Development data has a hole (holdout A + vault); analyse each contiguous part on its own."""
    out = {}
    for a, b in segments(df.index):
        name = f"{a.year}-{b.year}"
        out[name] = df.loc[a:b]
    return out


# ------------------------------------------------------------------ A. holding period vs cost
def cost_economics() -> dict:
    cm = CostModel()
    rows = {}
    for cap in (100, 1_000, 10_000):
        # regulatory fees on a full rebalance (sell `cap`, buy `cap`), rounded up per fee type per day
        sh = cap / 100.0                                   # $100 shares as a round assumption
        sec = np.ceil(cap * 20.60e-6 * 100) / 100
        cat = np.ceil(2 * sh * 0.000003 * 100) / 100 if sh > 0 else 0.0
        taf = np.ceil(min(sh * 0.000195, 9.79) * 100) / 100
        fees_pct = (sec + cat + taf) / cap * 100
        per_rebalance = 2 * PER_SIDE * 100 + fees_pct     # % of capital for 100% turnover
        rows[f"${cap:,}"] = {"spreadSlippagePct": 2 * PER_SIDE * 100, "feesPct": fees_pct, "perFullRebalancePct": per_rebalance,
                             "annualDragPct": {"daily": per_rebalance * 252, "weekly": per_rebalance * 52,
                                               "monthly": per_rebalance * 12, "quarterly": per_rebalance * 4}}
    _ = cm
    return rows


# ------------------------------------------------------------------ B. industry factors (49 industries)
def industry_factors(ind: pd.DataFrame, alphalens_check: bool = True) -> dict:
    out = {}
    for seg, r in seg_frames(ind).items():
        p = price_index(r)
        facs = {"mom_12_1": momentum(p, 252, 21), "mom_6_1": momentum(p, 126, 21), "mom_1m": momentum(p, 21, 0),
                "vol_63 (low first)": -volatility(r, 63), "trend_200": trend(p, 200)}
        me = month_ends(p.index)[13:]                     # need a year of history
        we = week_ends(p.index)[60:]
        res = {}
        for h in (21, 63):
            fwd = forward_returns(p, h, lag=1)
            for name, f in facs.items():
                TRIALS.append(f"industry49:{seg}:{name}:{h}d")
                ic = rank_ic(f, fwd, me)
                qs = quantile_spread(f, fwd, me, q=5)
                res[f"{name}|{h}d"] = ic_summary(ic, h, 21) | {"quintiles": qs}
        fwd5 = forward_returns(p, 5, lag=1)
        rev = -momentum(p, 5, 0)
        TRIALS.append(f"industry49:{seg}:rev_1w:5d")
        res["rev_1w (losers first)|5d"] = ic_summary(rank_ic(rev, fwd5, we), 5, 5) | {"quintiles": quantile_spread(rev, fwd5, we, q=5)}
        # long-only translation of the classic signal: top 10 of 49 by 12-1 momentum, monthly
        f = facs["mom_12_1"]
        fwd21 = forward_returns(p, 21, lag=1)
        qs = quantile_spread(f, fwd21, me, q=5)
        to = top_k_turnover(f, me, 10)
        if qs.get("n"):
            gross = qs["topMinusAllPct"] * 12
            cost = to * 2 * PER_SIDE * 100 * 12
            res["longOnlyTop10_vs_equalWeight"] = {"grossExcessPerYearPct": gross, "turnoverPerMonth": to,
                                                   "costPerYearPct": cost, "netExcessPerYearPct": gross - cost}
        if alphalens_check and seg.startswith("1926"):
            res["alphalensCheck_mom_12_1_21d"] = _alphalens_ic(f, p, me)
        out[seg] = res
    return out


def _alphalens_ic(f: pd.DataFrame, p: pd.DataFrame, dates: pd.DatetimeIndex) -> dict:
    """Independent cross-check of our IC with alphalens (its forward returns start at t, not t+1)."""
    try:
        import alphalens as al
    except ImportError:
        return {"skipped": "alphalens-reloaded not installed"}
    fac = f.loc[f.index.isin(dates)].stack().dropna()
    fac.index.names = ["date", "asset"]
    fd = al.utils.get_clean_factor_and_forward_returns(fac, p, periods=(21,), quantiles=5, max_loss=0.5)
    ic = al.performance.factor_information_coefficient(fd)
    mq, _ = al.performance.mean_return_by_quantile(fd)
    ours = rank_ic(f, forward_returns(p, 21, lag=0), dates)
    return {"alphalensMeanIC": float(ic.iloc[:, 0].mean()), "oursSameTimingMeanIC": float(ours.mean()),
            "alphalensQuintileMeanPct": {int(k): float(v * 100) for k, v in mq.iloc[:, 0].items()}}


# ------------------------------------------------------------------ C. market-level conditions
def tom_flags(idx: pd.DatetimeIndex) -> pd.Series:
    """Turn-of-the-month days: last trading day of a month and the first three of the next (-1..+3)."""
    s = pd.Series(0, index=idx)
    ym = idx.year * 12 + idx.month
    pos_fwd = pd.Series(1, index=idx).groupby(ym).cumsum()                 # 1,2,3,... within month
    pos_back = pd.Series(1, index=idx)[::-1].groupby(ym[::-1]).cumsum()[::-1]  # ...,3,2,1 = last
    s[(pos_fwd <= 3) | (pos_back == 1)] = 1
    return s.astype(bool)


def market_conditions(ff3: pd.DataFrame, ind49: pd.DataFrame) -> dict:
    mkt = market_total_return(ff3)
    ex = mkt - ff3["RF"]
    out = {}
    for seg, e in seg_frames(ex.to_frame("ex")).items():
        e = e["ex"]
        rf = ff3["RF"].loc[e.index]
        res = {}
        # turn of the month
        TRIALS.append(f"market:{seg}:turn_of_month")
        tom = tom_flags(e.index)
        a, b = e[tom], e[~tom]
        se = np.sqrt(a.var() / len(a) + b.var() / len(b))
        res["turnOfMonth"] = {"tomDays": int(tom.sum()), "otherDays": int((~tom).sum()),
                              "tomMeanBps": float(a.mean() * 1e4), "otherMeanBps": float(b.mean() * 1e4),
                              "diffTStat": float((a.mean() - b.mean()) / se),
                              "shareOfExcessReturnFromTom": float(a.sum() / e.sum()) if e.sum() != 0 else None,
                              "byDecade": {f"{y}s": {"tomBps": float(e[tom & (e.index.year // 10 * 10 == y)].mean() * 1e4),
                                                     "otherBps": float(e[~tom & (e.index.year // 10 * 10 == y)].mean() * 1e4)}
                                           for y in sorted(set(e.index.year // 10 * 10))}}
        # trend / volatility / breadth vs the next month's excess return
        p = price_index(mkt.loc[e.index].to_frame("m"))["m"]
        me = month_ends(e.index)
        fwd = (p.shift(-22) / p.shift(-1) - 1.0) - rf.rolling(21).sum().shift(-22)   # next month (after a 1-day lag)
        mom12 = p / p.shift(252) - 1.0 - rf.rolling(252).sum()
        vol = e.rolling(63).std() * np.sqrt(252)
        br = breadth(price_index(ind49.loc[e.index]), 200)
        cond = {}
        for name, x, cuts in (("trend_12m_excess", mom12, [0.0]), ("vol_63", vol, None), ("breadth_200", br, None)):
            TRIALS.append(f"market:{seg}:{name}")
            v = pd.concat([x.loc[me], fwd.loc[me]], axis=1, keys=["x", "f"]).dropna()
            if len(v) < 24:
                continue
            if cuts is None:
                v["g"] = pd.qcut(v.x, 3, labels=["low", "mid", "high"])
            else:
                v["g"] = np.where(v.x > cuts[0], "positive", "negative")
            cond[name] = {str(g): {"months": int(len(d)), "nextMonthExcessPct": float(d.f.mean() * 100),
                                   "sdPct": float(d.f.std() * 100), "sharpeAnn": float(d.f.mean() / d.f.std() * np.sqrt(12)) if d.f.std() > 0 else None}
                          for g, d in v.groupby("g", observed=True)}
        res["conditions"] = cond
        out[seg] = res
    return out


# ------------------------------------------------------------------ D. stock level (the app's universe)
def stock_factors(daily_dir: Path) -> dict:
    closes, vols = {}, {}
    for f in sorted(Path(daily_dir).glob("*.pkl")):
        d = pickle.loads(f.read_bytes())
        if d.empty or len(d) < 300:
            continue
        d.index = pd.to_datetime(d.index)
        closes[f.stem.replace("_", ".")] = d["close"].astype(float)
        vols[f.stem.replace("_", ".")] = (d["close"] * d["volume"]).astype(float)
    c = pd.DataFrame(closes).sort_index()
    r = c.pct_change()
    # undo share splits / reverse splits (no corporate-action feed here): a one-day move matching a split ratio
    for ratio in (2, 3, 4, 5, 10, 20, 0.5, 1 / 3, 0.25, 0.2, 0.1, 0.05):
        x = (1 + r) * ratio
        hit = (x >= 0.97) & (x <= 1.03) & (r.abs() > 0.3)
        r = r.mask(hit, (1 + r) * ratio - 1)
    r = r.mask(r.abs() > 0.6)                                 # anything else that large is treated as bad data
    p = price_index(r)
    dv = pd.DataFrame(vols).sort_index().rolling(21).mean()
    etfs = {"SPY", "QQQ", "IWM", "DIA", "IVV", "VOO", "RSP"}
    keep = [s for s in p.columns if s not in etfs]
    p, r, dv = p[keep], r[keep], dv[keep]
    me = month_ends(p.index)[13:]
    we = week_ends(p.index)[60:]
    facs = {"mom_12_1": momentum(p, 252, 21), "mom_1m": momentum(p, 21, 0), "vol_63 (low first)": -volatility(r, 63),
            "trend_200": trend(p, 200), "liquidity (log $vol)": np.log(dv)}
    res = {"symbols": len(keep), "from": str(p.index[0].date()), "to": str(p.index[-1].date())}
    fwd = forward_returns(p, 21, lag=1)
    for name, f in facs.items():
        TRIALS.append(f"stocks:{name}:21d")
        res[f"{name}|21d"] = ic_summary(rank_ic(f, fwd, me), 21, 21) | {"quintiles": quantile_spread(f, fwd, me)}
    TRIALS.append("stocks:rev_1w:5d")
    fwd5 = forward_returns(p, 5, lag=1)
    rev = -momentum(p, 5, 0)
    res["rev_1w (losers first)|5d"] = ic_summary(rank_ic(rev, fwd5, we), 5, 5) | {"quintiles": quantile_spread(rev, fwd5, we)}
    return res


def main(p3: Path, p2daily: Path, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    ff3 = load_series(p3, "ff3_daily")
    ind49 = load_series(p3, "ind49_daily")
    review = {"note": "development data only; exploratory", "costs": cost_economics(),
              "industries49": industry_factors(ind49), "market": market_conditions(ff3, ind49),
              "stocks": stock_factors(p2daily)}
    review["trials"] = {"count": len(TRIALS), "list": TRIALS}
    (out / "review.json").write_text(json.dumps(review, indent=1, default=str))
    print(json.dumps({k: v for k, v in review.items() if k != "trials"}, indent=1, default=str)[:20000])
    print("trials:", len(TRIALS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])))
