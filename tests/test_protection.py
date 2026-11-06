"""Protective stops only count once the broker has confirmed them.

A stop that was sent but never acknowledged (timeout, lost response), is being canceled, was rejected,
canceled or expired is NOT protection: the position is flagged unprotected, the app watches the stop
price itself, live entries are blocked, and the order is resolved by looking it up — never written
off while the broker can't be asked.
"""
from datetime import timedelta

import pytest

from dkapp.execution.order_manager import WORKING_STATES, is_working
from tests.harness import enter, make_rig, override_price


@pytest.fixture
def rig(tmp_path):
    r = make_rig(tmp_path, start_offset_min=90, universe_size=80)  # 11:00 ET, market open
    r.engine.orders.retry_delays = (0.0, 0.0)  # no real sleeping between submit retries
    r.engine.step()
    return r


def stops_at_broker(rig, symbol, status=("new", "partially_filled")):
    return [o for o in rig.broker.state.orders.values()
            if o["symbol"] == symbol and o["type"] == "stop" and o["status"] in status]


def local_stops(rig, trade_id):
    return rig.db.query("SELECT * FROM orders WHERE trade_id=? AND purpose='stop' ORDER BY id", (trade_id,))


def tick(rig, seconds=10, n=1):
    rig.run_until(rig.clock.now() + timedelta(seconds=seconds * n), step_seconds=seconds)


# ---------------------------------------------------------------- what counts as protection
def test_only_acknowledged_live_states_count():
    for status in ("pending_submit", "pending_cancel", "rejected", "canceled", "expired", "filled", "abandoned",
                   "replaced", "done_for_day", "suspended", "held", "something_new"):
        assert not is_working({"broker_order_id": "x", "status": status}), status
    for status in WORKING_STATES:
        assert is_working({"broker_order_id": "x", "status": status})
        assert not is_working({"broker_order_id": None, "status": status}), "no broker id = never acknowledged"


def test_healthy_stop_is_confirmed_protection(rig):
    t = enter(rig, "AAPL")
    assert rig.db.trade(t["id"])["software_stop"] == 0
    assert rig.engine.unprotected() == []
    assert len(stops_at_broker(rig, "AAPL")) == 1


# ---------------------------------------------------------------- timeouts
def test_stop_that_never_reached_the_broker_is_not_protection(rig):
    eng = rig.engine
    eng._strategy_exit = lambda t: None
    rig.broker.stop_submit_timeouts = 3      # the submit and both retries time out
    rig.broker.lookup_failures = 10_000      # and the broker can't be asked whether it arrived
    t = enter(rig, "AAPL")
    rows = local_stops(rig, t["id"])
    assert [r["status"] for r in rows] == ["pending_submit"]
    override_price(rig, "AAPL", rig.db.trade(t["id"])["entry_price"])
    assert stops_at_broker(rig, "AAPL") == []
    tr = rig.db.trade(t["id"])
    assert tr["status"] == "open" and tr["software_stop"] == 1
    assert eng.unprotected() == ["AAPL"]
    assert eng.risk_context(rig.clock.now(), eng.session).unprotected_symbols == ["AAPL"]
    assert rig.db.query("SELECT * FROM events WHERE title LIKE '%couldn''t confirm the stop-loss order%'")
    eng._publish_snapshot(rig.clock.now())
    assert "no confirmed stop-loss order" in eng.snapshot()["alerts"][0]["title"]

    # two minutes pass and the broker still can't be asked: the order must NOT be written off, and no
    # second stop may be stacked on top of one that might exist
    tick(rig, 10, 12)
    rows = local_stops(rig, t["id"])
    assert [r["status"] for r in rows] == ["pending_submit"], "never abandon what the broker hasn't confirmed"
    assert eng.unprotected() == ["AAPL"]

    # recovery: the broker answers, confirms it never got the order -> written off, a new stop placed
    rig.broker.lookup_failures = 0
    tick(rig, 10, 3)
    statuses = [r["status"] for r in local_stops(rig, t["id"])]
    assert statuses[0] == "abandoned" and statuses[-1] in WORKING_STATES
    assert len(stops_at_broker(rig, "AAPL")) == 1
    assert eng.unprotected() == []
    assert rig.db.trade(t["id"])["software_stop"] == 0
    assert rig.db.query("SELECT * FROM events WHERE title LIKE '%protected by a broker stop again%'")


# ---------------------------------------------------------------- lost responses
def test_lost_response_stop_is_adopted_not_duplicated(rig):
    eng = rig.engine
    rig.broker.stop_responses_lost = 1   # the stop reaches the broker but the reply is lost
    rig.broker.lookup_failures = 10_000  # ...and the follow-up lookups fail too
    t = enter(rig, "MSFT")
    assert len(stops_at_broker(rig, "MSFT")) == 1, "it did arrive"
    assert [r["status"] for r in local_stops(rig, t["id"])] == ["pending_submit"]
    assert eng.unprotected() == ["MSFT"], "arrived, but unconfirmed: not counted as protection"
    tick(rig, 10, 6)
    assert len(stops_at_broker(rig, "MSFT")) == 1, "no second stop stacked while unconfirmed"
    rig.broker.lookup_failures = 0
    tick(rig, 10, 2)
    rows = local_stops(rig, t["id"])
    assert len(rows) == 1 and rows[0]["status"] in WORKING_STATES and rows[0]["broker_order_id"]
    assert len(stops_at_broker(rig, "MSFT")) == 1
    assert eng.unprotected() == []


def test_lost_response_is_resolved_on_the_spot_when_lookup_works(rig):
    rig.broker.stop_responses_lost = 1
    t = enter(rig, "NVDA")
    rows = local_stops(rig, t["id"])
    assert len(rows) == 1 and is_working(rows[0])
    assert rig.engine.unprotected() == []


# ---------------------------------------------------------------- stop dropped by the broker later
def test_stop_canceled_at_the_broker_is_replaced(rig):
    eng = rig.engine
    t = enter(rig, "AAPL")
    (o,) = stops_at_broker(rig, "AAPL")
    o["status"] = "canceled"  # e.g. canceled by the broker (corporate action, manual cancel on the website)
    tick(rig, 10, 2)          # stops are re-checked every 10 s
    rows = local_stops(rig, t["id"])
    assert rows[0]["status"] == "canceled" and is_working(rows[-1])
    assert len(stops_at_broker(rig, "AAPL")) == 1
    assert eng.unprotected() == []


def test_raised_stop_replaces_an_older_lower_broker_stop(rig):
    eng = rig.engine
    t = enter(rig, "AAPL")
    (old,) = stops_at_broker(rig, "AAPL")
    higher = round(old["stop_price"] * 1.004, 2)
    rig.db.update("trades", t["id"], stop_price=higher)  # raised while a stop was in flight
    tick(rig, 10, 3)
    live = stops_at_broker(rig, "AAPL")
    assert len(live) == 1 and live[0]["stop_price"] == pytest.approx(higher, abs=0.011)
    assert eng.unprotected() == []


# ---------------------------------------------------------------- the app watches while unconfirmed
def test_app_watched_stop_sells_safely_when_broker_stop_is_unconfirmed(rig):
    rig.broker.stop_responses_lost = 1
    rig.broker.lookup_failures = 10_000
    t = enter(rig, "AAPL")
    assert len(stops_at_broker(rig, "AAPL")) == 1  # unknown to the app
    override_price(rig, "AAPL", t["stop_price"] * 0.999)
    tick(rig, 5, 4)
    # can't confirm whether a stop is resting: the app must not add a market sell on top of it (that could
    # sell twice and go short) — it waits, and the broker's own stop fills meanwhile
    sim_pos = rig.broker.state.positions.get("AAPL")
    assert sim_pos is None or sim_pos.qty >= -1e-12
    rig.broker.lookup_failures = 0
    tick(rig, 5, 6)
    tr = rig.db.trade(t["id"])
    assert tr["status"] == "closed"
    pos = rig.broker.state.positions.get("AAPL")
    assert pos is None or abs(pos.qty) < 1e-9, "never short"
    assert stops_at_broker(rig, "AAPL") == []


# ---------------------------------------------------------------- recovery after a restart
def test_restart_resolves_unconfirmed_stop(rig, tmp_path):
    from dkapp.engine.trading_engine import TradingEngine
    rig.broker.stop_responses_lost = 1
    rig.broker.lookup_failures = 10_000
    t = enter(rig, "MSFT")
    assert [r["status"] for r in local_stops(rig, t["id"])] == ["pending_submit"]
    rig.broker.lookup_failures = 0
    # the app crashes and starts again with the same database and broker account
    eng2 = TradingEngine(rig.settings, "demo", rig.broker, rig.data, rig.db, rig.calendar, rig.clock, rig.ai,
                         universe=rig.engine.universe, monotonic=lambda: rig.clock.now().timestamp())
    eng2.initialize()
    eng2.step()
    rows = local_stops(rig, t["id"])
    assert len(rows) == 1 and is_working(rows[0])
    assert eng2.unprotected() == []
    assert len(stops_at_broker(rig, "MSFT")) == 1


# ---------------------------------------------------------------- leftovers at the broker
def test_stray_sell_order_with_nothing_behind_it_is_canceled(rig):
    eng = rig.engine
    # place a real stop for a trade, then make the app lose track of it (written off as never arrived)
    t = enter(rig, "AAPL")
    (row,) = local_stops(rig, t["id"])
    rig.db.update("orders", row["id"], status="abandoned")
    # a: the trade is still open -> the order is adopted again as its protection
    eng.timers.stray_sweep = 0
    eng._sweep_stray_orders(rig.clock.now())
    assert is_working(rig.db.query_one("SELECT * FROM orders WHERE id=?", (row["id"],)))
    assert len(stops_at_broker(rig, "AAPL")) == 1
    # b: an order of ours for a trade that's already finished -> canceled so it can't open a short
    rig.db.update("orders", row["id"], status="abandoned")
    rig.db.update("trades", t["id"], status="closed")
    eng._sweep_stray_orders(rig.clock.now())
    assert stops_at_broker(rig, "AAPL") == []
    assert rig.db.query("SELECT * FROM events WHERE title LIKE 'canceled a leftover sell order%'")


def test_orders_not_placed_by_the_app_are_never_touched(rig):
    from dkapp.models import OrderRequest
    t = enter(rig, "AAPL")
    qty = rig.db.trade(t["id"])["entry_qty"]
    for o in stops_at_broker(rig, "AAPL"):
        o["status"] = "canceled"
    mine = rig.broker.submit_order(OrderRequest(client_order_id="my-own-order-1", symbol="AAPL", side="sell",
                                                type="stop", qty=qty / 2, stop_price=1.0))
    rig.engine._sweep_stray_orders(rig.clock.now())
    assert rig.broker.state.orders[mine.id]["status"] == "new"


# ---------------------------------------------------------------- live mode: block and resolve
def test_live_position_without_confirmed_stop_is_sold_after_the_limit(rig):
    eng = rig.engine
    eng._strategy_exit = lambda t: None  # hold flat: only the unprotected guard may sell
    rig.broker.reject_stop_orders = True
    t = enter(rig, "AAPL")
    override_price(rig, "AAPL", rig.db.trade(t["id"])["entry_price"])  # flat: no stop/target exit
    eng.mode = "live"  # (entries in live need authorization; the guard itself is what's tested here)
    limit = eng.settings.risk.unprotected_live_exit_minutes
    tick(rig, 30, int(limit * 2) - 2)
    assert rig.db.trade(t["id"])["status"] == "open", "not before the limit"
    tick(rig, 30, 6)
    tr = rig.db.trade(t["id"])
    assert tr["status"] == "closed"
    assert "no stop-loss order could be confirmed" in tr["exit_reason"]


def test_paper_position_without_stop_is_watched_not_sold(rig):
    eng = rig.engine
    eng._strategy_exit = lambda t: None  # hold flat: only the unprotected guard may sell
    rig.broker.reject_stop_orders = True
    t = enter(rig, "AAPL")
    override_price(rig, "AAPL", rig.db.trade(t["id"])["entry_price"])
    tick(rig, 30, 26)
    tr = rig.db.trade(t["id"])
    assert "no stop-loss order could be confirmed" not in (tr["exit_reason"] or "")
    assert eng.unprotected() in (["AAPL"], [])  # still flagged (or retried successfully)


def test_live_entries_pause_after_broker_refuses_stops():
    from dkapp.config import AppSettings
    from dkapp.risk.engine import RiskEngine
    from tests.test_risk_and_sizing import ctx, proposal
    s = AppSettings()
    one = RiskEngine(s).check_entry(ctx(mode="live", live_authorized=True, stop_refused_symbols=["AAPL"]),
                                    proposal(symbol="AAPL"))
    assert any("refused a stop-loss order for AAPL" in r for r in one.reasons)
    other = RiskEngine(s).check_entry(ctx(mode="live", live_authorized=True, stop_refused_symbols=["AAPL"]),
                                      proposal(symbol="MSFT"))
    assert not any("refused" in r for r in other.reasons)
    two = RiskEngine(s).check_entry(ctx(mode="live", live_authorized=True, stop_refused_symbols=["AAPL", "TSLA"]),
                                    proposal(symbol="MSFT"))
    assert any("paused until tomorrow" in r for r in two.reasons)
    paper = RiskEngine(s).check_entry(ctx(mode="paper", stop_refused_symbols=["AAPL", "TSLA"]), proposal())
    assert not any("refused" in r for r in paper.reasons)


def test_unconfirmed_stop_blocks_live_entries_via_risk_context(rig):
    eng = rig.engine
    rig.broker.stop_submit_timeouts = 3
    rig.broker.lookup_failures = 10_000
    enter(rig, "AAPL")
    eng.mode = "live"
    ctx = eng.risk_context(rig.clock.now(), eng.session)
    assert ctx.unprotected_symbols == ["AAPL"]
    from dkapp.risk.engine import RiskEngine
    from tests.test_risk_and_sizing import proposal
    ctx.live_authorized = True
    d = RiskEngine(eng.settings).check_entry(ctx, proposal(symbol="MSFT"))
    assert any("no broker-confirmed stop order" in r for r in d.reasons)
