"""Account-based position sizing with automatic compounding.

* reinvest mode: limits scale with the app's OWN capital — the starting capital plus everything the
  app itself has realized (after fees) — and never more than the account actually holds. $100 that
  grows to $105 means a $20 cap becomes $21; $95 means $19. Other money in the account (a paper
  account's $100,000, a bigger deposit) is never put to work without you raising the starting capital.
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
    app_capital: float | None = None  # starting capital + the app's own realized P/L


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


def capital_base(settings: AppSettings, equity: float, app_capital: float | None = None) -> float:
    """The money the app trades with. Never the broker's whole equity: in reinvest mode it is the app's
    own capital (starting capital + its realized P/L; the starting capital when unknown), capped by
    what the account really holds."""
    equity = max(0.0, equity)
    if settings.compounding.mode == "fixed":
        return min(settings.compounding.fixed_allocation_usd, equity)
    own = settings.risk.starting_capital if app_capital is None else app_capital
    return max(0.0, min(own, equity))


def scale_factor(settings: AppSettings, equity: float, app_capital: float | None = None) -> float:
    return capital_base(settings, equity, app_capital) / settings.risk.starting_capital


def scaled_limits(settings: AppSettings, equity: float, app_capital: float | None = None) -> dict[str, float]:
    base = capital_base(settings, equity, app_capital)
    s = base / settings.risk.starting_capital
    r = settings.risk
    return {
        "scale": s,
        "capital_base": base,
        "max_position_usd": r.max_position_usd * s,
        "daily_loss_limit_usd": r.daily_loss_limit_usd * s,
        "risk_per_trade_usd": base * r.max_risk_per_trade_pct / 100,
    }


def spendable_cash(settings: AppSettings, inp: SizingInputs) -> float:
    """What the next buy may spend. Never margin: capped by the cash actually in the account even when
    the broker reports more buying power (accounts over $2,000 get 2x-4x), and by the broker's own
    buying power (which already subtracts open orders). In auto mode, proceeds of today's sales count
    (Alpaca limited-margin accounts may reuse them before T+1 settlement); in conservative mode only
    settled cash does."""
    no_margin = min(inp.broker_buying_power, inp.broker_cash)
    if settings.risk.settlement_mode == "conservative":
        avail = min(no_margin, inp.settled_cash)
    else:
        avail = no_margin
    return max(0.0, avail - inp.pending_buy_notional)


def position_size(settings: AppSettings, inp: SizingInputs, stop_pct: float) -> SizeResult:
    lim = scaled_limits(settings, inp.equity, inp.app_capital)
    damp = 1.0
    if settings.risk.reduce_size_after_losses and inp.consecutive_losses > 0:
        damp = max(0.5, 0.75 ** inp.consecutive_losses)
    cap_position = lim["max_position_usd"] * damp
    cap_risk = lim["risk_per_trade_usd"] / (stop_pct / 100) if stop_pct > 0 else 0.0
    cap_cash = spendable_cash(settings, inp) * 0.995  # small buffer for limit-price slippage
    cash_label = "available settled cash" if settings.risk.settlement_mode == "conservative" else "available cash"
    caps = {"position limit": cap_position, "risk per trade": cap_risk, cash_label: cap_cash}
    binding = min(caps, key=caps.get)
    notional = math.floor(max(0.0, caps[binding]) * 100) / 100
    return SizeResult(notional, lim["capital_base"], lim["scale"], cap_position, cap_risk, cap_cash, damp, binding)
