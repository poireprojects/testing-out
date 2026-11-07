from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Protocol

from ..clock import SessionDay
from ..models import AccountInfo, AssetInfo, BrokerOrder, BrokerPosition, OrderRequest


class BrokerError(Exception):
    pass


class BrokerUnavailable(BrokerError):
    """Network failure, timeout, rate limit or server error — safe to retry later."""


class OrderRejected(BrokerError):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class DuplicateOrder(OrderRejected):
    """The client_order_id was already used — the original order exists at the broker."""


class NotFound(BrokerError):
    pass


class Broker(Protocol):
    name: str
    is_paper: bool

    def get_account(self) -> AccountInfo: ...
    def get_positions(self) -> list[BrokerPosition]: ...
    def get_open_orders(self) -> list[BrokerOrder]: ...
    def get_recent_orders(self, after: datetime) -> list[BrokerOrder]: ...
    def get_order(self, order_id: str) -> Optional[BrokerOrder]: ...
    def get_order_by_client_id(self, client_order_id: str) -> Optional[BrokerOrder]: ...
    def submit_order(self, req: OrderRequest) -> BrokerOrder: ...
    def cancel_order(self, order_id: str) -> None: ...
    def get_calendar(self, start: date, end: date) -> list[SessionDay]: ...
    def get_assets(self) -> list[AssetInfo]: ...
    def tick(self, now: datetime) -> None: ...
