"""Economics of a small trading account: costs, break-even, and hypothetical capital/income scenarios.

Every scenario here is HYPOTHETICAL arithmetic on stated assumptions. None of it is a forecast. A
"net return per trade" is the average result of a trade after all costs, as a % of that trade's
position (not of the account). Denominators are always named:

* per trade   — P/L ÷ the position's dollar size
* on deployed — P/L ÷ the average dollars actually in positions
* on account  — P/L ÷ the app's whole allocation (e.g. $100)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from ..risk.fees import CostModel

TRADING_DAYS_PER_MONTH = 21
DATA_PLAN_USD_PER_MONTH = 99.0   # Alpaca "Algo Trader Plus" (full SIP feed), as widely reported; NOT purchased


@dataclass(frozen=True)
class Plan:
    capital: float
    position_fraction: float = 0.20   # the app's default: $20 of $100
    max_positions: int = 2
    trades_per_day: float = 4.0
    monthly_expenses: float = 0.0     # e.g. a paid data feed or a paid AI model

    @property
    def position(self) -> float:
        return self.capital * self.position_fraction


def cost_breakdown(model: CostModel, notional: float, price: float = 100.0, spread_bps: float = 3.0) -> dict:
    return {k: round(v, 5) for k, v in model.breakdown_pct(notional, price, spread_bps).items()}


def monthly_outcome(plan: Plan, net_per_trade_pct: float, per_trade_sd_pct: Optional[float] = None) -> dict:
    """Arithmetic for one hypothetical month (21 trading days)."""
    n = plan.trades_per_day * TRADING_DAYS_PER_MONTH
    pnl = n * plan.position * net_per_trade_pct / 100 - plan.monthly_expenses
    deployed = plan.position * min(plan.max_positions, max(1.0, plan.trades_per_day / 4))  # rough average in use
    out = {"capital": plan.capital, "position": plan.position, "tradesPerMonth": n,
           "netPerTradePct": net_per_trade_pct, "monthlyPnl": pnl,
           "onAccountPct": pnl / plan.capital * 100 if plan.capital else 0.0,
           "onDeployedPct": pnl / deployed * 100 if deployed else 0.0,
           "expenses": plan.monthly_expenses}
    if per_trade_sd_pct is not None:
        sd = math.sqrt(n) * plan.position * per_trade_sd_pct / 100  # independent trades (optimistic)
        out["p05"] = pnl - 1.645 * sd
        out["p95"] = pnl + 1.645 * sd
    return out


def capital_needed(target_monthly: float, net_per_trade_pct: float, trades_per_day: float,
                   position_fraction: float = 0.20, monthly_expenses: float = 0.0) -> Optional[float]:
    """Allocation needed for a target monthly profit at a given edge (None if the edge is ≤ 0)."""
    per_dollar = trades_per_day * TRADING_DAYS_PER_MONTH * position_fraction * net_per_trade_pct / 100
    if per_dollar <= 0:
        return None
    return (target_monthly + monthly_expenses) / per_dollar


def economics(model: CostModel, spread_bps: float = 3.0, price: float = 100.0,
              per_trade_sd_pct: float = 0.6) -> dict:
    """Everything the report and the app's Performance page show about economics."""
    sizes = [20.0, 100.0, 200.0, 1000.0, 2000.0]
    breakeven = {f"${s:,.0f}": cost_breakdown(model, s, price, spread_bps) for s in sizes}
    capitals = [100.0, 500.0, 1000.0, 5000.0, 10000.0]
    edges = [-0.25, -0.10, 0.0, 0.02, 0.05, 0.10, 0.20]
    scen = []
    for cap in capitals:
        for tpd in (1.0, 4.0):
            for e in edges:
                scen.append(monthly_outcome(Plan(cap, trades_per_day=tpd), e, per_trade_sd_pct) | {"tradesPerDay": tpd})
    with_data = [monthly_outcome(Plan(cap, trades_per_day=4.0, monthly_expenses=DATA_PLAN_USD_PER_MONTH), 0.05)
                 for cap in capitals]
    needed = {f"{e:+.2f}%": {f"${t}/mo": capital_needed(t, e, 4.0) for t in (100, 500, 1000)}
              for e in (0.02, 0.05, 0.10, 0.20)}
    return {"costModel": {"secRate": model.sec_rate, "tafPerShare": model.taf_per_share,
                          "catPerShare": model.cat_per_share, "rounding": model.rounding,
                          "slippageBpsPerSide": model.slippage_bps, "spreadBps": spread_bps},
            "breakEvenBySize": breakeven, "scenarios": scen, "withPaidData": with_data,
            "capitalNeeded": needed, "perTradeSdPct": per_trade_sd_pct}
