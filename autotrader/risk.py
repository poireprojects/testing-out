"""Deterministic risk controller: the only path from a decision to a (simulated) order.

Order of checks: kill switch -> system health (model, data, ledger) -> permitted mode -> a valid long proposal ->
instrument allowlist (the event's stock or its sector ETF, in the app's universe, plain U.S. symbol) -> exit time
inside the owner's window -> live quote present -> the decision's own entry conditions -> the desktop app's
RiskEngine.check_entry (capital, $20 position cap, 2 positions, daily loss limit, consecutive losses, trade
count, cooldown, trading hours, price range, quote age, spread, stop bounds, sizing) on a simulated $100 account.

The controller sizes the position itself; nothing in a decision can raise a limit. Any exception inside the
controller rejects the order (fail closed). Rejections are final: nobody is asked during market hours.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Optional

from dkapp.clock import ET, MarketCalendar, session_state
from dkapp.data.universe import is_safe_symbol
from dkapp.models import AccountInfo
from dkapp.risk.engine import EntryProposal, OpenRisk, RiskContext, RiskEngine
from dkapp.risk.fees import CostModel

from .evidence import EvidencePack
from .policy import OwnerPolicy


@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    time: datetime
    prev_close: Optional[float] = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.mid * 1e4 if self.mid > 0 else float("inf")


@dataclass
class Health:
    model_failures: int = 0          # consecutive
    data_failures: int = 0           # consecutive
    ledger_ok: bool = True

    def safe_state_reasons(self, policy: OwnerPolicy) -> list[str]:
        r = []
        if self.data_failures >= policy.max_consecutive_data_failures:
            r.append(f"safe state: {self.data_failures} consecutive data failures")
        if not self.ledger_ok:
            r.append("safe state: the decision ledger cannot be written")
        return r


@dataclass
class SimPosition:
    symbol: str
    qty: float
    entry_price: float
    stop_price: float
    entry_time: datetime


@dataclass
class SimPortfolio:
    """One simulated $100 account per decider, so deciders never compete for the same slots."""
    capital: float = 100.0
    realized: float = 0.0
    day: Optional[date] = None
    realized_today: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    open: list[SimPosition] = field(default_factory=list)
    last_exit: dict[str, datetime] = field(default_factory=dict)

    @property
    def equity(self) -> float:
        return self.capital + self.realized

    def roll(self, d: date) -> None:
        if self.day != d:
            self.day, self.realized_today, self.trades_today, self.consecutive_losses = d, 0.0, 0, 0

    def close(self, symbol: str, pnl: float, when: datetime) -> None:
        self.open = [p for p in self.open if p.symbol != symbol]
        self.realized += pnl
        self.realized_today += pnl
        self.consecutive_losses = self.consecutive_losses + 1 if pnl < 0 else 0
        self.last_exit[symbol] = when


def _hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


class RiskController:
    def __init__(self, policy: OwnerPolicy, universe: set[str], run_dir: Optional[Path] = None,
                 calendar: Optional[MarketCalendar] = None):
        self.policy = policy
        self.universe = {u.upper() for u in universe}
        self.run_dir = Path(run_dir) if run_dir else None
        self.calendar = calendar or MarketCalendar()
        self.settings = OwnerPolicy.risk_settings()

    def kill_switch(self) -> bool:
        return bool(self.run_dir and (self.run_dir / self.policy.kill_switch_file).exists())

    def check(self, decision: dict, pack: EvidencePack, quote: Optional[Quote], portfolio: SimPortfolio,
              health: Health, now: datetime) -> dict:
        try:
            return self._check(decision, pack, quote, portfolio, health, now)
        except Exception as exc:                      # fail closed
            return {"approved": False, "reasons": [f"risk controller error: {type(exc).__name__}: {exc}"],
                    "checked_at": now.isoformat(), "error": True}

    def _check(self, d: dict, pack: EvidencePack, q: Optional[Quote], pf: SimPortfolio, health: Health,
               now: datetime) -> dict:
        out = {"approved": False, "checked_at": now.isoformat(), "policy_sha256": self.policy.sha256}
        reasons: list[str] = []
        if self.kill_switch():
            reasons.append("kill switch is present: all trading stopped")
        reasons += health.safe_state_reasons(self.policy)
        if self.policy.mode != "simulate":
            reasons.append(f"mode {self.policy.mode!r} is not permitted")
        if d.get("action") != "long":
            return out | {"reasons": reasons or ["no trade proposed"], "no_order": True}
        if not d.get("valid", False):
            reasons.append("the decision failed validation")
        sym = str(d.get("instrument") or "").upper()
        if sym not in (pack.symbol.upper(), pack.sector_etf.upper()):
            reasons.append(f"{sym or 'no instrument'} is not the event's stock or its sector ETF")
        if sym not in self.universe:
            reasons.append(f"{sym} is not in the permitted universe")
        if not is_safe_symbol(sym):
            reasons.append(f"{sym} is not a plain U.S. stock or ETF symbol")
        ex, en = d.get("exit") or {}, d.get("entry") or {}
        try:
            exit_t = _hhmm(ex.get("exit_time", ""))
            if exit_t > _hhmm(self.policy.exit_by):
                reasons.append(f"exit time {ex['exit_time']} is after the owner's limit {self.policy.exit_by}")
            if exit_t <= now.astimezone(ET).time():
                reasons.append(f"exit time {ex['exit_time']} has already passed")
        except (ValueError, AttributeError):
            reasons.append("no valid exit time")
        stop_pct = ex.get("stop_pct")
        if not isinstance(stop_pct, (int, float)) or stop_pct <= 0:
            reasons.append("no stop: every trade needs one")
        if q is None:
            reasons.append(f"no quote for {sym}")
            return out | {"reasons": reasons}
        if q.spread_bps > float(en.get("max_spread_bps") or 0):
            reasons.append(f"entry condition: spread {q.spread_bps:.0f} bps exceeds the decision's "
                           f"{en.get('max_spread_bps')} bps")
        if q.prev_close and (en.get("min_move_pct") is not None or en.get("max_move_pct") is not None):
            move = (q.mid / q.prev_close - 1) * 100
            if en.get("min_move_pct") is not None and move < en["min_move_pct"]:
                reasons.append(f"entry condition: move {move:+.2f}% is below {en['min_move_pct']:+.2f}%")
            if en.get("max_move_pct") is not None and move > en["max_move_pct"]:
                reasons.append(f"entry condition: move {move:+.2f}% is above {en['max_move_pct']:+.2f}%")
        if reasons and not isinstance(stop_pct, (int, float)):
            return out | {"reasons": reasons}

        # the desktop app's own deterministic risk engine, on a simulated account
        cash = pf.equity - sum(p.qty * p.entry_price for p in pf.open)
        acct = AccountInfo(account_number="SIMULATED", status="ACTIVE", currency="USD", equity=pf.equity,
                           last_equity=pf.equity - pf.realized_today, cash=cash, buying_power=cash,
                           non_marginable_buying_power=cash, portfolio_value=pf.equity,
                           long_market_value=pf.equity - cash, multiplier=1.0, trading_blocked=False,
                           account_blocked=False, is_paper=True)
        r = self.settings.risk
        ctx = RiskContext(
            now=now, session=session_state(self.calendar, now, no_entries_first_minutes=r.no_entries_first_minutes,
                                           last_entry_minutes_before_close=r.last_entry_minutes_before_close,
                                           flatten_minutes_before_close=r.flatten_minutes_before_close),
            mode="paper", autopilot=True, emergency_stop=self.kill_switch(), live_authorized=False,
            broker_connected=True, data_ok=health.data_failures == 0, account=acct, settled_cash=cash,
            open_positions=[OpenRisk(p.symbol, p.qty, p.entry_price, p.entry_price, p.stop_price) for p in pf.open],
            realized_today=pf.realized_today, trades_today=pf.trades_today,
            consecutive_losses=pf.consecutive_losses, last_exit_time=dict(pf.last_exit),
            held_symbols={p.symbol for p in pf.open}, app_capital=pf.equity)
        target = ex.get("target_pct")
        # +1e-9 of the price keeps a stop of exactly the maximum (e.g. 3%) from failing on float rounding
        prop = EntryProposal(symbol=sym, price=q.mid, bid=q.bid, ask=q.ask, quote_time=q.time,
                             stop_price=q.ask * (1 - float(stop_pct) / 100) + 1e-9 * q.ask,
                             target_price=q.ask * (1 + float(target) / 100) if target else 0.0,
                             strategy=f"autonomous:{d.get('decider')}", strategy_validated=False)
        rd = RiskEngine(self.settings, CostModel.on(now.astimezone(ET).date())).check_entry(ctx, prop)
        reasons += rd.reasons
        return out | {"approved": not reasons, "reasons": reasons, "notes": rd.notes, "notional": rd.notional,
                      "qty": rd.qty, "stop_price": rd.stop_price, "target_price": rd.target_price if target else None,
                      "symbol": sym, "quote": {"bid": q.bid, "ask": q.ask, "time": q.time.isoformat()}}
