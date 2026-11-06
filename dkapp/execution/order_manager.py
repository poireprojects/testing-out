"""Idempotent order submission and order-state tracking.

Duplicate-order prevention, in layers:
1. Every order gets a deterministic client_order_id derived from (mode, day, trade, purpose,
   attempt) and is saved to the database *before* it is sent (write-ahead).
2. If the network fails mid-submit, we ask the broker for that client_order_id before
   ever retrying — the order may have arrived even though the response didn't.
3. The broker itself rejects a reused client_order_id; that rejection is treated as
   "the order exists" and the existing order is adopted.
4. The trading engine never opens a second entry for a symbol that has an open
   position or an active order.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from ..broker.base import Broker, BrokerUnavailable, DuplicateOrder, OrderRejected
from ..clock import ET
from ..models import BrokerOrder, FINAL_ORDER_STATES, OrderRequest
from ..storage.db import Database, from_iso, to_iso

log = logging.getLogger(__name__)

TERMINAL = FINAL_ORDER_STATES | {"abandoned"}

# Alpaca has acknowledged the order (it has a broker id) and it can still execute. Only an order in one
# of these states counts as a protective stop. "pending_submit" (our own write-ahead state: sent, no
# answer yet), "pending_cancel", "rejected", "canceled", "expired" and any unknown status do not.
WORKING_STATES = frozenset({"new", "accepted", "pending_new", "partially_filled", "accepted_for_bidding",
                            "pending_replace"})
# We don't know yet whether the order exists / is still live at the broker: never place a second
# protective order on top of one of these, and never treat them as protection either.
IN_FLIGHT_STATES = frozenset({"pending_submit", "pending_cancel"})


def is_working(row: dict) -> bool:
    return bool(row.get("broker_order_id")) and row.get("status") in WORKING_STATES


@dataclass
class FillDelta:
    order_id: int
    trade_id: Optional[int]
    purpose: str
    side: str
    qty: float
    price: float
    time: datetime


class OrderManager:
    def __init__(self, db: Database, broker: Broker, now: Callable[[], datetime], mode: str,
                 retry_delays: tuple[float, ...] = (0.5, 1.5), sleep: Callable[[float], None] = time.sleep):
        self.db = db
        self.broker = broker
        self.now = now
        self.mode = mode
        self.retry_delays = retry_delays
        self.sleep = sleep
        self.tag = self._install_tag()

    def _install_tag(self) -> str:
        """A short random id for this database, inside every client_order_id. Two copies of the app (or a
        test) on the same brokerage account can then never collide on an order id, and each copy only
        ever cleans up its own leftover orders."""
        row = self.db.query_one("SELECT value FROM meta WHERE key='order_tag'")
        if row and row["value"]:
            return row["value"]
        import secrets
        tag = secrets.token_hex(2)
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('order_tag', ?)", (tag,))
        return tag

    @property
    def prefix(self) -> str:
        return f"dk{self.mode[0]}{self.tag}-"

    # ------------------------------------------------------------ ids
    def client_order_id(self, trade_id: int, purpose: str) -> str:
        attempt = int(self.db.scalar("SELECT COUNT(*) FROM orders WHERE trade_id=? AND purpose=?", (trade_id, purpose), 0)) + 1
        day = self.now().astimezone(ET).strftime("%y%m%d")
        return f"{self.prefix}{day}-t{trade_id}-{purpose}-{attempt}"

    # ------------------------------------------------------------ submit
    def place(self, trade_id: int, purpose: str, req_kwargs: dict) -> dict:
        """Write-ahead, then submit. Returns the order row (status may be 'pending_submit'
        if the broker couldn't be reached; reconciliation resolves it later)."""
        cid = self.client_order_id(trade_id, purpose)
        now = self.now()
        row_id = self.db.insert(
            "orders", trade_id=trade_id, purpose=purpose, client_order_id=cid, symbol=req_kwargs["symbol"],
            side=req_kwargs["side"], type=req_kwargs["type"], qty=req_kwargs.get("qty"),
            notional=req_kwargs.get("notional"), limit_price=req_kwargs.get("limit_price"),
            stop_price=req_kwargs.get("stop_price"), status="pending_submit", created_at=to_iso(now))
        req = OrderRequest(client_order_id=cid, **req_kwargs)
        attempts = 0
        delays = list(self.retry_delays)
        while True:
            attempts += 1
            self.db.update("orders", row_id, attempts=attempts)
            try:
                bo = self.broker.submit_order(req)
                self._apply(row_id, bo)
                break
            except DuplicateOrder:
                bo = self._lookup(cid)
                if bo is not None:
                    self._apply(row_id, bo)
                break
            except OrderRejected as exc:
                self.db.update("orders", row_id, status="rejected", reject_reason=str(exc)[:500],
                               updated_at=to_iso(self.now()))
                break
            except BrokerUnavailable as exc:
                log.warning("submit %s failed (%s); checking whether it reached the broker", cid, exc)
                bo = self._lookup(cid)
                if bo is not None:
                    self._apply(row_id, bo)
                    break
                if not delays:
                    self.db.update("orders", row_id, reject_reason=f"unconfirmed: {exc}"[:500])
                    break
                self.sleep(delays.pop(0))
        return self.db.query_one("SELECT * FROM orders WHERE id=?", (row_id,))

    def _lookup(self, cid: str) -> Optional[BrokerOrder]:
        """Best-effort lookup used while submitting: None means "not found OR couldn't ask" — safe
        there, because any retry reuses the same client_order_id (the broker refuses duplicates)."""
        try:
            return self.broker.get_order_by_client_id(cid)
        except BrokerUnavailable:
            return None

    def _apply(self, row_id: int, bo: BrokerOrder) -> None:
        self.db.update("orders", row_id, broker_order_id=bo.id, status=bo.status,
                       submitted_at=to_iso(bo.submitted_at or self.now()), updated_at=to_iso(self.now()),
                       reject_reason=bo.reject_reason)
        # Fills are recorded by sync() so that every fill passes through one code path.

    # ------------------------------------------------------------ sync
    def sync(self, row: dict) -> tuple[dict, Optional[FillDelta]]:
        """Refresh one order from the broker; returns (row, new fill since last sync)."""
        if row["status"] in TERMINAL and row["status"] != "pending_submit":
            # still need to account for fills recorded at the broker but not locally
            pass
        bo: Optional[BrokerOrder] = None
        if row.get("broker_order_id"):
            bo = self.broker.get_order(row["broker_order_id"])
        if bo is None:
            # BrokerUnavailable propagates: "couldn't ask" must never be mistaken for "not there"
            bo = self.broker.get_order_by_client_id(row["client_order_id"])
        if bo is None:
            if row["status"] == "pending_submit":
                created = from_iso(row["created_at"]) or self.now()
                if self.now() - created > timedelta(seconds=90):
                    # the broker answered and confirmed it has no order with this id
                    self.db.update("orders", row["id"], status="abandoned", updated_at=to_iso(self.now()),
                                   reject_reason=(row.get("reject_reason") or "") + " | broker confirmed it never arrived")
            return self.db.query_one("SELECT * FROM orders WHERE id=?", (row["id"],)), None
        old_qty = float(row.get("filled_qty") or 0.0)
        old_px = float(row.get("filled_avg_price") or 0.0)
        new_qty = float(bo.filled_qty or 0.0)
        new_px = float(bo.filled_avg_price or 0.0)
        delta: Optional[FillDelta] = None
        if new_qty > old_qty + 1e-9:
            dq = new_qty - old_qty
            dp = (new_px * new_qty - old_px * old_qty) / dq if dq > 0 else new_px
            ftime = bo.filled_at or bo.updated_at or self.now()
            delta = FillDelta(row["id"], row.get("trade_id"), row["purpose"], row["side"], dq, dp, ftime)
            self.db.insert("fills", order_id=row["id"], trade_id=row.get("trade_id"), symbol=row["symbol"],
                           side=row["side"], qty=dq, price=dp, time=to_iso(ftime))
        self.db.update("orders", row["id"], broker_order_id=bo.id, status=bo.status, filled_qty=new_qty,
                       filled_avg_price=new_px if new_qty > 0 else None, updated_at=to_iso(self.now()))
        return self.db.query_one("SELECT * FROM orders WHERE id=?", (row["id"],)), delta

    def cancel(self, row: dict) -> None:
        if row.get("broker_order_id") and row["status"] not in TERMINAL:
            self.broker.cancel_order(row["broker_order_id"])

    def working(self, trade_id: int, purpose: str) -> list[dict]:
        """Orders the broker has confirmed and that can still execute."""
        return [r for r in self.active(trade_id=trade_id, purpose=purpose) if is_working(r)]

    def in_flight(self, trade_id: int, purpose: str) -> list[dict]:
        """Orders whose existence/state at the broker isn't confirmed yet."""
        return [r for r in self.active(trade_id=trade_id, purpose=purpose) if not is_working(r)]

    def active(self, trade_id: Optional[int] = None, purpose: Optional[str] = None) -> list[dict]:
        rows = self.db.active_orders()
        if trade_id is not None:
            rows = [r for r in rows if r.get("trade_id") == trade_id]
        if purpose is not None:
            rows = [r for r in rows if r["purpose"] == purpose]
        return rows
