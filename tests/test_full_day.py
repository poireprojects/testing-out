"""A complete simulated trading day through the real engine (demo market + simulated broker).

Checks invariants that must hold no matter what the strategies do."""
from datetime import date, timedelta

import pytest

from dkapp.clock import ET
from tests.harness import ScriptedAI, make_rig


@pytest.fixture(scope="module")
def day_run(tmp_path_factory):
    rig = make_rig(tmp_path_factory.mktemp("fullday"), day=date(2026, 11, 4), universe_size=70,
                   decision_mode="ai_confirm", provider=ScriptedAI(confidence=0.75, quality="moderate"))
    sess = rig.calendar.session(date(2026, 11, 4))
    max_open = 0
    min_cash = float("inf")
    t = rig.clock.now()
    while t < sess.close + timedelta(minutes=3):
        t = rig.clock.advance(15)
        rig.engine.step()
        for p in list(rig.engine.pending_ai.values()):
            p.future.result(timeout=10)
        for f in list(rig.engine.pending_reviews.values()):
            f.result(timeout=10)
        max_open = max(max_open, len(rig.broker.state.positions))
        min_cash = min(min_cash, rig.broker.state.cash)
    return rig, max_open, min_cash


def test_trades_happened_and_all_closed_by_the_bell(day_run):
    rig, _, _ = day_run
    closed = rig.db.closed_trades()
    assert len(closed) >= 1, "the demo day should produce at least one trade"
    assert rig.db.open_trades() == []
    assert rig.broker.state.positions == {}
    assert not [o for o in rig.broker.state.orders.values() if o["status"] in ("new", "partially_filled")]


def test_limits_held_all_day(day_run):
    rig, max_open, min_cash = day_run
    s = rig.settings.risk
    assert max_open <= s.max_positions
    assert min_cash >= -1e-9, "never used margin"
    for t in rig.db.query("SELECT * FROM trades WHERE entry_qty > 0"):
        assert t["entry_qty"] * t["entry_price"] <= s.max_position_usd * 1.01 + 0.01
    assert len(rig.db.query("SELECT * FROM trades WHERE entry_qty > 0")) <= s.max_trades_per_day


def test_realized_pnl_reconciles_with_broker_cash(day_run):
    rig, _, _ = day_run
    total = sum(t["realized_pnl"] for t in rig.db.closed_trades())
    assert rig.broker.state.cash - 100.0 == pytest.approx(total, abs=1e-6)
    assert rig.broker.state.fees_paid == pytest.approx(sum(t["fees"] for t in rig.db.closed_trades()), abs=1e-9)


def test_every_trade_was_ai_approved_and_explained(day_run):
    rig, _, _ = day_run
    for t in rig.db.query("SELECT * FROM trades WHERE entry_qty > 0"):
        assert t["ai_decision_id"] is not None and t["reasoning"]
        d = rig.db.query_one("SELECT * FROM ai_decisions WHERE id=?", (t["ai_decision_id"],))
        assert d["valid"] == 1 and d["action"] == "buy"


def test_daily_summary_and_shadow_tracking(day_run):
    rig, _, _ = day_run
    s = rig.db.query_one("SELECT * FROM daily_summary WHERE session_date='2026-11-04'")
    assert s and s["end_equity"] is not None and s["trades"] == len(rig.db.closed_trades())
    shadow = rig.engine.shadow.summary()
    assert shadow["approved"]["count"] >= 1
    assert rig.db.scalar("SELECT COUNT(*) FROM shadow_trades WHERE status='open'", (), 0) == 0


def test_no_errors_logged(day_run):
    rig, _, _ = day_run
    errors = rig.db.query("SELECT * FROM events WHERE level='error'")
    assert errors == [], errors
