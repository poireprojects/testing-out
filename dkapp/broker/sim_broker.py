"""Simulated broker for DEMO mode and automated tests.

Behaves like a small Alpaca cash-style account: no margin, no short selling, DAY
orders only, fills against the simulated bid/ask with slippage, regulatory fee
estimates on sales, open sell orders reserve shares ("insufficient qty available"),
and optional failure injection (network errors, rejections, partial fills).
"""
from __future__ import annotations

import itertools
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Optional

from ..clock import Clock, MarketCalendar, SessionDay, session_state
from ..models import AccountInfo, AssetInfo, BrokerOrder, BrokerPosition, OrderRequest, Quote
from ..risk.fees import CostModel
from .base import BrokerUnavailable, DuplicateOrder, OrderRejected

QuoteFn = Callable[[str, datetime], Optional[Quote]]


@dataclass
class _Pos:
    qty: float
    avg: float


@dataclass
class SimState:
    cash: float
    positions: dict = field(default_factory=dict)
    orders: dict = field(default_factory=dict)
    seq: int = 0
    fees_paid: float = 0.0


class SimBroker:
    is_paper = True

    def __init__(self, quote_fn: QuoteFn, clock: Clock, calendar: MarketCalendar, starting_cash: float = 100.0,
                 cost_model: Optional[CostModel] = None, symbols: Optional[list[str]] = None,
                 state: Optional[dict] = None, name: str = "demo simulator"):
        self.name = name
        self.quote_fn = quote_fn
        self.clock = clock
        self.calendar = calendar
        self.costs = cost_model or CostModel()
        self.symbols = set(symbols or [])
        self._lock = threading.RLock()
        self.state = SimState(cash=starting_cash)
        if state:
            self.load_state(state)
        # failure injection (tests)
        self.fail_next_calls = 0
        self.fail_after_submit = False   # order accepted, but the response is "lost"
        self.reject_next: Optional[str] = None
        self.partial_fill_ratio: Optional[float] = None
        self.reject_stop_orders = False
        self.calls = 0

    # ------------------------------------------------------------ persistence
    def dump_state(self) -> dict:
        with self._lock:
            return {"cash": self.state.cash, "positions": {k: asdict(v) for k, v in self.state.positions.items()},
                    "orders": self.state.orders, "seq": self.state.seq, "fees_paid": self.state.fees_paid}

    def load_state(self, d: dict) -> None:
        with self._lock:
            self.state = SimState(cash=float(d.get("cash", 0.0)),
                                  positions={k: _Pos(**v) for k, v in d.get("positions", {}).items()},
                                  orders=dict(d.get("orders", {})), seq=int(d.get("seq", 0)),
                                  fees_paid=float(d.get("fees_paid", 0.0)))

    # ------------------------------------------------------------ helpers
    def _maybe_fail(self) -> None:
        self.calls += 1
        if self.fail_next_calls > 0:
            self.fail_next_calls -= 1
            raise BrokerUnavailable("simulated network failure")

    def _order_obj(self, o: dict) -> BrokerOrder:
        return BrokerOrder(
            id=o["id"], client_order_id=o["client_order_id"], symbol=o["symbol"], side=o["side"], type=o["type"],
            status=o["status"], qty=o["qty"], notional=None, filled_qty=o["filled_qty"],
            filled_avg_price=o["filled_avg_price"], limit_price=o.get("limit_price"), stop_price=o.get("stop_price"),
            time_in_force="day", submitted_at=_dt(o["submitted_at"]), filled_at=_dt(o.get("filled_at")),
            updated_at=_dt(o.get("updated_at")), reject_reason=o.get("reject_reason"))

    def _reserved_sell_qty(self, symbol: str) -> float:
        return sum(o["qty"] - o["filled_qty"] for o in self.state.orders.values()
                   if o["symbol"] == symbol and o["side"] == "sell" and o["status"] in ("new", "partially_filled"))

    def _reserved_buy_cash(self) -> float:
        tot = 0.0
        for o in self.state.orders.values():
            if o["side"] == "buy" and o["status"] in ("new", "partially_filled"):
                px = o.get("limit_price") or o.get("ref_price") or 0.0
                tot += (o["qty"] - o["filled_qty"]) * px
        return tot

    def _session(self, now: datetime):
        return session_state(self.calendar, now, no_entries_first_minutes=0, last_entry_minutes_before_close=0,
                             flatten_minutes_before_close=0)

    # ------------------------------------------------------------ account
    def get_account(self) -> AccountInfo:
        self._maybe_fail()
        with self._lock:
            now = self.clock.now()
            mv = 0.0
            for sym, p in self.state.positions.items():
                q = self.quote_fn(sym, now)
                mv += p.qty * (q.bid if q else p.avg)
            equity = self.state.cash + mv
            bp = max(0.0, self.state.cash - self._reserved_buy_cash())
            return AccountInfo(account_number="DEMO-SIM", status="active", currency="USD", equity=equity,
                               last_equity=equity, cash=self.state.cash, buying_power=bp,
                               non_marginable_buying_power=bp, portfolio_value=equity, long_market_value=mv,
                               multiplier=1.0, trading_blocked=False, account_blocked=False, is_paper=True)

    def get_positions(self) -> list[BrokerPosition]:
        self._maybe_fail()
        with self._lock:
            now = self.clock.now()
            out = []
            for sym, p in self.state.positions.items():
                q = self.quote_fn(sym, now)
                px = q.bid if q else p.avg
                out.append(BrokerPosition(sym, p.qty, p.avg, px, p.qty * px, (px - p.avg) * p.qty,
                                          max(0.0, p.qty - self._reserved_sell_qty(sym))))
            return out

    # ------------------------------------------------------------ orders
    def get_open_orders(self) -> list[BrokerOrder]:
        self._maybe_fail()
        with self._lock:
            return [self._order_obj(o) for o in self.state.orders.values() if o["status"] in ("new", "partially_filled")]

    def get_recent_orders(self, after: datetime) -> list[BrokerOrder]:
        self._maybe_fail()
        with self._lock:
            return [self._order_obj(o) for o in self.state.orders.values() if _dt(o["submitted_at"]) >= after]

    def get_order(self, order_id: str) -> Optional[BrokerOrder]:
        self._maybe_fail()
        with self._lock:
            o = self.state.orders.get(order_id)
            return self._order_obj(o) if o else None

    def get_order_by_client_id(self, client_order_id: str) -> Optional[BrokerOrder]:
        self._maybe_fail()
        with self._lock:
            for o in self.state.orders.values():
                if o["client_order_id"] == client_order_id:
                    return self._order_obj(o)
            return None

    def submit_order(self, req: OrderRequest) -> BrokerOrder:
        self._maybe_fail()
        with self._lock:
            now = self.clock.now()
            if any(o["client_order_id"] == req.client_order_id for o in self.state.orders.values()):
                raise DuplicateOrder("client_order_id must be unique")
            if self.reject_next:
                msg, self.reject_next = self.reject_next, None
                raise OrderRejected(msg, 422)
            if req.time_in_force != "day":
                raise OrderRejected("fractional orders must be DAY orders", 422)
            if req.type == "stop" and self.reject_stop_orders:
                raise OrderRejected("stop orders are not supported for this order (simulated)", 422)
            if req.qty is None or req.qty <= 0:
                raise OrderRejected("qty must be > 0", 422)
            if self.symbols and req.symbol not in self.symbols:
                raise OrderRejected(f"asset {req.symbol} not found", 422)
            if not self._session(now).is_open:
                raise OrderRejected("market is closed (simulator only accepts orders during the session)", 422)
            q = self.quote_fn(req.symbol, now)
            if q is None:
                raise OrderRejected("no quote", 422)
            if req.side == "sell":
                pos = self.state.positions.get(req.symbol)
                avail = (pos.qty if pos else 0.0) - self._reserved_sell_qty(req.symbol)
                if req.qty > avail + 1e-9:
                    raise OrderRejected(f"insufficient qty available for order (requested {req.qty}, available {avail})", 403)
            else:
                ref = req.limit_price or q.ask
                cost = req.qty * ref
                if cost > self.state.cash - self._reserved_buy_cash() + 1e-9:
                    raise OrderRejected("insufficient buying power", 403)
            self.state.seq += 1
            oid = f"sim-{self.state.seq:06d}"
            o = {"id": oid, "client_order_id": req.client_order_id, "symbol": req.symbol, "side": req.side,
                 "type": req.type, "status": "new", "qty": float(req.qty), "filled_qty": 0.0,
                 "filled_avg_price": None, "limit_price": req.limit_price, "stop_price": req.stop_price,
                 "ref_price": q.ask, "submitted_at": now.isoformat(), "updated_at": now.isoformat()}
            self.state.orders[oid] = o
            self._try_fill(o, now)
            if self.fail_after_submit:
                self.fail_after_submit = False
                raise BrokerUnavailable("simulated timeout after the order reached the broker")
            return self._order_obj(o)

    def cancel_order(self, order_id: str) -> None:
        self._maybe_fail()
        with self._lock:
            o = self.state.orders.get(order_id)
            if o and o["status"] in ("new", "partially_filled"):
                o["status"] = "canceled"
                o["updated_at"] = self.clock.now().isoformat()

    # ------------------------------------------------------------ matching
    def tick(self, now: datetime) -> None:
        with self._lock:
            sess = self._session(now)
            for o in list(self.state.orders.values()):
                if o["status"] not in ("new", "partially_filled"):
                    continue
                if not sess.is_open:
                    o["status"] = "expired"  # DAY orders end with the session
                    o["updated_at"] = now.isoformat()
                    continue
                self._try_fill(o, now)

    def _try_fill(self, o: dict, now: datetime) -> None:
        q = self.quote_fn(o["symbol"], now)
        if q is None:
            return
        side, typ = o["side"], o["type"]
        price: Optional[float] = None
        if typ == "market":
            price = self.costs.fill_price(side, q.ask if side == "buy" else q.bid)
        elif typ == "limit":
            if side == "buy" and q.ask <= o["limit_price"]:
                price = min(o["limit_price"], self.costs.fill_price("buy", q.ask))
            elif side == "sell" and q.bid >= o["limit_price"]:
                price = max(o["limit_price"], self.costs.fill_price("sell", q.bid))
        elif typ == "stop":
            if side == "sell" and q.bid <= o["stop_price"]:
                price = self.costs.fill_price("sell", q.bid)
            elif side == "buy" and q.ask >= o["stop_price"]:
                price = self.costs.fill_price("buy", q.ask)
        if price is None:
            return
        remaining = o["qty"] - o["filled_qty"]
        fill_qty = remaining
        if self.partial_fill_ratio is not None and o["filled_qty"] == 0:
            fill_qty = round(remaining * self.partial_fill_ratio, 6)
        if fill_qty <= 0:
            return
        self._apply_fill(o, fill_qty, round(price, 4), now)

    def _apply_fill(self, o: dict, qty: float, price: float, now: datetime) -> None:
        sym = o["symbol"]
        if o["side"] == "buy":
            cost = qty * price
            self.state.cash -= cost
            pos = self.state.positions.get(sym)
            if pos:
                new_qty = pos.qty + qty
                pos.avg = (pos.avg * pos.qty + cost) / new_qty
                pos.qty = new_qty
            else:
                self.state.positions[sym] = _Pos(qty, price)
        else:
            pos = self.state.positions[sym]
            proceeds = qty * price
            fees = self.costs.sell_fees(proceeds, qty)
            self.state.cash += proceeds - fees
            self.state.fees_paid += fees
            pos.qty = round(pos.qty - qty, 9)
            if pos.qty <= 1e-9:
                del self.state.positions[sym]
        prev = o["filled_qty"]
        o["filled_qty"] = round(prev + qty, 9)
        prev_px = o["filled_avg_price"] or 0.0
        o["filled_avg_price"] = (prev_px * prev + price * qty) / o["filled_qty"]
        o["status"] = "filled" if o["filled_qty"] >= o["qty"] - 1e-9 else "partially_filled"
        o["updated_at"] = now.isoformat()
        if o["status"] == "filled":
            o["filled_at"] = now.isoformat()

    # ------------------------------------------------------------ reference data
    def get_calendar(self, start: date, end: date) -> list[SessionDay]:
        out = []
        for d in self.calendar.trading_days_between(start, end):
            s = self.calendar.session(d)
            if s:
                out.append(s)
        return out

    def get_assets(self) -> list[AssetInfo]:
        return [AssetInfo(s, s, "NASDAQ", "us_equity", True, True) for s in sorted(self.symbols)]


def _dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
