"""Settled-cash tracking.

U.S. stock trades settle one business day after the trade (T+1, since May 2024).
In a cash account, buying with unsettled sale proceeds and selling before they settle
is a "good-faith violation". Our conservative mode only ever spends *settled* cash,
which makes that impossible regardless of how the broker classifies the account.
"""
from __future__ import annotations

from datetime import date

from ..clock import MarketCalendar
from ..storage.db import Database


class SettlementLedger:
    def __init__(self, db: Database, calendar: MarketCalendar):
        self.db = db
        self.calendar = calendar

    def record_sale(self, trade_id: int | None, proceeds: float, trade_date: date) -> date:
        settle = self.calendar.add_trading_days(trade_date, 1)
        if proceeds > 0:
            self.db.insert("settlements", trade_id=trade_id, amount=float(proceeds), trade_date=trade_date.isoformat(),
                           settle_date=settle.isoformat())
        return settle

    def unsettled(self, as_of: date) -> float:
        return float(self.db.scalar("SELECT SUM(amount) FROM settlements WHERE settle_date > ?", (as_of.isoformat(),), 0.0))

    def settled_cash(self, broker_cash: float, as_of: date) -> float:
        return max(0.0, broker_cash - self.unsettled(as_of))

    def schedule(self, as_of: date) -> list[dict]:
        return self.db.query("SELECT settle_date, SUM(amount) AS amount FROM settlements WHERE settle_date > ? "
                             "GROUP BY settle_date ORDER BY settle_date", (as_of.isoformat(),))
