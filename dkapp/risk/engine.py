"""Deterministic risk engine.

This module is the final gate before any order. It is pure, rule-based code: no AI
model output can bypass it, and an AI's suggested stop/target is only ever *clamped*
by it. Every rejected trade gets a plain-English reason.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from ..clock import SessionState
from ..config import AppSettings
from ..data.universe import asset_allowed, is_safe_symbol
from ..models import AccountInfo, AssetInfo
from .fees import CostModel
from .sizing import SizeResult, SizingInputs, position_size, scaled_limits


@dataclass
class OpenRisk:
    symbol: str
    qty: float
    entry_price: float
    current_price: float
    stop_price: float

    @property
    def risk_to_stop(self) -> float:
        return max(0.0, (self.current_price - self.stop_price) * self.qty)


@dataclass
class RiskContext:
    now: datetime
    session: SessionState
    mode: str
    autopilot: bool
    emergency_stop: bool
    live_authorized: bool
    broker_connected: bool
    data_ok: bool
    account: Optional[AccountInfo]
    settled_cash: float
    open_positions: list[OpenRisk] = field(default_factory=list)
    pending_entry_symbols: set[str] = field(default_factory=set)
    pending_buy_notional: float = 0.0
    realized_today: float = 0.0
    unrealized_today: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    last_exit_time: dict[str, datetime] = field(default_factory=dict)
    held_symbols: set[str] = field(default_factory=set)  # includes positions not managed by the app

    @property
    def pnl_today(self) -> float:
        return self.realized_today + self.unrealized_today


@dataclass
class EntryProposal:
    symbol: str
    price: float
    bid: float
    ask: float
    quote_time: Optional[datetime]
    stop_price: float
    target_price: float
    strategy: str
    strategy_validated: bool
    asset: Optional[AssetInfo] = None


@dataclass
class RiskDecision:
    approved: bool
    reasons: list[str]
    notes: list[str] = field(default_factory=list)
    notional: float = 0.0
    qty: float = 0.0
    limit_price: Optional[float] = None
    stop_price: float = 0.0
    target_price: float = 0.0
    size: Optional[SizeResult] = None

    def summary(self) -> str:
        if self.approved:
            return f"approved: ${self.notional:.2f}, stop ${self.stop_price:.2f}" + (
                f" ({'; '.join(self.notes)})" if self.notes else "")
        return "blocked: " + "; ".join(self.reasons)


def round_price(p: float) -> float:
    """Alpaca/sub-penny rule: 2 decimals at or above $1, 4 decimals below."""
    return round(p, 2) if p >= 1 else round(p, 4)


def floor_qty(q: float, decimals: int = 6) -> float:
    f = 10 ** decimals
    return math.floor(q * f) / f


class RiskEngine:
    def __init__(self, settings: AppSettings, cost_model: Optional[CostModel] = None):
        self.settings = settings
        self.costs = cost_model or CostModel()

    # ------------------------------------------------------------- daily limit
    def daily_limit(self, equity: float) -> float:
        return scaled_limits(self.settings, equity)["daily_loss_limit_usd"]

    def daily_limit_breached(self, ctx: RiskContext) -> bool:
        equity = ctx.account.equity if ctx.account else self.settings.risk.starting_capital
        return ctx.pnl_today <= -self.daily_limit(equity) + 1e-9

    # ------------------------------------------------------------- entries
    def check_entry(self, ctx: RiskContext, p: EntryProposal) -> RiskDecision:
        r = self.settings.risk
        reasons: list[str] = []
        notes: list[str] = []
        acct = ctx.account

        if ctx.emergency_stop:
            reasons.append("emergency stop is active")
        if not ctx.autopilot:
            reasons.append("autopilot is off")
        if ctx.mode == "live" and not ctx.live_authorized:
            reasons.append("live trading is not authorized")
        if not p.strategy_validated:
            if ctx.mode == "live":
                reasons.append(f"strategy '{p.strategy}' has no backtest evidence (required for real money)")
            elif not self.settings.strategies.paper_allow_unvalidated:
                reasons.append(f"strategy '{p.strategy}' is not validated")
        if not ctx.broker_connected:
            reasons.append("brokerage connection is down")
        if not ctx.data_ok:
            reasons.append("market data is unhealthy")
        if acct is None:
            reasons.append("account details unavailable")
        elif acct.trading_blocked or acct.account_blocked:
            reasons.append("the brokerage has blocked trading on this account")

        # session / hours
        if not ctx.session.is_open:
            reasons.append("market is closed")
        elif ctx.session.phase == "opening":
            reasons.append(f"waiting out the first {r.no_entries_first_minutes} minutes after the open")
        elif ctx.session.phase in ("wind_down", "flatten"):
            reasons.append("too close to the market close for new trades")

        # instrument
        if not is_safe_symbol(p.symbol):
            reasons.append("unsupported symbol (options/crypto/other)")
        if p.asset is not None:
            ok, why = asset_allowed(p.asset)
            if not ok:
                reasons.append(why)
        if not (r.min_price <= p.price <= r.max_price):
            reasons.append(f"price ${p.price:.2f} outside ${r.min_price:.0f}–${r.max_price:.0f}")

        # data quality
        if p.quote_time is None:
            reasons.append("no live quote")
        else:
            age = (ctx.now - p.quote_time).total_seconds()
            if age > r.max_data_age_seconds:
                reasons.append(f"quote is {age:.0f}s old (max {r.max_data_age_seconds}s)")
        if p.bid <= 0 or p.ask <= 0 or p.ask < p.bid:
            reasons.append("invalid bid/ask")
        else:
            spread = (p.ask - p.bid) / ((p.ask + p.bid) / 2) * 10_000
            if spread > r.max_spread_bps:
                reasons.append(f"spread {spread:.0f} bps is wider than {r.max_spread_bps:.0f} bps")

        # stop sanity (clamp, never loosen beyond max)
        ref = p.ask if p.ask > 0 else p.price
        stop = p.stop_price
        if not (0 < stop < ref):
            stop = ref * (1 - r.default_stop_pct / 100)
            notes.append(f"stop reset to default {r.default_stop_pct:.1f}%")
        stop_pct = (ref - stop) / ref * 100
        if stop_pct < r.min_stop_pct:
            stop = ref * (1 - r.min_stop_pct / 100)
            stop_pct = r.min_stop_pct
            notes.append(f"stop widened to minimum {r.min_stop_pct:.1f}%")
        if stop_pct > r.max_stop_pct:
            reasons.append(f"stop would be {stop_pct:.1f}% away (max {r.max_stop_pct:.1f}%)")
        target = p.target_price if p.target_price > ref else ref * (1 + 2 * stop_pct / 100)

        # account-level limits
        equity = acct.equity if acct else r.starting_capital
        limit = self.daily_limit(equity)
        if ctx.pnl_today <= -limit + 1e-9:
            reasons.append(f"daily loss limit reached (${-ctx.pnl_today:.2f} of ${limit:.2f})")
        if ctx.consecutive_losses >= r.max_consecutive_losses:
            reasons.append(f"{ctx.consecutive_losses} losses in a row — pausing new trades for today")
        if ctx.trades_today >= r.max_trades_per_day:
            reasons.append(f"reached {r.max_trades_per_day} trades today")
        open_count = len(ctx.open_positions) + len(ctx.pending_entry_symbols)
        if open_count >= r.max_positions:
            reasons.append(f"already holding {open_count} of {r.max_positions} allowed positions")
        if p.symbol in ctx.held_symbols or p.symbol in ctx.pending_entry_symbols or any(
                o.symbol == p.symbol for o in ctx.open_positions):
            reasons.append(f"already have a position or order in {p.symbol}")
        last = ctx.last_exit_time.get(p.symbol)
        if last is not None and ctx.now - last < timedelta(minutes=r.symbol_cooldown_minutes):
            reasons.append(f"cooling down on {p.symbol} after a recent exit")
        if r.pdt_guard and acct is not None and acct.pattern_day_trader is False and acct.daytrade_count is not None \
                and acct.daytrade_count >= 3 and acct.equity < 25_000:
            reasons.append("legacy pattern-day-trader guard: 3 day trades used in 5 days")

        # sizing
        inp = SizingInputs(
            equity=equity,
            settled_cash=ctx.settled_cash,
            broker_cash=acct.cash if acct else 0.0,
            broker_buying_power=min(acct.buying_power, acct.non_marginable_buying_power or acct.buying_power) if acct else 0.0,
            pending_buy_notional=ctx.pending_buy_notional,
            consecutive_losses=ctx.consecutive_losses,
        )
        size = position_size(self.settings, inp, stop_pct)
        notional = size.notional

        # worst-case daily loss check: all open stops + this one must fit inside what's left today
        remaining = limit + min(0.0, ctx.pnl_today)
        open_risk = sum(o.risk_to_stop for o in ctx.open_positions)
        per_dollar = stop_pct / 100 + (2 * self.costs.slippage_bps) / 10_000
        fees = self.costs.sell_fees(notional, notional / ref if ref > 0 else 0)
        room = remaining - open_risk - fees
        if notional * per_dollar > room:
            fitted = math.floor(max(0.0, room / per_dollar) * 100) / 100 if per_dollar > 0 else 0.0
            if fitted < notional:
                notes.append(f"size cut to ${fitted:.2f} so a stop-out can't break the daily limit")
                notional = fitted
        if notional < r.min_order_usd:
            reasons.append(f"position would be ${notional:.2f} — below the ${r.min_order_usd:.2f} minimum "
                           f"({size.binding})")
        if acct is not None and notional > acct.cash + 1e-9:
            reasons.append("would require margin")

        limit_price = None
        if r.entry_order_type == "limit":
            limit_price = round_price(ref * (1 + r.limit_offset_bps / 10_000))
            qty = floor_qty(notional / limit_price) if limit_price > 0 else 0.0
        else:
            qty = floor_qty(notional / ref) if ref > 0 else 0.0
        if qty <= 0 and not reasons:
            reasons.append("quantity rounds to zero")

        return RiskDecision(
            approved=not reasons,
            reasons=reasons,
            notes=notes,
            notional=notional,
            qty=qty,
            limit_price=limit_price,
            stop_price=round_price(stop),
            target_price=round_price(target),
            size=size,
        )

    # ------------------------------------------------------------- exits
    @staticmethod
    def check_exit(symbol: str, qty: float, held_qty: float) -> tuple[bool, str]:
        """Exits reduce risk and are always allowed, but never oversell (no shorting)."""
        if qty <= 0:
            return False, "nothing to sell"
        if qty > held_qty + 1e-9:
            return False, f"cannot sell {qty} — only {held_qty} held (no short selling)"
        return True, ""
