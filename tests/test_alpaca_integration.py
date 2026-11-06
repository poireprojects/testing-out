"""Real checks against Alpaca's PAPER trading environment (never live).

    ALPACA_PAPER_KEY / ALPACA_PAPER_SECRET   paper key pair (or a placeholder when a proxy injects them)
    ALPACA_PAPER_ORDER_TEST=1                also place real PAPER orders (fake money; a few dollars of SPY)

Hard guards: the adapter is created with paper=True, the client must point at paper-api.alpaca.markets,
the account number must start with "PA", and a key that looks like a live key (AK...) is refused.
Order tests only ever sell back what they bought themselves; other positions on the account are left
alone. Tests that need a fill skip (with a reason) while the market is closed.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from dkapp.credentials import AlpacaKeys

pytestmark = pytest.mark.integration
KEY, SECRET = os.environ.get("ALPACA_PAPER_KEY"), os.environ.get("ALPACA_PAPER_SECRET")
ORDERS = os.environ.get("ALPACA_PAPER_ORDER_TEST") == "1"
needs_keys = pytest.mark.skipif(not (KEY and SECRET), reason="no Alpaca paper credentials in the environment")
needs_orders = pytest.mark.skipif(not ORDERS, reason="paper order tests not requested (ALPACA_PAPER_ORDER_TEST=1)")
SYMBOL = "SPY"
TINY_QTY = 0.013  # fractional: a few dollars of SPY, paper money


def cid(tag: str) -> str:
    return f"dkitest-{tag}-{uuid.uuid4().hex[:10]}"


@pytest.fixture(scope="module")
def broker():
    from dkapp.broker.alpaca_broker import AlpacaBroker
    if (KEY or "").upper().startswith("AK"):
        pytest.fail("refusing to run: that looks like a LIVE key id (live keys start with AK)")
    b = AlpacaBroker(AlpacaKeys(KEY, SECRET), paper=True)
    raw = getattr(b.client, "_base_url", "")
    base = str(getattr(raw, "value", raw))
    assert "paper-api.alpaca.markets" in base, f"not the paper endpoint: {base}"
    ok, msg = b.verify_environment()
    assert ok, msg
    assert b.get_account().account_number.upper().startswith("PA")
    return b


@pytest.fixture(scope="module")
def market_open(broker):
    return bool(broker.client.get_clock().is_open)


def wait_for(broker, order_id, statuses, timeout=30.0):
    end = time.monotonic() + timeout
    o = broker.get_order(order_id)
    while o is not None and o.status not in statuses and time.monotonic() < end:
        time.sleep(0.7)
        o = broker.get_order(order_id)
    return o


def position_qty(broker, symbol):
    return next((p.qty for p in broker.get_positions() if p.symbol == symbol), 0.0)


# ---------------------------------------------------------------- account, data, guards
@needs_keys
def test_paper_account_is_used_and_sizing_never_borrows(broker):
    from dkapp.config import AppSettings
    from dkapp.risk.sizing import SizingInputs, spendable_cash
    acct = broker.get_account()
    assert acct.is_paper and acct.status == "active" and not acct.trading_blocked
    assert acct.multiplier >= 1
    # the app never spends more than the cash in the account, whatever buying power the broker offers
    inp = SizingInputs(equity=acct.equity, settled_cash=0.0, broker_cash=acct.cash,
                       broker_buying_power=min(acct.buying_power, acct.non_marginable_buying_power or acct.buying_power))
    assert spendable_cash(AppSettings(), inp) <= acct.cash + 1e-6


@needs_keys
def test_calendar_assets_and_positions(broker):
    today = datetime.now(timezone.utc).date()
    days = broker.get_calendar(today - timedelta(days=10), today + timedelta(days=10))
    assert days and all(d.open < d.close for d in days)
    spy = next(a for a in broker.get_assets() if a.symbol == SYMBOL)
    assert spy.tradable and spy.fractionable
    assert isinstance(broker.get_positions(), list)
    assert isinstance(broker.get_open_orders(), list)


@needs_keys
def test_unknown_order_is_confirmed_not_found(broker):
    """The stop-protection logic relies on this: 'not found' is an answer, a network error is not."""
    assert broker.get_order_by_client_id(cid("missing")) is None
    assert broker.get_order(str(uuid.uuid4())) is None


@needs_keys
def test_market_data_snapshot_and_bars():
    from dkapp.data.alpaca_data import AlpacaMarketData
    md = AlpacaMarketData(AlpacaKeys(KEY, SECRET), feed="iex")
    try:
        snaps = md.get_snapshots([SYMBOL, "AAPL"])
    except Exception as exc:  # the data API needs its own credentials
        pytest.skip(f"market-data API not reachable with these credentials: {exc}")
    assert snaps[SYMBOL].price > 0
    bars = md.get_intraday_bars([SYMBOL], datetime.now(timezone.utc) - timedelta(days=5),
                                datetime.now(timezone.utc) - timedelta(minutes=20))
    assert not bars[SYMBOL].empty


# ---------------------------------------------------------------- orders (paper money)
@needs_keys
@needs_orders
def test_fractional_limit_order_submit_lookup_duplicate_cancel(broker):
    from dkapp.broker.base import DuplicateOrder
    from dkapp.execution.order_manager import is_working
    from dkapp.models import OrderRequest
    c = cid("limit")
    req = OrderRequest(SYMBOL, "buy", "limit", c, qty=TINY_QTY, limit_price=100.00)  # far below the market
    o = broker.submit_order(req)
    try:
        assert o.client_order_id == c and o.qty == pytest.approx(TINY_QTY)
        assert o.status in ("new", "accepted", "pending_new"), o.status
        assert is_working({"broker_order_id": o.id, "status": o.status})
        assert broker.get_order_by_client_id(c).id == o.id
        with pytest.raises(DuplicateOrder):  # the same client_order_id is refused, never placed twice
            broker.submit_order(req)
    finally:
        broker.cancel_order(o.id)
    done = wait_for(broker, o.id, {"canceled"})
    assert done.status == "canceled"
    assert not is_working({"broker_order_id": done.id, "status": done.status})


@needs_keys
@needs_orders
def test_rejections_are_mapped_and_explained(broker):
    from dkapp.broker.base import OrderRejected
    from dkapp.broker.explain import explain_rejection
    from dkapp.models import OrderRequest
    with pytest.raises(OrderRejected) as e1:
        broker.submit_order(OrderRequest("ZZZZQX", "buy", "market", cid("bad-symbol"), qty=1))
    assert explain_rejection(str(e1.value))


@needs_keys
@needs_orders
def test_fractional_buy_broker_stop_and_safe_exit(broker, market_open):
    """The real protective-stop path: buy a fraction, place a DAY stop, confirm the broker acknowledged it,
    raise it (cancel, then a higher one), then cancel and sell — ending exactly where we started."""
    if not market_open:
        pytest.skip("market is closed: fractional orders queue until the open")
    from dkapp.execution.order_manager import WORKING_STATES, is_working
    from dkapp.models import OrderRequest
    before = position_qty(broker, SYMBOL)
    buy = broker.submit_order(OrderRequest(SYMBOL, "buy", "market", cid("buy"), qty=TINY_QTY))
    buy = wait_for(broker, buy.id, {"filled"})
    assert buy.status == "filled" and buy.filled_qty == pytest.approx(TINY_QTY)
    px = buy.filled_avg_price
    stop = None
    try:
        stop = broker.submit_order(OrderRequest(SYMBOL, "sell", "stop", cid("stop"), qty=TINY_QTY,
                                                stop_price=round(px * 0.90, 2)))
        stop = wait_for(broker, stop.id, WORKING_STATES, timeout=10)
        assert is_working({"broker_order_id": stop.id, "status": stop.status}), stop.status
        broker.cancel_order(stop.id)
        assert wait_for(broker, stop.id, {"canceled"}).status == "canceled"
        stop = broker.submit_order(OrderRequest(SYMBOL, "sell", "stop", cid("stop2"), qty=TINY_QTY,
                                                stop_price=round(px * 0.95, 2)))
        stop = wait_for(broker, stop.id, WORKING_STATES, timeout=10)
        assert is_working({"broker_order_id": stop.id, "status": stop.status})
        assert stop.stop_price == pytest.approx(round(px * 0.95, 2))
    finally:
        if stop is not None:
            broker.cancel_order(stop.id)
            wait_for(broker, stop.id, {"canceled", "filled"})
        sell = broker.submit_order(OrderRequest(SYMBOL, "sell", "market", cid("sell"), qty=TINY_QTY))
        assert wait_for(broker, sell.id, {"filled"}).status == "filled"
    assert position_qty(broker, SYMBOL) == pytest.approx(before, abs=1e-9)


@needs_keys
@needs_orders
def test_engine_order_paths_against_the_real_paper_broker(broker, market_open, tmp_path):
    """The app's own code (order manager + engine protection + exit) end to end on the paper account."""
    if not market_open:
        pytest.skip("market is closed")
    from dkapp.ai.engine import AIEngine
    from dkapp.clock import MarketCalendar, RealClock, session_state
    from dkapp.config import AppSettings
    from dkapp.data.demo_data import DemoMarket, DemoMarketData
    from dkapp.engine.trading_engine import TradingEngine
    from dkapp.execution.order_manager import is_working
    from dkapp.models import OrderRequest
    from dkapp.storage.db import Database
    from tests.harness import ScriptedAI
    s = AppSettings()
    s.ai.decision_mode = "rules"
    clock, cal = RealClock(), MarketCalendar()
    db = Database(tmp_path / "paper.db", "paper")
    data = DemoMarketData(DemoMarket([SYMBOL], cal, 1), clock)  # (quotes aren't used by these code paths)
    eng = TradingEngine(s, "paper", broker, data, db, cal, clock, AIEngine(s, db, ScriptedAI()), universe=[SYMBOL])
    eng.orders.retry_delays = (0.5, 1.0)
    now = clock.now()
    eng.session = session_state(cal, now)
    eng.reconcile(now, force=True)
    before = position_qty(broker, SYMBOL)
    tid = db.insert("trades", symbol=SYMBOL, strategy="orb", mode="paper", status="pending_entry", managed=1,
                    session_date=now.date().isoformat(), created_at=now.isoformat(), stop_price=1.0,
                    initial_stop=1.0, target_price=10_000.0, max_hold_until=(now + timedelta(hours=1)).isoformat())
    try:
        row = eng.orders.place(tid, "entry", dict(symbol=SYMBOL, side="buy", type="market", qty=TINY_QTY))
        assert row["status"] != "rejected", row["reject_reason"]
        for _ in range(30):
            row, delta = eng.orders.sync(row)
            if delta:
                eng._on_fill(delta, clock.now())
            if row["status"] == "filled":
                break
            time.sleep(0.7)
        t = db.trade(tid)
        assert t["entry_qty"] == pytest.approx(TINY_QTY)
        db.update("trades", tid, stop_price=round(t["entry_price"] * 0.90, 2))
        eng.reconcile(clock.now(), force=True)
        eng._advance_trade(db.trade(tid), clock.now())     # -> open, broker stop placed
        stops = db.query("SELECT * FROM orders WHERE trade_id=? AND purpose='stop'", (tid,))
        assert len(stops) == 1 and is_working(stops[0]), stops
        assert eng.unprotected() == [], "a broker-confirmed stop must count as protection"
        assert db.trade(tid)["software_stop"] == 0
        eng._begin_exit(db.trade(tid), "integration test exit")
        for _ in range(40):
            eng.process_orders(clock.now())
            if db.trade(tid)["status"] == "closed":
                break
            time.sleep(0.7)
            eng.reconcile(clock.now(), force=True)
        t = db.trade(tid)
        assert t["status"] == "closed", t
        assert all(o["status"] in ("canceled", "filled") for o in
                   db.query("SELECT * FROM orders WHERE trade_id=?", (tid,)))
    finally:
        for o in broker.get_open_orders():
            if o.client_order_id.startswith("dkp-") and o.symbol == SYMBOL:
                broker.cancel_order(o.id)
        time.sleep(1.0)
        extra = position_qty(broker, SYMBOL) - before
        if extra > 1e-9:
            broker.submit_order(OrderRequest(SYMBOL, "sell", "market", cid("cleanup"), qty=round(extra, 9)))
    time.sleep(1.5)
    assert position_qty(broker, SYMBOL) == pytest.approx(before, abs=1e-9)
