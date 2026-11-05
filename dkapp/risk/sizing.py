"""Account-based position sizing with automatic compounding.

* reinvest mode: limits scale with current equity. $100 → $105 means a $20 cap becomes
  $21; $100 → $95 means it becomes $19.
* fixed mode: the trading allocation stays at the configured amount (and shrinks if the
  account falls below it). Profits are not put back to work.

Sizes are always capped by *spendable* cash (settled cash in conservative mode) and by
the per-trade risk budget. Losses only ever make positions smaller.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..config import AppSettings


@dataclass
class SizingInputs:
    equity: float
    settled_cash: float
    broker_cash: float
    broker_buying_power: float
    pending_buy_notional: float = 0.0
    consecutive_losses: int = 0


@dataclass
class SizeResult:
    notional: float
    capital_base: float
    scale: float
    cap_position: float
    cap_risk: float
    cap_cash: float
    dampening: float
    binding: str

    def explain(self) -> str:
        return (f"size ${self.notional:.2f}: limited by {self.binding} "
                f"(position cap ${self.cap_position:.2f}, risk cap ${self.cap_risk:.2f}, cash ${self.cap_cash:.2f})")


def capital_base(settings: AppSettings, equity: float) -> float:
    equity = max(0.0, equity)
    if settings.compounding.mode == "fixed":
        return min(settings.compounding.fixed_allocation_usd, equity)
    return equity


def scale_factor(settings: AppSettings, equity: float) -> float:
    return capital_base(settings, equity) / settings.risk.starting_capital


def scaled_limits(settings: AppSettings, equity: float) -> dict[str, float]:
    s = scale_factor(settings, equity)
    r = settings.risk
    return {
        "scale": s,
        "capital_base": capital_base(settings, equity),
        "max_position_usd": r.max_position_usd * s,
        "daily_loss_limit_usd": r.daily_loss_limit_usd * s,
        "risk_per_trade_usd": capital_base(settings, equity) * r.max_risk_per_trade_pct / 100,
    }


def spendable_cash(settings: AppSettings, inp: SizingInputs) -> float:
    # Never use margin: cap by cash even if the broker reports more buying power.
    no_margin = min(inp.broker_buying_power, inp.broker_cash)
    if settings.risk.settlement_mode == "conservative":
        avail = min(no_margin, inp.settled_cash)
    else:
        avail = no_margin
    return max(0.0, avail - inp.pending_buy_notional)


def position_size(settings: AppSettings, inp: SizingInputs, stop_pct: float) -> SizeResult:
    lim = scaled_limits(settings, inp.equity)
    damp = 1.0
    if settings.risk.reduce_size_after_losses and inp.consecutive_losses > 0:
        damp = max(0.5, 0.75 ** inp.consecutive_losses)
    cap_position = lim["max_position_usd"] * damp
    cap_risk = lim["risk_per_trade_usd"] / (stop_pct / 100) if stop_pct > 0 else 0.0
    cap_cash = spendable_cash(settings, inp) * 0.995  # small buffer for limit-price slippage
    caps = {"position limit": cap_position, "risk per trade": cap_risk, "available settled cash": cap_cash}
    binding = min(caps, key=caps.get)
    notional = math.floor(max(0.0, caps[binding]) * 100) / 100
    return SizeResult(notional, lim["capital_base"], lim["scale"], cap_position, cap_risk, cap_cash, damp, binding)
