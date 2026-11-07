"""Long-only daily portfolio simulator for low-turnover strategies (research only; places no orders).

Model:
* Daily close-to-close total returns move each holding. Cash earns `cash_returns`, or 0.
* `targets` maps an EXECUTION day to target weights, filled at that day's close. Strategy builders apply
  their own implementation lag; this file never looks ahead.
* Each trade pays half the quoted spread plus slippage on its notional. Regulatory fees come from the app's
  `CostModel`. Each fee type's daily total is rounded up to a cent, which is why small accounts pay
  proportionally more.
* Orders below `min_order` dollars are skipped, except full liquidations. There is no leverage: weights are
  capped so their sum is at most 1.
* `buy_delay=1` models a cash account that waits a day (T+1 settlement) before spending sale proceeds.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from ..risk.fees import CostModel, FeeBreakdown


def _ceil_cent(x: float) -> float:
    return math.ceil(round(x * 100, 9)) / 100.0 if x > 0 else 0.0


@dataclass(frozen=True)
class SimCosts:
    spread_bps: float = 3.0            # quoted spread; half is paid on each side
    slippage_bps: float = 5.0          # per side
    fees: Optional[CostModel] = field(default_factory=CostModel)
    share_price: float = 100.0         # for per-share fees when no price frame is given
    min_order: float = 1.0             # Alpaca's fractional minimum

    @property
    def per_side(self) -> float:
        return (self.spread_bps / 2.0 + self.slippage_bps) / 1e4

    def scaled(self, k: float) -> "SimCosts":
        return SimCosts(self.spread_bps * k, self.slippage_bps * k, self.fees, self.share_price, self.min_order)


@dataclass
class SimResult:
    daily: pd.DataFrame                # equity, ret, gross_ret, cost, turnover, exposure
    trades: int
    capital: float

    @property
    def ret(self) -> pd.Series:
        return self.daily["ret"]


def simulate(returns: pd.DataFrame, targets: dict, capital: float, costs: SimCosts = SimCosts(),
             cash_returns: Optional[pd.Series] = None, prices: Optional[pd.DataFrame] = None,
             buy_delay: int = 0) -> SimResult:
    assets = list(returns.columns)
    pos = {a: 0.0 for a in assets}
    cash = float(capital)
    rows = []
    pending_buys: dict[int, dict] = {}
    trades = 0
    R = returns.fillna(0.0)
    days = list(R.index)
    tgt = {pd.Timestamp(k): v for k, v in targets.items()}
    for i, d in enumerate(days):
        start_eq = cash + sum(pos.values())
        r = R.iloc[i]
        for a in assets:
            if pos[a]:
                pos[a] *= 1.0 + float(r[a])
        if cash_returns is not None and d in cash_returns.index and cash > 0:
            cash *= 1.0 + float(cash_returns.loc[d])
        cost = 0.0
        traded = 0.0
        fees = FeeBreakdown()
        orders: dict = {}
        if d in tgt:
            w = pd.Series(tgt[d], dtype=float).reindex(assets).fillna(0.0).clip(lower=0.0)
            if w.sum() > 1.0 + 1e-9:
                w = w / w.sum()
            eq = cash + sum(pos.values())
            for a in assets:
                orders[a] = w[a] * eq - pos[a]
        if i in pending_buys:
            for a, amt in pending_buys.pop(i).items():
                orders[a] = orders.get(a, 0.0) + amt
        if orders:
            px = (lambda a: float(prices.loc[d, a])) if prices is not None else (lambda a: costs.share_price)
            # sells first (they fund the buys)
            for a, amt in sorted(orders.items(), key=lambda kv: kv[1]):
                if amt >= 0:
                    continue
                sell = min(-amt, pos[a])
                full = sell >= pos[a] - 1e-9
                if sell <= 0 or (sell < costs.min_order and not full):
                    continue
                pos[a] -= sell
                c = sell * costs.per_side
                cash += sell - c
                cost += c
                traded += sell
                trades += 1
                if costs.fees is not None:
                    fees = fees + costs.fees.sale_breakdown(sell, sell / max(px(a), 1e-9))
            buys = {a: amt for a, amt in orders.items() if amt > 0}
            if buys and buy_delay > 0 and d in tgt:
                pending_buys.setdefault(i + buy_delay, {}).update(buys)
                buys = {}
            # keep a few cents back for the day's rounded regulatory fees: cash never goes negative
            budget = max(cash - (0.05 if costs.fees is not None else 0.0), 0.0)
            want = sum(buys.values())
            scale = min(1.0, budget / (want * (1 + costs.per_side))) if want > 0 else 0.0
            for a, amt in buys.items():
                buy = amt * scale
                if buy < costs.min_order:
                    continue
                c = buy * costs.per_side
                pos[a] += buy
                cash -= buy + c
                cost += c
                traded += buy
                trades += 1
                if costs.fees is not None:
                    fees = fees + costs.fees.buy_breakdown(buy / max(px(a), 1e-9))
            if costs.fees is not None:
                fee_cost = _ceil_cent(fees.sec) + _ceil_cent(fees.taf) + _ceil_cent(fees.cat)
                cash -= fee_cost
                cost += fee_cost
        eq = cash + sum(pos.values())
        ret = eq / start_eq - 1.0 if start_eq > 0 else 0.0
        rows.append((d, eq, ret, ret + (cost / start_eq if start_eq > 0 else 0.0), cost,
                     traded / start_eq if start_eq > 0 else 0.0, sum(pos.values()) / eq if eq > 0 else 0.0))
    daily = pd.DataFrame(rows, columns=["date", "equity", "ret", "gross_ret", "cost", "turnover", "exposure"]).set_index("date")
    return SimResult(daily, trades, capital)


# ------------------------------------------------------------------------------------------ metrics
def perf(daily_ret: pd.Series, rf: Optional[pd.Series] = None, exposure: Optional[pd.Series] = None,
         turnover: Optional[pd.Series] = None) -> dict:
    x = daily_ret.dropna()
    if len(x) < 2:
        return {"days": len(x)}
    ex = x - (rf.reindex(x.index).fillna(0.0) if rf is not None else 0.0)
    eq = (1.0 + x).cumprod()
    dd = eq / eq.cummax() - 1.0
    n = len(x)
    years = n / 252.0
    cagr = float(eq.iloc[-1] ** (1 / years) - 1) if years > 0 else 0.0
    sd = float(x.std(ddof=1))
    down = float(np.sqrt((np.minimum(ex, 0.0) ** 2).mean()))
    monthly = (1.0 + x).groupby([x.index.year, x.index.month]).prod() - 1.0
    out = {"days": n, "years": years, "cagrPct": cagr * 100, "totalPct": float(eq.iloc[-1] - 1) * 100,
           "volPct": sd * math.sqrt(252) * 100,
           "sharpe": float(ex.mean() / ex.std(ddof=1) * math.sqrt(252)) if ex.std(ddof=1) > 0 else 0.0,
           "sortino": float(ex.mean() / down * math.sqrt(252)) if down > 0 else 0.0,
           "maxDrawdownPct": float(dd.min()) * 100,
           "calmar": float(cagr / abs(dd.min())) if dd.min() < 0 else None,
           "worstDayPct": float(x.min()) * 100, "worst2DayPct": float(((1 + x).rolling(2).apply(np.prod, raw=True) - 1).min()) * 100,
           "bestMonthPct": float(monthly.max()) * 100, "worstMonthPct": float(monthly.min()) * 100,
           "positiveMonths": float((monthly > 0).mean())}
    if exposure is not None:
        out["avgExposure"] = float(exposure.reindex(x.index).mean())
    if turnover is not None:
        out["turnoverPerYear"] = float(turnover.reindex(x.index).sum() / years) if years > 0 else 0.0
    return out


def monthly_returns(daily_ret: pd.Series) -> pd.Series:
    x = daily_ret.dropna()
    g = (1.0 + x).groupby([x.index.year, x.index.month]).prod() - 1.0
    g.index = pd.PeriodIndex([pd.Period(year=y, month=m, freq="M") for y, m in g.index])
    return g
