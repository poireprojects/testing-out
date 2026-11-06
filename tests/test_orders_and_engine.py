from datetime import date, datetime, timedelta, timezone

import pytest

from dkapp.ai.engine import AIEngine
from dkapp.broker.base import BrokerUnavailable, DuplicateOrder
from dkapp.clock import ET
from dkapp.engine.trading_engine import TradingEngine
from dkapp.execution.order_manager import OrderManager
from dkapp.models import OrderRequest
from tests.harness import ScriptedAI, enter, make_rig, override_price

DAY = date(2026, 11, 4)


def at(h, m):
    return datetime(2026, 11, 4, h, m, tzinfo=ET).astimezone(timezone.utc)


@pytest.fixture
def rig(tmp_path):
    r = make_rig(tmp_path, start_offset_min=90, universe_size=80)  # 11:00 ET, market open
    r.engine.step()
    return r


def broker_orders(rig, **filt):
    out = list(rig.broker.state.orders.values())
    for k, v in filt.items():
        out = [o for o in out if o[k] == v]
    return out


# ================================================================ order manager
def test_write_ahead_then_submit(rig):
    om = OrderManager(rig.db, rig.broker, rig.clock.now, "demo", retry_delays=(), sleep=lambda s: None)
    tid = rig.db.insert("trades", symbol="SPY", strategy="orb", mode="demo", status="pending_entry",
                        session_date=DAY.isoformat(), created_at=rig.clock.now().isoformat())
    row = om.place(tid, "entry", dict(symbol="SPY", side="buy", type="market", qty=0.01))
    assert row["client_order_id"] == f"{rig.engine.orders.prefix}261104-t{tid}-entry-1"
    assert rig.engine.orders.prefix.startswith("dkd") and len(rig.engine.orders.tag) == 4
    assert row["broker_order_id"] and row["status"] == "filled"
    assert len(broker_orders(rig)) == 1


def test_lost_response_is_not_resubmitted(rig):
    om = OrderManager(rig.db, rig.broker, rig.clock.now, "demo", retry_delays=(0.0, 0.0), sleep=lambda s: None)
    tid = rig.db.insert("trades", symbol="SPY", strategy="orb", mode="demo", status="pending_entry",
                        session_date=DAY.isoformat(), created_at=rig.clock.now().isoformat())
    rig.broker.fail_after_submit = True  # broker accepts the order, but the reply never arrives
    row = om.place(tid, "entry", dict(symbol="SPY", side="buy", type="market", qty=0.01))
    assert len(broker_orders(rig)) == 1, "a lost response must never create a second order"
    assert row["broker_order_id"] is not None


def test_network_down_leaves_unconfirmed_order_then_abandons(rig):
    om = OrderManager(rig.db, rig.broker, rig.clock.now, "demo", retry_delays=(0.0,), sleep=lambda s: None)
    tid = rig.db.insert("trades", symbol="SPY", strategy="orb", mode="demo", status="pending_entry",
                        session_date=DAY.isoformat(), created_at=rig.clock.now().isoformat())
    rig.broker.fail_next_calls = 10
    row = om.place(tid, "entry", dict(symbol="SPY", side="buy", type="market", qty=0.01))
    assert row["status"] == "pending_submit" and "unconfirmed" in row["reject_reason"]
    rig.broker.fail_next_calls = 0
    rig.clock.advance(120)
    row, delta = om.sync(row)
    assert row["status"] == "abandoned" and delta is None
    assert broker_orders(rig) == []


def test_duplicate_client_order_id_is_adopted(rig):
    om = OrderManager(rig.db, rig.broker, rig.clock.now, "demo", retry_delays=(), sleep=lambda s: None)
    tid = rig.db.insert("trades", symbol="SPY", strategy="orb", mode="demo", status="pending_entry",
                        session_date=DAY.isoformat(), created_at=rig.clock.now().isoformat())
    cid = om.client_order_id(tid, "entry")
    rig.broker.submit_order(OrderRequest("SPY", "buy", "market", cid, qty=0.01))
    with pytest.raises(DuplicateOrder):
        rig.broker.submit_order(OrderRequest("SPY", "buy", "market", cid, qty=0.01))
    row = om.place(tid, "entry", dict(symbol="SPY", side="buy", type="market", qty=0.01))
    assert len(broker_orders(rig)) == 1 and row["broker_order_id"] == broker_orders(rig)[0]["id"]


def test_rejections_are_recorded(rig):
    om = OrderManager(rig.db, rig.broker, rig.clock.now, "demo", retry_delays=(), sleep=lambda s: None)
    tid = rig.db.insert("trades", symbol="SPY", strategy="orb", mode="demo", status="pending_entry",
                        session_date=DAY.isoformat(), created_at=rig.clock.now().isoformat())
    rig.broker.reject_next = "asset not tradable"
    row = om.place(tid, "entry", dict(symbol="SPY", side="buy", type="market", qty=0.01))
    assert row["status"] == "rejected" and "not tradable" in row["reject_reason"]


def test_partial_fills_produce_incremental_deltas(rig):
    om = OrderManager(rig.db, rig.broker, rig.clock.now, "demo", retry_delays=(), sleep=lambda s: None)
    tid = rig.db.insert("trades", symbol="SPY", strategy="orb", mode="demo", status="pending_entry",
                        session_date=DAY.isoformat(), created_at=rig.clock.now().isoformat())
    rig.broker.partial_fill_ratio = 0.4
    row = om.place(tid, "entry", dict(symbol="SPY", side="buy", type="market", qty=0.1))
    row, d1 = om.sync(row)
    assert d1.qty == pytest.approx(0.04) and row["status"] == "partially_filled"
    rig.broker.tick(rig.clock.advance(5))
    row, d2 = om.sync(row)
    assert d2.qty == pytest.approx(0.06) and row["status"] == "filled"
    row, d3 = om.sync(row)
    assert d3 is None
    assert rig.db.scalar("SELECT SUM(qty) FROM fills WHERE order_id=?", (row["id"],)) == pytest.approx(0.1)


# ================================================================ engine order flow
def test_entry_gets_protective_broker_stop(rig):
    t = enter(rig, "AAPL")
    assert t["status"] == "open" and t["entry_qty"] > 0 and t["entry_qty"] * t["entry_price"] <= 20.01
    stops = broker_orders(rig, type="stop", status="new")
    assert len(stops) == 1 and stops[0]["qty"] == pytest.approx(t["entry_qty"])
    assert stops[0]["stop_price"] == pytest.approx(t["stop_price"])
    assert not t["software_stop"]


def test_no_duplicate_entries_for_same_symbol(rig):
    enter(rig, "AAPL")
    second = enter(rig, "AAPL")
    assert second is None
    assert len(broker_orders(rig, side="buy")) == 1


def test_partial_entry_then_timeout_sizes_stop_to_filled_qty(rig):
    rig.broker.partial_fill_ratio = 0.5
    real_fill = rig.broker._try_fill
    rig.broker._try_fill = lambda o, now: real_fill(o, now) if o["filled_qty"] == 0 else None  # remainder never fills
    t = enter(rig, "MSFT")
    assert t["status"] == "pending_entry" and t["entry_qty"] > 0
    rig.run_until(rig.clock.now() + timedelta(seconds=70), step_seconds=5)
    t = rig.db.trade(t["id"])
    assert t["status"] == "open"
    entry = rig.db.query_one("SELECT * FROM orders WHERE trade_id=? AND purpose='entry'", (t["id"],))
    assert entry["status"] == "canceled" and entry["filled_qty"] == pytest.approx(entry["qty"] * 0.5, rel=1e-4)
    stop = [o for o in broker_orders(rig, type="stop") if o["status"] == "new"][0]
    assert stop["qty"] == pytest.approx(t["entry_qty"])


def test_software_stop_fallback_when_broker_rejects_stops(rig):
    rig.broker.reject_stop_orders = True
    t = enter(rig, "NVDA")
    t = rig.db.trade(t["id"])
    assert t["software_stop"] == 1
    assert rig.db.query("SELECT * FROM events WHERE title LIKE '%stop order not accepted%'")
    override_price(rig, "NVDA", t["stop_price"] * 0.997)
    rig.run_until(rig.clock.now() + timedelta(seconds=15), step_seconds=5)
    t = rig.db.trade(t["id"])
    assert t["status"] == "closed" and "stop-loss" in t["exit_reason"]
    assert rig.broker.state.positions.get("NVDA") is None


def test_broker_stop_fills_and_closes_trade(rig):
    t = enter(rig, "AAPL")
    override_price(rig, "AAPL", t["stop_price"] * 0.998)
    rig.run_until(rig.clock.now() + timedelta(seconds=20), step_seconds=5)
    t = rig.db.trade(t["id"])
    assert t["status"] == "closed" and t["realized_pnl"] < 0
    assert "stop" in t["exit_reason"]


def test_target_exit_and_pnl_matches_broker_cash(rig):
    cash0 = rig.broker.state.cash
    t = enter(rig, "AAPL", target_pct=0.6)
    override_price(rig, "AAPL", t["target_price"] * 1.002)
    rig.run_until(rig.clock.now() + timedelta(seconds=20), step_seconds=5)
    t = rig.db.trade(t["id"])
    assert t["status"] == "closed" and "target" in t["exit_reason"] and t["realized_pnl"] > 0
    assert rig.broker.state.cash - cash0 == pytest.approx(t["realized_pnl"], abs=1e-6)
    assert 0 < t["fees"] < 0.005  # exact SEC/TAF/CAT accrual on a ~$20 sale (Alpaca rounds the DAY total, not each sale)


def test_trailing_stop_only_moves_up(rig):
    t = enter(rig, "AAPL", stop_pct=1.0, target_pct=5.0)
    entry = t["entry_price"]
    override_price(rig, "AAPL", entry * 1.016)  # > 1.5R
    rig.run_until(rig.clock.now() + timedelta(seconds=20), step_seconds=5)
    t2 = rig.db.trade(t["id"])
    assert t2["stop_price"] > t["stop_price"] and t2["stop_price"] >= entry
    live_stops = broker_orders(rig, type="stop", status="new")
    assert len(live_stops) == 1 and live_stops[0]["stop_price"] == pytest.approx(t2["stop_price"])
    override_price(rig, "AAPL", entry * 1.005)  # pullback, still above new stop
    rig.run_until(rig.clock.now() + timedelta(seconds=10), step_seconds=5)
    assert rig.db.trade(t["id"])["stop_price"] == pytest.approx(t2["stop_price"])


def test_emergency_stop_closes_everything_and_blocks_entries(rig):
    a, b = enter(rig, "AAPL"), enter(rig, "MSFT")
    rig.engine.command("emergency_stop")
    rig.run_until(rig.clock.now() + timedelta(seconds=10), step_seconds=5)
    assert rig.db.open_trades() == []
    assert rig.broker.state.positions == {}
    assert not [o for o in rig.broker.state.orders.values() if o["status"] == "new"]
    assert rig.db.get_state("emergency_stop") is True
    assert enter(rig, "NVDA") is None
    rig.engine.command("reset_emergency")
    rig.engine.step()
    assert rig.engine.emergency_stop is False


def test_max_positions_enforced(rig):
    assert enter(rig, "AAPL") and enter(rig, "MSFT")
    assert enter(rig, "NVDA") is None
    assert len(rig.db.open_trades()) == 2


def test_daily_loss_limit_halts_new_entries(rig):
    rig.db.insert("trades", symbol="XYZ", strategy="orb", mode="demo", status="closed", session_date=DAY.isoformat(),
                  created_at=rig.clock.now().isoformat(), entry_qty=1, entry_price=10, exit_qty=1, exit_price=7.9,
                  exit_time=rig.clock.now().isoformat(), realized_pnl=-2.1)
    rig.engine.step()
    assert rig.engine._daily_limit_day == DAY
    assert rig.db.query("SELECT * FROM events WHERE title='daily loss limit reached'")
    assert enter(rig, "AAPL") is None
    assert rig.engine.status_line(rig.clock.now())["headline"].startswith("hit today")


def test_end_of_day_flatten(rig):
    rig.engine._strategy_exit = lambda t: None  # isolate the end-of-day rule from strategy exits
    enter(rig, "AAPL", max_hold=600, stop_pct=2.5, target_pct=8)
    enter(rig, "MSFT", max_hold=600, stop_pct=2.5, target_pct=8)
    rig.clock.set(at(15, 49))
    rig.run_until(at(15, 52), step_seconds=10)
    assert rig.db.open_trades() == []
    assert rig.broker.state.positions == {}
    assert all("end of day" in t["exit_reason"] for t in rig.db.closed_trades())


def test_network_failures_do_not_crash_or_trade(rig):
    rig.broker.fail_next_calls = 50
    for _ in range(5):
        rig.clock.advance(15)
        rig.engine.step()
    assert rig.engine.health["broker"].ok is False
    assert enter(rig, "AAPL") is None  # no new entries while the broker is unreachable
    rig.broker.fail_next_calls = 0
    rig.engine.timers.reconcile = 0
    rig.clock.advance(20)
    rig.engine.step()
    assert rig.engine.health["broker"].ok is True


def test_position_sold_outside_app_is_reconciled(rig):
    t = enter(rig, "AAPL")
    for o in broker_orders(rig, type="stop", status="new"):
        rig.broker.cancel_order(o["id"])
    rig.broker.submit_order(OrderRequest("AAPL", "sell", "market", "manual-1", qty=t["entry_qty"]))
    rig.engine.timers.reconcile = 0
    rig.clock.advance(20)
    rig.engine.step()
    t = rig.db.trade(t["id"])
    assert t["status"] == "closed" and "outside" in t["exit_reason"]


def test_restart_recovery_resumes_management(rig):
    t = enter(rig, "AAPL")
    stops_before = len(broker_orders(rig, type="stop"))
    # simulate the app crashing and restarting: new engine, same database and broker account
    ai = AIEngine(rig.settings, rig.db, ScriptedAI(), now=rig.clock.now)
    eng2 = TradingEngine(rig.settings, "demo", rig.broker, rig.data, rig.db, rig.calendar, rig.clock, ai,
                         universe=rig.engine.universe, monotonic=lambda: rig.clock.now().timestamp())
    eng2.initialize()
    rig.engine = eng2
    rig.run_until(rig.clock.now() + timedelta(seconds=30), step_seconds=5)
    assert rig.db.trade(t["id"])["status"] == "open"
    assert len(broker_orders(rig, type="stop")) == stops_before, "restart must not duplicate protective stops"
    rig.clock.set(at(15, 50))
    rig.run_until(at(15, 52), step_seconds=10)
    assert rig.db.trade(t["id"])["status"] == "closed"


def test_crash_between_write_ahead_and_reply_is_recovered(rig):
    tid = rig.db.insert("trades", symbol="SPY", strategy="orb", mode="demo", status="pending_entry", managed=1,
                        session_date=DAY.isoformat(), created_at=rig.clock.now().isoformat(), stop_price=1.0,
                        initial_stop=1.0, target_price=9999.0,
                        max_hold_until=(rig.clock.now() + timedelta(hours=1)).isoformat())
    cid = f"{rig.engine.orders.prefix}261104-t{tid}-entry-1"
    rig.db.insert("orders", trade_id=tid, purpose="entry", client_order_id=cid, symbol="SPY", side="buy",
                  type="market", qty=0.02, status="pending_submit", created_at=rig.clock.now().isoformat())
    rig.broker.submit_order(OrderRequest("SPY", "buy", "market", cid, qty=0.02))  # it DID reach the broker
    ai = AIEngine(rig.settings, rig.db, ScriptedAI(), now=rig.clock.now)
    eng2 = TradingEngine(rig.settings, "demo", rig.broker, rig.data, rig.db, rig.calendar, rig.clock, ai,
                         universe=rig.engine.universe, monotonic=lambda: rig.clock.now().timestamp())
    eng2.initialize()
    eng2.step()
    t = rig.db.trade(tid)
    assert t["status"] == "open" and t["entry_qty"] == pytest.approx(0.02)
    assert len(broker_orders(rig, side="buy")) == 1


def test_leftover_position_from_previous_day_is_closed(tmp_path):
    r = make_rig(tmp_path, start_offset_min=90, universe_size=80)
    r.engine.step()
    t = enter(r, "AAPL")
    r.db.update("trades", t["id"], session_date="2026-11-03")
    r.run_until(r.clock.now() + timedelta(seconds=15), step_seconds=5)
    assert "previous session" in r.db.trade(t["id"])["exit_reason"]
