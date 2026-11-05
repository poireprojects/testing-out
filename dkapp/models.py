"""Broker/data-agnostic value types shared across the backend."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop"]

FINAL_ORDER_STATES = {"filled", "canceled", "expired", "rejected", "replaced", "done_for_day", "stopped", "suspended"}
OPEN_ORDER_STATES = {
    "new", "accepted", "pending_new", "partially_filled", "pending_cancel", "pending_replace",
    "accepted_for_bidding", "calculated", "held", "pending_review",
}


@dataclass
class Quote:
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    timestamp: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2 if self.bid > 0 and self.ask > 0 else max(self.bid, self.ask)

    @property
    def spread_bps(self) -> float:
        m = self.mid
        if m <= 0 or self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            return float("inf")
        return (self.ask - self.bid) / m * 10_000


@dataclass
class Snapshot:
    symbol: str
    price: float
    timestamp: datetime  # time of the last trade/quote used for `price`
    prev_close: float
    day_open: float
    day_high: float
    day_low: float
    day_volume: float
    prev_day_volume: float
    minute_close: float
    minute_volume: float
    minute_time: Optional[datetime]
    quote: Optional[Quote] = None


@dataclass
class AssetInfo:
    symbol: str
    name: str
    exchange: str
    asset_class: str  # us_equity only is tradable here
    tradable: bool
    fractionable: bool
    shortable: bool = False
    status: str = "active"


@dataclass
class AccountInfo:
    account_number: str
    status: str
    currency: str
    equity: float
    last_equity: float
    cash: float
    buying_power: float
    non_marginable_buying_power: float
    portfolio_value: float
    long_market_value: float
    multiplier: float
    trading_blocked: bool
    account_blocked: bool
    is_paper: bool
    daytrade_count: Optional[int] = None
    pattern_day_trader: Optional[bool] = None
    accrued_fees: float = 0.0


@dataclass
class BrokerPosition:
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pl: float
    qty_available: float
    side: str = "long"
    asset_class: str = "us_equity"


@dataclass
class BrokerOrder:
    id: str
    client_order_id: str
    symbol: str
    side: Side
    type: str
    status: str
    qty: Optional[float]
    notional: Optional[float]
    filled_qty: float
    filled_avg_price: Optional[float]
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "day"
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    reject_reason: Optional[str] = None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_ORDER_STATES

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_ORDER_STATES


@dataclass
class OrderRequest:
    symbol: str
    side: Side
    type: OrderType
    client_order_id: str
    qty: Optional[float] = None
    notional: Optional[float] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "day"
    extended_hours: bool = False


@dataclass
class Signal:
    """A rules-based strategy's proposal to buy. Long-only."""
    symbol: str
    strategy: str
    time: datetime
    price: float
    stop_price: float
    target_price: float
    score: float  # 0..1 strength
    reasons: list[str] = field(default_factory=list)
    max_hold_minutes: int = 90
    features: dict = field(default_factory=dict)

    @property
    def stop_pct(self) -> float:
        return (self.price - self.stop_price) / self.price * 100 if self.price > 0 else 0.0

    @property
    def target_pct(self) -> float:
        return (self.target_price - self.price) / self.price * 100 if self.price > 0 else 0.0
