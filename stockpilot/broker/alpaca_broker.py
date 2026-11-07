"""Alpaca brokerage adapter (alpaca-py `TradingClient`).

Paper and live use different API keys and different hosts; the `paper` flag is fixed
when the adapter is created and cannot change afterwards. A paper adapter refuses to
start against an account that doesn't look like a paper account (and vice-versa).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from ..clock import ET, SessionDay
from ..credentials import AlpacaKeys
from ..models import AccountInfo, AssetInfo, BrokerOrder, BrokerPosition, OrderRequest
from .base import BrokerUnavailable, DuplicateOrder, NotFound, OrderRejected

log = logging.getLogger(__name__)


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x) if x is not None else default
    except (TypeError, ValueError):
        return default


def _enum(v) -> str:
    return str(getattr(v, "value", v) or "").lower()


def convert_order(o) -> BrokerOrder:
    return BrokerOrder(
        id=str(o.id),
        client_order_id=str(o.client_order_id or ""),
        symbol=o.symbol,
        side=_enum(o.side),  # type: ignore[arg-type]
        type=_enum(getattr(o, "order_type", None) or o.type),
        status=_enum(o.status),
        qty=_f(o.qty, None) if o.qty is not None else None,
        notional=_f(o.notional, None) if o.notional is not None else None,
        filled_qty=_f(o.filled_qty),
        filled_avg_price=_f(o.filled_avg_price, None) if o.filled_avg_price is not None else None,
        limit_price=_f(o.limit_price, None) if o.limit_price is not None else None,
        stop_price=_f(o.stop_price, None) if o.stop_price is not None else None,
        time_in_force=_enum(o.time_in_force),
        submitted_at=o.submitted_at,
        filled_at=o.filled_at,
        updated_at=o.updated_at,
    )


class AlpacaBroker:
    def __init__(self, keys: AlpacaKeys, paper: bool, client=None):
        from alpaca.trading.client import TradingClient

        self.is_paper = bool(paper)
        self.name = "Alpaca paper" if self.is_paper else "Alpaca LIVE"
        self.client = client or TradingClient(keys.key_id, keys.secret, paper=self.is_paper)

    # ----------------------------------------------------------- error mapping
    def _call(self, fn, *args, **kwargs):
        from alpaca.common.exceptions import APIError

        try:
            import requests
            net_errors: tuple = (requests.ConnectionError, requests.Timeout)
        except ImportError:  # pragma: no cover
            net_errors = ()
        try:
            return fn(*args, **kwargs)
        except APIError as exc:
            sc = exc.status_code
            msg = str(exc)
            try:
                msg = exc.message
            except Exception:
                pass
            if sc is None or sc >= 500 or sc == 429:
                raise BrokerUnavailable(f"Alpaca unavailable ({sc}): {msg}") from exc
            if sc == 404:
                raise NotFound(msg) from exc
            if "client_order_id" in msg.lower() and "unique" in msg.lower():
                raise DuplicateOrder(msg, sc) from exc
            raise OrderRejected(msg, sc) from exc
        except net_errors as exc:
            raise BrokerUnavailable(f"network error talking to Alpaca: {exc}") from exc

    # ----------------------------------------------------------- account
    def get_account(self) -> AccountInfo:
        a = self._call(self.client.get_account)
        info = AccountInfo(
            account_number=str(a.account_number),
            status=_enum(a.status),
            currency=a.currency or "USD",
            equity=_f(a.equity),
            last_equity=_f(a.last_equity),
            cash=_f(a.cash),
            buying_power=_f(a.buying_power),
            non_marginable_buying_power=_f(a.non_marginable_buying_power, _f(a.cash)),
            portfolio_value=_f(a.portfolio_value, _f(a.equity)),
            long_market_value=_f(a.long_market_value),
            multiplier=_f(a.multiplier, 1.0),
            trading_blocked=bool(a.trading_blocked),
            account_blocked=bool(a.account_blocked),
            is_paper=self.is_paper,
            daytrade_count=a.daytrade_count,
            pattern_day_trader=a.pattern_day_trader,
            accrued_fees=_f(a.accrued_fees),
        )
        return info

    def get_positions(self) -> list[BrokerPosition]:
        out = []
        for p in self._call(self.client.get_all_positions):
            out.append(BrokerPosition(
                symbol=p.symbol, qty=_f(p.qty), avg_entry_price=_f(p.avg_entry_price),
                current_price=_f(p.current_price), market_value=_f(p.market_value),
                unrealized_pl=_f(p.unrealized_pl), qty_available=_f(p.qty_available, _f(p.qty)),
                side=_enum(p.side), asset_class=_enum(p.asset_class)))
        return out

    # ----------------------------------------------------------- orders
    def get_open_orders(self) -> list[BrokerOrder]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest
        res = self._call(self.client.get_orders, GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500))
        return [convert_order(o) for o in res]

    def get_recent_orders(self, after: datetime) -> list[BrokerOrder]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest
        res = self._call(self.client.get_orders,
                         GetOrdersRequest(status=QueryOrderStatus.ALL, after=after, limit=500, direction="desc"))
        return [convert_order(o) for o in res]

    def get_order(self, order_id: str) -> Optional[BrokerOrder]:
        try:
            return convert_order(self._call(self.client.get_order_by_id, order_id))
        except NotFound:
            return None

    def get_order_by_client_id(self, client_order_id: str) -> Optional[BrokerOrder]:
        try:
            return convert_order(self._call(self.client.get_order_by_client_id, client_order_id))
        except NotFound:
            return None

    def submit_order(self, req: OrderRequest) -> BrokerOrder:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, StopOrderRequest

        if req.time_in_force != "day":
            raise OrderRejected("only DAY orders are used (required for fractional shares)")
        side = OrderSide.BUY if req.side == "buy" else OrderSide.SELL
        common = dict(symbol=req.symbol, side=side, time_in_force=TimeInForce.DAY, client_order_id=req.client_order_id,
                      extended_hours=False)
        if req.qty is not None:
            common["qty"] = round(req.qty, 9)
        elif req.notional is not None and req.type == "market":
            common["notional"] = round(req.notional, 2)
        else:
            raise OrderRejected("order needs a quantity")
        if req.type == "market":
            r = MarketOrderRequest(**common)
        elif req.type == "limit":
            r = LimitOrderRequest(limit_price=req.limit_price, **common)
        elif req.type == "stop":
            r = StopOrderRequest(stop_price=req.stop_price, **common)
        else:
            raise OrderRejected(f"unsupported order type {req.type}")
        return convert_order(self._call(self.client.submit_order, r))

    def cancel_order(self, order_id: str) -> None:
        try:
            self._call(self.client.cancel_order_by_id, order_id)
        except NotFound:
            pass
        except OrderRejected as exc:  # 422: already filled/canceled
            log.info("cancel %s not possible: %s", order_id, exc)

    # ----------------------------------------------------------- reference data
    def get_calendar(self, start: date, end: date) -> list[SessionDay]:
        from alpaca.trading.requests import GetCalendarRequest
        days = self._call(self.client.get_calendar, GetCalendarRequest(start=start, end=end))
        out = []
        for d in days:
            o = d.open if d.open.tzinfo else d.open.replace(tzinfo=ET)
            c = d.close if d.close.tzinfo else d.close.replace(tzinfo=ET)
            out.append(SessionDay(d.date, o, c))
        return out

    def get_assets(self) -> list[AssetInfo]:
        from alpaca.trading.enums import AssetClass, AssetStatus
        from alpaca.trading.requests import GetAssetsRequest
        assets = self._call(self.client.get_all_assets,
                            GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY))
        return [AssetInfo(symbol=a.symbol, name=a.name or "", exchange=_enum(a.exchange).upper(),
                          asset_class=_enum(a.asset_class), tradable=bool(a.tradable),
                          fractionable=bool(a.fractionable), shortable=bool(a.shortable), status=_enum(a.status))
                for a in assets]

    def get_fee_activities(self, after: datetime) -> list[dict]:
        """Regulatory fee debits (best effort; endpoint shape may vary)."""
        try:
            data = self._call(self.client.get, "/account/activities/FEE",
                              {"after": after.astimezone(timezone.utc).isoformat()})
            return list(data or [])
        except Exception as exc:
            log.info("fee activities unavailable: %s", exc)
            return []

    def tick(self, now: datetime) -> None:
        return None

    def verify_environment(self) -> tuple[bool, str]:
        """Paper accounts at Alpaca have account numbers starting with 'PA'."""
        acct = self.get_account()
        looks_paper = acct.account_number.upper().startswith("PA")
        if self.is_paper and not looks_paper:
            return False, "these keys belong to a LIVE account — save them under live trading instead"
        if not self.is_paper and looks_paper:
            return False, "these keys belong to a PAPER account — save them under paper trading instead"
        return True, f"connected to {'paper' if self.is_paper else 'live'} account {acct.account_number}"
