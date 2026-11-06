"""Statistical model, research pipeline and their safety rules: no look-ahead, honest data use, explicit stages."""
from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from dkapp.backtest.engine import BTConfig, BTTrade, DayInput, FrameView, _simulate_exit, replay_portfolio, with_daily_rounding
from dkapp.quant.model import QuantModel
from dkapp.quant.registry import ModelCard, ModelRegistry
from dkapp.research.dataset import build_frames, dense_samples
from dkapp.research.evaluate import judge, walk_forward_predict
from dkapp.research.ledger import DataLedger, Period
from dkapp.research.stats import auc, calibration, day_block_bootstrap, holm


def _bars(seed: int, day: date, n: int = 390, drift: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(pd.Timestamp(f"{day} 13:30", tz="UTC"), periods=n, freq="1min")
    c = 100 * np.exp(np.cumsum(rng.normal(drift, 0.0008, n)))
    o = np.concatenate([[100.0], c[:-1]])
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.0006, n))
    l = np.minimum(o, c) * (1 - rng.uniform(0, 0.0006, n))
    v = rng.integers(1_000, 20_000, n).astype(float)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v, "vwap": c}, index=idx)


def _day(day: date, n_sym: int = 6, cut: int | None = None, alter_after: int | None = None) -> DayInput:
    from dkapp.analysis.features import Baseline
    bars = {}
    for k, s in enumerate(["SPY"] + [f"S{j}" for j in range(n_sym)]):
        b = _bars(k + day.toordinal(), day)
        if alter_after is not None:  # change the FUTURE only
            b.iloc[alter_after + 1:, :4] *= 1.07
        bars[s] = b.iloc[:cut] if cut else b
    open_ts = pd.Timestamp(f"{day} 13:30", tz="UTC")
    return DayInput(day, open_ts, 390, bars, {s: Baseline(5_000_000, 99.5, 1.2) for s in bars}, None)


# ------------------------------------------------------------------ no look-ahead
def test_features_at_a_bar_never_change_when_future_bars_change():
    d = date(2026, 3, 2)
    a, b = build_frames(_day(d)), build_frames(_day(d, alter_after=200))
    for sym in a.syms:
        for i in (40, 120, 200):
            fa, fb = a.features(sym, i), b.features(sym, i)
            assert np.allclose(fa, fb, equal_nan=True), (sym, i)
    assert np.allclose(a.breadth[:200], b.breadth[:200], equal_nan=True)


def test_dense_labels_start_after_the_decision_bar():
    cfg = BTConfig()
    df = build_frames(_day(date(2026, 3, 3)))
    for s in dense_samples(df, cfg):
        sf = df.syms[s.symbol]
        assert sf.mins[s.trade.entry_i] > s.minute          # bought at a bar AFTER the decision minute
        assert s.trade.ref_entry == pytest.approx(sf.fv["open"].a[s.trade.entry_i])


def test_walk_forward_trains_only_on_earlier_days_with_an_embargo():
    rng = np.random.default_rng(1)
    days = [date(2026, 1, 1) + timedelta(days=k) for k in range(30) for _ in range(40)]
    X = rng.normal(size=(len(days), 3))
    y = X[:, 0] * 0.2 + rng.normal(0, 1, len(days))
    wf = walk_forward_predict(X, y, days, ["a", "b", "c"], min_train_days=10, embargo=1, n_boot=0)
    for test_day, m in wf.models:
        assert date.fromisoformat(m.train_days[1]) < test_day - timedelta(days=1)
    # predictions for a day don't depend on LATER days' data
    y2 = y.copy()
    late = np.array([d >= date(2026, 1, 25) for d in days])
    y2[late] += 50
    wf2 = walk_forward_predict(X, y2, days, ["a", "b", "c"], min_train_days=10, embargo=1, n_boot=0)
    early = [i for i, d in enumerate(days) if d < date(2026, 1, 25) and i in wf.preds]
    assert early and all(wf.preds[i]["exp_net"] == pytest.approx(wf2.preds[i]["exp_net"]) for i in early)


# ------------------------------------------------------------------ fills and costs
def test_target_must_trade_through_and_ranker_decides_on_the_signal_close():
    idx = pd.date_range("2026-03-02 13:30", periods=5, freq="1min", tz="UTC")
    f = pd.DataFrame({"open": [100, 100, 100, 100, 100], "high": [100.5, 101.0, 100.2, 100.2, 100.2],
                      "low": [99.9] * 5, "close": [100.0] * 5}, index=idx, dtype=float)
    fv = FrameView(f)
    new, old = BTConfig(), BTConfig.baseline_build20()
    assert _simulate_exit(fv, None, 0, 100, 99, 101.0, 60, 5, new, use_strategy_exit=False)[2] != "target"
    assert _simulate_exit(fv, None, 0, 100, 99, 101.0, 60, 5, old, use_strategy_exit=False)[2] == "target"


def test_daily_fee_rounding_is_charged_once_per_day():
    cfg = BTConfig()
    d = date(2027, 2, 1)
    ts = [BTTrade("x", f"S{k}", d, 1, 2, 100, 100, 20, 0.0, 0.0, "t", fee_sec=0.0004, fee_taf=0.00004,
                  fee_cat=0.000001, minute=k, exit_minute=k + 1) for k in range(4)]
    out = with_daily_rounding(ts, cfg)
    total = sum(t.pnl for t in out)
    acc = 4 * (0.0004 + 0.00004 + 0.000001)
    assert total == pytest.approx(-(0.03 - acc))  # three fee types, each rounded up to one cent for the day


# ------------------------------------------------------------------ model quality tools
def test_logistic_model_is_calibrated_and_ranks_on_a_known_signal():
    rng = np.random.default_rng(4)
    X = rng.normal(size=(6000, 2))
    p = 1 / (1 + np.exp(-(1.5 * X[:, 0] - 0.5)))
    ret = np.where(rng.random(6000) < p, 0.5, -0.5) + 0.05 * X[:, 1]
    m = QuantModel(["a", "b"]).fit(X[:4000], ret[:4000])
    out = m.predict(X[4000:])
    y = (ret[4000:] > 0).astype(int)
    assert auc(out["p_win"], y) > 0.75
    assert calibration(out["p_win"], y)["ece"] < 0.05
    m2 = QuantModel.from_dict(json.loads(json.dumps(m.to_dict())))
    assert m2.fingerprint() == m.fingerprint()
    assert np.allclose(m2.predict(X[:5])["exp_net"], m.predict(X[:5])["exp_net"])


def test_day_block_bootstrap_and_holm():
    days = [date(2026, 1, 1) + timedelta(days=k // 10) for k in range(400)]
    rng = np.random.default_rng(2)
    pos = day_block_bootstrap(list(rng.normal(0.3, 1, 400)), days)
    zero = day_block_bootstrap(list(rng.normal(0.0, 1, 400)), days)
    assert pos["p"] < 0.05 and zero["p"] > 0.05 and pos["lo"] < pos["mean"] < pos["hi"]
    adj = holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj["a"] == pytest.approx(0.03) and adj["c"] == pytest.approx(0.06) and adj["b"] == pytest.approx(0.06)


def test_go_no_go_rejects_losers_and_marks_small_samples_insufficient():
    ev = {"trades": 500, "avgRetPct": -0.05, "usedFolds": 4, "positiveFolds": 1, "top5Share": None,
          "stress2xAvgRetPct": -0.2}
    assert judge(ev, 0.9, 0.0)["verdict"] == "rejected"
    small = dict(ev, trades=40, avgRetPct=0.3, positiveFolds=4, stress2xAvgRetPct=0.1)
    assert judge(small, 0.01, 0.0)["verdict"] == "insufficient"
    good = dict(ev, avgRetPct=0.1, positiveFolds=4, stress2xAvgRetPct=0.02, top5Share=0.2)
    assert judge(good, 0.01, 0.0)["verdict"] == "eligible_for_forward_test"
    assert judge(good, 0.01, 0.2)["verdict"] == "rejected"  # must beat the best simple baseline


# ------------------------------------------------------------------ honest data use
def test_ledger_never_calls_examined_days_unseen_and_loader_refuses_the_holdout(tmp_path):
    from dkapp.research.data import ReservedData, load_range
    led = DataLedger([Period(date(2026, 1, 1), date(2026, 1, 31), "examined", "t"),
                      Period(date(2026, 2, 1), date(2026, 2, 28), "reserved", "t")], date(2026, 11, 9))
    assert led.status(date(2026, 1, 5)) == "examined" and led.status(date(2026, 2, 5)) == "reserved"
    assert led.status(date(2026, 11, 10)) == "forward"
    assert not led.may_call_unseen([date(2026, 1, 5)]) and led.may_call_unseen([date(2026, 11, 10)])
    with pytest.raises(ReservedData):
        load_range(tmp_path, date(2026, 2, 2), date(2026, 2, 6), ledger=led)
    marker = tmp_path / "HOLDOUT_USED.json"
    try:
        load_range(tmp_path, date(2026, 2, 2), date(2026, 2, 6), purpose="holdout", ledger=led, marker=marker)
    except Exception:
        pass  # empty cache: no days, but the one-time marker is written first
    assert marker.exists()
    with pytest.raises(ReservedData):
        load_range(tmp_path, date(2026, 2, 2), date(2026, 2, 6), purpose="holdout", ledger=led, marker=marker)


def test_repository_ledger_marks_the_build20_study_examined():
    led = DataLedger.load()
    assert led.status(date(2026, 10, 30)) == "examined"   # build 20's "unseen" test period
    assert not led.may_call_unseen([date(2026, 10, 30)])


# ------------------------------------------------------------------ model registry stages
def _tiny_model() -> QuantModel:
    from dkapp.quant.features import FEATURES, STRATEGY_FEATURES
    names = list(FEATURES) + STRATEGY_FEATURES
    rng = np.random.default_rng(0)
    X = rng.normal(size=(300, len(names)))
    return QuantModel(names).fit(X, rng.normal(-0.1, 0.5, 300))


def test_registry_stages_activation_and_rollback(tmp_path):
    reg = ModelRegistry(tmp_path, bundled=tmp_path / "none")
    m = _tiny_model()
    reg.register(ModelCard("sf-v1", "strategy_filter", "f1", "shadow", ["2026-01-01", "2026-04-30"]), m)
    with pytest.raises(ValueError):
        reg.register(ModelCard("sf-v1", "strategy_filter", "f1", "shadow", []), m)  # never overwritten
    with pytest.raises(PermissionError):
        reg.set_stage("sf-v1", "paper")  # did not pass the pre-registered checks
    reg.activate("sf-v1")
    reg.register(ModelCard("sf-v2", "strategy_filter", "f1", "shadow", []), m)
    reg.activate("sf-v2")
    assert reg.active_card("strategy_filter").id == "sf-v2"
    assert reg.rollback("strategy_filter") == "sf-v1" and reg.active_card("strategy_filter").id == "sf-v1"
    assert reg.load("sf-v1").fingerprint() == m.fingerprint()


def test_gate_records_in_shadow_and_blocks_only_in_gated_paper_mode(tmp_path):
    from dkapp.models import Signal
    from dkapp.quant.gate import QuantGate
    reg = ModelRegistry(tmp_path, bundled=tmp_path / "none")
    reg.register(ModelCard("sf-v1", "strategy_filter", "f1", "shadow", [], paper_eligible=True), _tiny_model())
    reg.activate("sf-v1")
    g = QuantGate(reg)
    sig = Signal("AAA", "momentum", None, 100.0, 99.0, 102.0, 0.7, [], 60, {"rsi14": 60, "momentum_5m_pct": 0.2})
    a = g.assess(sig, {}, 0.5, 90, 390, gating=False)
    assert a is not None and not a.blocks and isinstance(a.p_win, float)
    assert not g.may_gate("paper", True)            # card is shadow, not paper
    reg.set_stage("sf-v1", "paper")
    g.reload()
    assert g.may_gate("paper", True) and not g.may_gate("live", True) and not g.may_gate("paper", False)
    a2 = g.assess(sig, {}, 0.5, 90, 390, gating=True, min_exp=10.0)
    assert a2.blocks and "below the bar" in a2.text


# ------------------------------------------------------------------ language-model explanations
def test_explanations_that_contradict_computed_numbers_are_caught():
    from dkapp.ai.consistency import Facts, check_explanation
    f = Facts(rsi=64.3, stop_pct=0.92, target_pct=1.84, reward_risk=1.4)
    assert check_explanation("RSI is oversold, so buy.", f).problems
    assert check_explanation("The stop loss is set at 1.2% below the entry.", f).problems
    assert check_explanation("RSI is 64, above the oversold threshold, and the stop is 0.92% away.", f).ok
    assert check_explanation("RSI (14) is below 70 because momentum is steady; target 1.84%.", f).ok
    assert check_explanation("rsi 64.3 vwap 100.2 ema9 99.8 ema20 99.1 atr 0.3 mom5 0.2", f).problems


def test_engine_records_model_predictions_and_gates_only_paper(tmp_path):
    from datetime import timedelta as td

    from dkapp.quant.gate import NO_EDGE_TEXT, QuantGate
    from tests.harness import make_rig, make_signal
    rig = make_rig(tmp_path, mode="paper", start_offset_min=40)
    rig.run_until(rig.clock.now() + td(minutes=2), step_seconds=30)
    reg = ModelRegistry(tmp_path / "models", bundled=tmp_path / "none")
    reg.register(ModelCard("sf-v1", "strategy_filter", "f1", "paper", [], paper_eligible=True), _tiny_model())
    reg.activate("sf-v1")
    eng = rig.engine
    eng.quant = QuantGate(reg)
    syms = [s for s in rig.engine.universe if s not in ("SPY", "QQQ")][:2]
    sigs = [make_signal(rig, s, stop_pct=1.0, target_pct=2.0) for s in syms]
    d = eng._rank(sigs, rig.clock.now(), {}, eng.session)
    assert all(c.quant and not c.quant["blocks"] for c in d.candidates)      # shadow: recorded, never blocks
    rig.settings.quant.gate_paper_entries = True
    rig.settings.quant.min_expected_net_pct = 5.0                             # nothing can clear this bar
    d = eng._rank([make_signal(rig, s) for s in syms], rig.clock.now(), {}, eng.session)
    assert all(any("statistical model" in b for b in c.blockers) for c in d.candidates)
    assert not d.chosen and d.summary.startswith(NO_EDGE_TEXT)
    eng.mode = "live"                                                         # never gates with real money
    d = eng._rank([make_signal(rig, s) for s in syms], rig.clock.now(), {}, eng.session)
    assert not any(any("statistical model" in b for b in c.blockers) for c in d.candidates)
    snap = eng.snapshot()
    assert "quant" in snap and eng._quant_status()["model"] == "sf-v1"
