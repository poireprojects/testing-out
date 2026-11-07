"""Opportunity ranking, market regime, and the AI-picks decision mode."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from stockpilot.ai.schema import parse_select, select_schema
from stockpilot.decision import (REGIME_FIT, Evidence, OpportunityRanker, Regime, breadth_pct, classify_regime,
                            cost_aware_bracket)
from stockpilot.models import Signal
from tests.harness import ScriptedAI, make_rig

T = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)


def sig(symbol="AAPL", strategy="orb", price=100.0, stop_pct=1.5, target_pct=3.0, score=0.8, agreeing=None):
    return Signal(symbol, strategy, T, price, price * (1 - stop_pct / 100), price * (1 + target_pct / 100), score,
                  ["broke out"], 60, {}, agreeing or [strategy])


UP = classify_regime(True, 0.4, 65.0, 1.0)


# ---------------------------------------------------------------- regime
def test_regime_labels():
    assert classify_regime(None, None, None, None).label == "unknown"
    assert classify_regime(True, 0.5, 70.0, 2.0).label == "volatile"
    assert classify_regime(False, -0.8, 40.0, 1.0).label == "risk-off"
    assert classify_regime(False, -0.2, 25.0, 1.0).label == "risk-off"
    assert classify_regime(True, 0.3, 60.0, 1.0).label == "trending up"
    assert classify_regime(True, 0.1, 35.0, 1.0).label == "choppy"
    assert classify_regime(False, 0.1, 55.0, 1.0).label == "choppy"
    for label in REGIME_FIT:
        assert Regime(label, None, None, None, None).text  # every label has a plain-English explanation


def test_breadth_needs_enough_stocks():
    class Row:
        def __init__(self, ch):
            self.eligible, self.day_volume, self.change_open_pct = True, 1000, ch
    assert breadth_pct([Row(1.0)] * 10) is None
    assert breadth_pct([Row(1.0)] * 15 + [Row(-1.0)] * 5) == pytest.approx(75.0)


# ---------------------------------------------------------------- cost-aware brackets
def test_tight_stop_is_widened_keeping_two_to_one():
    stop, target, widened = cost_aware_bracket(100.0, 99.8, 100.4, cost_pct=0.25)
    assert widened
    assert (100 - stop) == pytest.approx(1.0)        # 4 x 0.25% cost
    assert (target - 100) == pytest.approx(2.0)


def test_wide_enough_stop_is_left_alone():
    assert cost_aware_bracket(100.0, 98.5, 103.0, cost_pct=0.25) == (98.5, 103.0, False)


# ---------------------------------------------------------------- ranker
def test_good_setup_in_good_market_is_taken():
    r = OpportunityRanker()
    c = r.score(sig(), UP, Evidence(), notional=20.0, spread_bps=3.0, dollar_volume_rank=0.9)
    assert not c.blockers, c.blockers
    assert c.rr_after_cost >= r.MIN_RR_AFTER_COST
    d = r.decide([c], UP, free_slots=2)
    assert d.chosen == [c] and c.take
    assert "picked AAPL" in d.summary


def test_costs_that_eat_the_target_block_the_trade():
    r = OpportunityRanker(max_stop_pct=3.0, max_spread_bps=30.0)
    # wide spread: blocked outright, and the cost-covering stop would be beyond the limit too
    c = r.score(sig(stop_pct=1.0, target_pct=1.0), UP, Evidence(), notional=20.0, spread_bps=75.0)
    assert any("spread" in b for b in c.blockers)
    assert any("beyond the 3.0% limit" in b for b in c.blockers)
    # without the widening rule, a small target after costs fails the reward/risk test
    s = sig(stop_pct=1.0, target_pct=0.6)
    reward, risk = s.target_pct, s.stop_pct
    cost = r.costs.round_trip_cost_pct(20.0, s.price, 20.0)
    assert (reward - cost) / (risk + cost) < r.MIN_RR_AFTER_COST
    c2 = r.score(sig(stop_pct=1.0, target_pct=0.6), UP, Evidence(), 20.0, 20.0)
    assert c2.rr_after_cost >= r.MIN_RR_AFTER_COST or c2.blockers  # widened to 2:1, or blocked


def test_no_edge_evidence_is_a_hard_no_and_synthetic_never_counts():
    r = OpportunityRanker()
    c = r.score(sig(), UP, Evidence(verdict="no_edge"), 20.0, 3.0)
    assert any("no edge" in b for b in c.blockers)
    base = r.score(sig(), UP, Evidence(), 20.0, 3.0).score
    synth = r.score(sig(), UP, Evidence(verdict="synthetic_only", avg_return_pct=2.0), 20.0, 3.0).score
    assert synth == pytest.approx(base), "synthetic results must never raise a score"
    real = r.score(sig(), UP, Evidence(verdict="evidence", avg_return_pct=0.2, source="alpaca"), 20.0, 3.0).score
    assert real > base


def test_losing_forward_test_blocks():
    c = OpportunityRanker().score(sig(), UP, Evidence(trades=25, avg_return_pct=-0.3), 20.0, 3.0)
    assert any("forward test is losing" in b for b in c.blockers)


def test_momentum_is_not_bought_into_a_falling_market():
    off = classify_regime(False, -1.0, 20.0, 1.0)
    c = OpportunityRanker().score(sig(strategy="momentum"), off, Evidence(), 20.0, 3.0)
    assert any("don't suit" in b for b in c.blockers)


def test_bar_is_higher_in_risk_off_and_volatile_markets():
    r = OpportunityRanker()
    weak = dict(score=0.45)
    up = r.score(sig(strategy="orb", **weak), UP, Evidence(), 20.0, 3.0)
    vol = r.score(sig(strategy="orb", **weak), classify_regime(True, 0.3, 60.0, 2.5), Evidence(), 20.0, 3.0)
    assert len(vol.blockers) >= len(up.blockers)
    assert any("bar for a volatile market" in b for b in vol.blockers) or vol.blockers


def test_compares_and_picks_the_best_within_free_slots():
    r = OpportunityRanker()
    cands = [r.score(sig("AAA", score=0.6), UP, Evidence(), 20.0, 3.0),
             r.score(sig("BBB", score=0.95, agreeing=["orb", "trend"]), UP, Evidence(), 20.0, 3.0),
             r.score(sig("CCC", score=0.8), UP, Evidence(), 20.0, 3.0)]
    d = r.decide(cands, UP, free_slots=1)
    assert [c.signal.symbol for c in d.chosen] == ["BBB"]
    assert d.candidates[0].signal.symbol == "BBB"
    others = [c for c in d.candidates if c.signal.symbol != "BBB" and not c.take]
    assert all(c.blockers for c in others)
    assert "passed on" in d.summary


def test_choosing_not_to_trade_is_explained():
    r = OpportunityRanker()
    c = r.score(sig(strategy="momentum"), classify_regime(False, -1.0, 20.0, 1.0), Evidence(), 20.0, 3.0)
    d = r.decide([c], classify_regime(False, -1.0, 20.0, 1.0), free_slots=2)
    assert d.chosen == []
    assert "chose not to trade" in d.summary
    assert r.decide([], UP, 2).summary.startswith("no setups")
    full = r.decide([r.score(sig(), UP, Evidence(), 20.0, 3.0)], UP, free_slots=0)
    assert full.chosen == [] and "slots are in use" in full.summary


def test_candidate_dict_is_ui_ready():
    c = OpportunityRanker().score(sig(), UP, Evidence(), 20.0, 3.0)
    d = c.to_dict()
    for k in ("symbol", "strategy", "score", "take", "reasons", "blockers", "costPct", "rrAfterCost"):
        assert k in d
    json.dumps(d)


# ---------------------------------------------------------------- AI selection parsing
def test_parse_select_accepts_only_offered_symbols():
    ok, err = parse_select(json.dumps({"choice": "aapl", "confidence": 0.6, "reasoning": "cleanest setup of the three.",
                                       "key_risks": []}), ["AAPL", "MSFT"])
    assert err is None and ok.choice == "AAPL"
    none, err = parse_select(json.dumps({"choice": "None", "confidence": 0.3, "reasoning": "nothing is good enough.",
                                         "key_risks": []}), ["AAPL"])
    assert err is None and none.choice == "none"
    bad, err = parse_select(json.dumps({"choice": "TSLA", "confidence": 0.9, "reasoning": "made up a ticker here.",
                                        "key_risks": []}), ["AAPL"])
    assert bad is None and "wasn't one of the options" in err
    assert parse_select("not json", ["AAPL"])[0] is None
    assert select_schema(["AAPL"])["properties"]["choice"]["enum"] == ["AAPL", "none"]


# ---------------------------------------------------------------- engine integration
def _run(rig, minutes):
    sess = rig.calendar.session(date(2026, 11, 4))
    rig.run_until(sess.open + timedelta(minutes=minutes), step_seconds=15)


def test_engine_ranks_journals_and_shadows(tmp_path):
    rig = make_rig(tmp_path, universe_size=60, decision_mode="rules")
    _run(rig, 150)
    cycles = rig.db.query("SELECT * FROM decision_cycles")
    assert cycles, "every decision cycle with candidates should be journaled"
    data = json.loads(cycles[-1]["data"])
    assert data["regime"]["label"] in REGIME_FIT and data["candidates"]
    verdicts = {r["verdict"] for r in rig.db.query("SELECT DISTINCT verdict FROM shadow_trades")}
    assert "rules" in verdicts
    snap = rig.engine.snapshot()
    assert snap["regime"]["label"] in REGIME_FIT
    # rules mode only ever buys what the ranker chose
    chosen = {sym for c in cycles for sym in json.loads(c["data"])["chosen"]}
    for t in rig.db.query("SELECT * FROM trades"):
        assert t["symbol"] in chosen


def test_ai_select_mode_trades_only_what_the_ai_picked(tmp_path):
    ai = ScriptedAI(select="first")
    rig = make_rig(tmp_path, universe_size=60, decision_mode="ai_select", provider=ai)
    _run(rig, 150)
    asked = rig.db.query("SELECT * FROM ai_decisions WHERE kind='select'")
    if not asked:
        pytest.skip("the ranker found nothing worth asking about on this demo day")
    trades = rig.db.query("SELECT * FROM trades")
    for t in trades:
        assert t["decision_mode"] == "ai_select"
        assert t["reasoning"] and "compared the options" in t["reasoning"]
    picked = {a["symbol"] for a in asked if a["action"] == "buy"}
    assert all(t["symbol"] in picked for t in trades)
    sel_prompts = [json.loads(p) for p in ai.prompts if '"options"' in p]
    assert sel_prompts and all(1 <= len(p["options"]) <= 3 for p in sel_prompts)
    assert all("rrAfterCost" in o and "costPct" in o for p in sel_prompts for o in p["options"])


def test_ai_select_none_means_no_trades(tmp_path):
    ai = ScriptedAI(select="none")
    rig = make_rig(tmp_path, universe_size=60, decision_mode="ai_select", provider=ai)
    _run(rig, 150)
    assert rig.db.query("SELECT * FROM trades") == []
    if ai.calls:
        assert rig.db.query("SELECT * FROM shadow_trades WHERE verdict='skipped'")


def test_ai_select_with_ai_offline_never_trades(tmp_path):
    rig = make_rig(tmp_path, universe_size=60, decision_mode="ai_select", provider=ScriptedAI(fail="offline"))
    _run(rig, 120)
    assert rig.db.query("SELECT * FROM trades") == []


# ---------------------------------------------------------------- does the AI help? (measured, never assumed)
def _shadow(db, verdict, rets, signal_ids=None):
    for k, r in enumerate(rets):
        db.insert("shadow_trades", signal_id=(signal_ids[k] if signal_ids else None), symbol="X", strategy="orb",
                  verdict=verdict, entry_time="2026-11-04T15:00:00+00:00", entry_price=100, stop_price=99,
                  target_price=102, max_hold_until="2026-11-04T16:00:00+00:00", status="closed", return_pct=r)


def test_approach_comparison_needs_samples_then_reports_honestly(tmp_path):
    from stockpilot.config import AppSettings
    from stockpilot.performance import performance_report
    from stockpilot.storage.db import Database
    db = Database(tmp_path / "p.db", "paper")
    _shadow(db, "approved", [0.1] * 10)
    rep = performance_report(db, AppSettings())
    assert rep["approaches"]["verdict"] == "not_enough_data"
    # AI's approvals did worse than what it rejected -> says so plainly
    _shadow(db, "approved", [-0.2] * 40)
    _shadow(db, "skipped", [0.1] * 40)
    _shadow(db, "ranked", [0.05] * 40)
    rep = performance_report(db, AppSettings())
    assert rep["approaches"]["verdict"] == "ai_hurts"
    assert "rules-only" in rep["approaches"]["text"]


def test_ai_that_matches_the_ranker_adds_nothing(tmp_path):
    from stockpilot.config import AppSettings
    from stockpilot.performance import performance_report
    from stockpilot.storage.db import Database
    db = Database(tmp_path / "p.db", "paper")
    _shadow(db, "approved", [0.05] * 40)
    _shadow(db, "skipped", [-0.1] * 40)
    _shadow(db, "ranked", [0.08] * 40)
    assert performance_report(db, AppSettings())["approaches"]["verdict"] == "no_gain"


def test_confidence_calibration_flags_meaningless_confidence(tmp_path):
    from stockpilot.performance import confidence_calibration
    from stockpilot.storage.db import Database
    db = Database(tmp_path / "p.db", "paper")
    ids = []
    for k in range(60):
        conf = 0.55 if k < 30 else 0.9
        sid = db.insert("signals", time="2026-11-04T15:00:00+00:00", symbol="X", strategy="orb", outcome="t")
        db.insert("ai_decisions", time="2026-11-04T15:00:00+00:00", kind="entry", symbol="X", strategy="orb",
                  action="buy", confidence=conf, valid=1, signal_id=sid)
        ids.append(sid)
    # high confidence did no better than medium confidence
    _shadow(db, "approved", [0.2 if k % 2 else -0.2 for k in range(60)], ids)
    c = confidence_calibration(db)
    assert c["samples"] == 60
    assert c["verdict"] == "uninformative"
    assert "meaningless" in c["text"]
