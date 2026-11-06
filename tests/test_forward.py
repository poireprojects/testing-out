"""Forward research ledger: append-only, hash-chained, outcomes only from bars after each decision."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from dkapp.research.forward import Bars, ForwardLedger, outcomes_for
from dkapp.risk.fees import CostModel
from dkapp.storage.db import Database

OPEN = datetime(2026, 11, 9, 14, 30, tzinfo=timezone.utc)


def _ledger(tmp_path):
    return ForwardLedger(Database(tmp_path / "paper.db", "paper"))


def _bars(prices, highs=None, lows=None):
    p = np.array(prices, float)
    return Bars(np.arange(len(p), dtype=float), np.concatenate([[p[0]], p[:-1]]),
                np.array(highs, float) if highs is not None else p * 1.0005,
                np.array(lows, float) if lows is not None else p * 0.9995, p)


def test_chain_detects_tampering_and_the_table_refuses_edits(tmp_path):
    led = _ledger(tmp_path)
    for k in range(3):
        led.record(time=OPEN + timedelta(minutes=30 + k), session_date=date(2026, 11, 9), symbol=f"S{k}",
                   hypothesis="momentum", decision="rejected", payload={"plan": {"kind": "marketable"}, "k": k})
    assert led.verify() == (True, None)
    with pytest.raises(sqlite3.DatabaseError):
        led.db.execute("UPDATE forward_observations SET decision='taken' WHERE id=2")
    with pytest.raises(sqlite3.DatabaseError):
        led.db.execute("DELETE FROM forward_observations WHERE id=1")
    # someone bypasses the triggers and rewrites a past prediction: the chain shows it
    led.db.execute("DROP TRIGGER fwd_obs_no_update")
    led.db.execute("UPDATE forward_observations SET payload=replace(payload, '\"k\":1', '\"k\":9') WHERE id=2")
    assert led.verify() == (False, 2)


def test_outcomes_use_only_bars_that_start_after_the_decision():
    prices = [100] * 40 + [110] * 360          # a jump at minute 40
    b = _bars(prices)
    p = {"plan": {"kind": "marketable", "spreadBps": 3.0}}
    before = outcomes_for(p, b, 35.5, CostModel(), date(2026, 11, 9))
    after = outcomes_for(p, b, 45.0, CostModel(), date(2026, 11, 9))
    assert before["plan"]["entry"] == pytest.approx(100.0) and before["plan"]["gross"] > 9
    assert after["plan"]["entry"] == pytest.approx(110.0)        # the jump already happened: no credit for it
    assert abs(after["plan"]["gross"]) < 0.01 and after["plan"]["net"] < 0   # costs make a flat trade negative


def test_breakout_plan_that_never_triggers_is_no_trade_not_a_fill():
    b = _bars([100] * 400)
    p = {"plan": {"kind": "orb", "triggerPrice": 101.0, "stopPrice": 99.0, "deadlineMin": 90, "spreadBps": 10.0}}
    out = outcomes_for(p, b, 5.0, CostModel(), date(2026, 11, 9))
    assert out["plan"]["filled"] is False and "h30" not in out
    hi = [100.0] * 400
    hi[20] = 101.5
    b2 = _bars([100] * 400, highs=hi)
    out2 = outcomes_for(p, b2, 5.0, CostModel(), date(2026, 11, 9))
    assert out2["plan"]["filled"] and out2["plan"]["entry"] == pytest.approx(101.0)


def test_scoring_never_changes_observations_and_reports_by_decision(tmp_path):
    led = _ledger(tmp_path)
    day = date(2026, 11, 9)
    for k, dec in enumerate(["taken", "rejected", "rejected"]):
        led.record(time=OPEN + timedelta(minutes=60), session_date=day, symbol=f"S{k}", hypothesis="momentum",
                   decision=dec, payload={"plan": {"kind": "marketable", "stopPrice": 95.0, "spreadBps": 3.0},
                                          "predicted": {"expNetPct": -0.1}})
    bars = {f"S{k}": _bars([100 + 0.01 * i * (k + 1) for i in range(390)]) for k in range(3)}
    assert led.days_needing_outcomes(day + timedelta(days=1)) == [day]
    n = led.compute_outcomes(day, bars, OPEN, CostModel(), OPEN + timedelta(hours=7))
    assert n == 3 and led.days_needing_outcomes(day + timedelta(days=1)) == []
    assert led.verify() == (True, None)
    s = led.summary()
    got = {(g["hypothesis"], g["decision"]): g for g in s["groups"]}
    assert got[("momentum", "taken")]["withOutcome"] == 1 and got[("momentum", "rejected")]["withOutcome"] == 2
    assert s["chainIntact"] is True
    with pytest.raises(sqlite3.DatabaseError):
        led.db.execute("UPDATE forward_outcomes SET net_pct = 5")


def test_engine_records_taken_and_rejected_candidates_and_scores_them_after_the_close(tmp_path, monkeypatch):
    from tests.harness import make_rig, make_signal
    rig = make_rig(tmp_path, mode="paper", start_offset_min=60)
    rig.run_until(rig.clock.now() + timedelta(minutes=2), step_seconds=30)
    eng = rig.engine
    monkeypatch.setattr(type(eng.data), "is_demo", property(lambda self: False))
    syms = [s for s in eng.universe if s not in ("SPY", "QQQ")][:3]
    d = eng._rank([make_signal(rig, s) for s in syms], rig.clock.now(), {}, eng.session)
    eng._record_forward(d, rig.clock.now(), eng.session)
    eng._record_forward(d, rig.clock.now(), eng.session)        # deduplicated within the cooldown
    rows = rig.db.query("SELECT * FROM forward_observations")
    assert len(rows) == len(d.candidates) and {r["decision"] for r in rows} <= {"taken", "rejected"}
    close = eng.session.session.close
    eng._forward_outcomes(close + timedelta(minutes=5), include_today=True)
    assert rig.db.scalar("SELECT COUNT(DISTINCT obs_id) FROM forward_outcomes") == len(rows)
    assert eng.forward.verify() == (True, None)
    st = eng._forward_status()
    assert st["available"] and st["observations"] == len(rows)


def test_gap_observer_records_qualifying_stocks_in_play_once_per_day(tmp_path, monkeypatch):
    import pandas as pd
    from dkapp.analysis.features import Baseline
    from dkapp.scanner import ScanRow
    from tests.harness import make_rig
    rig = make_rig(tmp_path, mode="paper", start_offset_min=6)
    rig.run_until(rig.clock.now() + timedelta(seconds=30), step_seconds=30)
    eng = rig.engine
    monkeypatch.setattr(type(eng.data), "is_demo", property(lambda self: False))
    open_ = eng.session.session.open
    eng.scanner.baselines = {"GAPX": Baseline(2_000_000, 50.0, 2.0), "FLAT": Baseline(2_000_000, 50.0, 2.0),
                             "REDX": Baseline(2_000_000, 50.0, 2.0)}
    # GAPX opened +6% and is green with heavy volume; FLAT has no gap; REDX gapped but is red since the open
    eng.scanner.rows = {"GAPX": ScanRow("GAPX", 53.5, 7.0, 0.94, 300_000, 4.0, 5.0, 1.0),
                        "FLAT": ScanRow("FLAT", 50.1, 0.2, 0.1, 300_000, 4.0, 5.0, 1.0),
                        "REDX": ScanRow("REDX", 52.0, 4.0, -1.5, 300_000, 4.0, 5.0, 1.0)}
    idx = pd.date_range(pd.Timestamp(open_).tz_convert("UTC"), periods=6, freq="1min")
    bars = pd.DataFrame({"open": [53.0, 53.1, 53.2, 53.3, 53.4, 53.5], "high": [53.2, 53.3, 53.4, 53.6, 53.6, 53.7],
                         "low": [52.8, 53.0, 53.1, 53.2, 53.3, 53.4], "close": [53.1, 53.2, 53.3, 53.4, 53.5, 53.6],
                         "volume": [1e4] * 6}, index=idx)
    monkeypatch.setattr(eng.data, "get_intraday_bars", lambda syms, a, b: {s: bars for s in syms})
    eng._record_gap_candidates(rig.clock.now(), eng.session)
    eng._record_gap_candidates(rig.clock.now(), eng.session)       # once a day
    rows = rig.db.query("SELECT symbol, hypothesis, decision, payload FROM forward_observations")
    assert {r["symbol"] for r in rows} == {"GAPX"} and len(rows) == 2
    import json
    plans = {r["hypothesis"]: json.loads(r["payload"])["plan"] for r in rows}
    orb = plans["P2-H2 gap stocks in play (ORB)"]
    assert orb["triggerPrice"] == pytest.approx(53.6) and orb["stopPrice"] == pytest.approx(52.8)  # first 5 bars only
    assert all(r["decision"] == "would-take" for r in rows)


def test_shipped_phase2_summary_matches_the_committed_holdout_results():
    """The Performance page's phase-2 table is generated from holdout.json; a stale copy would misreport evidence."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    hold = json.loads((root / "research/runs/2026-11-07-phase2/holdout.json").read_text())
    shipped = json.loads((root / "dkapp/assets/research/phase2-summary.json").read_text())
    rows = {r["key"]: r for r in shipped["rows"]}
    assert rows["no_trade"]["trades"] == 0
    for k, r in rows.items():
        if k == "no_trade":
            continue
        e = hold["evals"][k]
        assert r["trades"] == e["trades"] and r["netPct"] == pytest.approx(e["avgRetPct"])
        assert r["verdict"] == hold["verdicts"].get(k, {}).get("verdict", "baseline")
    passed = [h for h, v in hold["verdicts"].items() if v["verdict"] == "eligible_for_forward_test"]
    assert sorted(shipped["passed"]) == sorted(passed)
