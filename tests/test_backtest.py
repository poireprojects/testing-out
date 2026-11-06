from datetime import date

import numpy as np
import pandas as pd
import pytest

from dkapp.analysis.features import feature_frame
from dkapp.backtest.engine import (BTConfig, BTTrade, DayInput, FrameView, _fill_trade, _simulate_exit,
                                   bootstrap_p_value, judge, run_backtest, summarize)
from dkapp.backtest.runner import latest_results, record_results, synthetic_days
from dkapp.clock import ET, MarketCalendar
from dkapp.data.demo_data import DemoMarket
from dkapp.storage.db import Database
from dkapp.strategies import ALL_STRATEGIES


def fv_from(o, h, l, c):
    idx = pd.date_range("2026-11-04 14:30", periods=len(c), freq="1min", tz="UTC")
    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": [1000] * len(c),
                       "vwap": c}, index=idx)
    return FrameView(feature_frame(df, idx[0].to_pydatetime()))


def test_stop_hit_first_when_bar_touches_both():
    fv = fv_from([100, 100], [100.5, 103], [99.5, 97], [100, 100])
    j, px, why = _simulate_exit(fv, ALL_STRATEGIES["orb"], 1, 100.0, 98.0, 102.0, 60, 99, BTConfig(),
                                use_strategy_exit=False)
    assert why == "stop" and px == 98.0


def test_gap_through_stop_fills_at_open():
    fv = fv_from([100, 96], [100.5, 96.5], [99.5, 95.5], [100, 96])
    j, px, why = _simulate_exit(fv, ALL_STRATEGIES["orb"], 1, 100.0, 98.0, 102.0, 60, 99, BTConfig(),
                                use_strategy_exit=False)
    assert why == "stop (gap)" and px == 96


def test_end_of_day_exit_at_flatten_bar():
    n = 10
    fv = fv_from([100] * n, [100.1] * n, [99.9] * n, [100] * n)
    j, px, why = _simulate_exit(fv, ALL_STRATEGIES["orb"], 1, 100.0, 98.0, 102.0, 60, 5, BTConfig(),
                                use_strategy_exit=False)
    assert (j, why) == (5, "end of day")


def test_costs_make_a_flat_trade_lose():
    t = _fill_trade("orb", "X", date(2026, 11, 4), 1, 2, 100.0, 100.0, "time", BTConfig())
    assert t.pnl < 0 and t.entry_px > 100 > t.exit_px


def test_entries_fill_at_next_bar_open_not_signal_close():
    cal = MarketCalendar()
    m = DemoMarket(["SPY", "AAPL", "MSFT", "NVDA"], cal, 3)
    days = synthetic_days(m, ["SPY", "AAPL", "MSFT", "NVDA"], date(2026, 11, 4), 6, warmup=5)
    res = run_backtest(days, list(ALL_STRATEGIES), BTConfig(random_baseline_draws=0))
    from dkapp.backtest.engine import run_day
    import random
    trades = [t for d in days for t in run_day(d, list(ALL_STRATEGIES), BTConfig(random_baseline_draws=0), random.Random(1))]
    for t in trades[:50]:
        d = next(d for d in days if d.day == t.day)
        bars = d.bars[t.symbol]
        assert t.entry_i >= 1
        open_px = bars["open"].iloc[t.entry_i]
        assert t.entry_px == pytest.approx(BTConfig().costs.fill_price("buy", open_px, BTConfig().spread_bps))
    assert res["days"] == 6


def test_statistics_and_verdicts():
    rng = np.random.default_rng(0)
    good = list(rng.normal(0.25, 0.4, 200))
    noise = list(rng.normal(0.0, 0.4, 200))
    assert bootstrap_p_value(good) < 0.01
    assert bootstrap_p_value(noise) > 0.05
    mk = lambda rets: [BTTrade("orb", "X", date(2026, 1, 1), 0, 1, 1, 1, 20, r / 100 * 20, r, "t") for r in rets]
    v = judge(summarize(mk(good)), summarize(mk(good)), summarize(mk(noise)))
    assert v.verdict == "evidence"
    v = judge(summarize(mk(noise)), summarize(mk(noise)), summarize(mk(noise)))
    assert v.verdict == "no_edge"
    v = judge(summarize(mk(good)), summarize(mk(good[:10])), summarize(mk(noise)))
    assert v.verdict == "insufficient"
    v = judge(summarize(mk(good)), summarize(mk(good)), summarize(mk([x + 1 for x in good])))
    assert v.verdict == "no_edge" and "random" in v.explanation


def test_synthetic_results_never_count_as_evidence(tmp_path):
    db = Database(tmp_path / "bt.db", "demo")
    fake = {"days": 1, "firstDay": "", "lastDay": "", "splitDay": "", "config": {},
            "strategies": {"orb": {"verdict": "evidence", "explanation": "x", "outOfSample": {}}}}
    record_results(db, fake, "synthetic (demo simulator)", {})
    assert db.query_one("SELECT verdict FROM strategy_evidence WHERE strategy='orb'")["verdict"] == "synthetic_only"
    record_results(db, fake, "alpaca historical (IEX feed)", {})
    assert db.query_one("SELECT verdict FROM strategy_evidence WHERE strategy='orb'")["verdict"] == "evidence"
    assert latest_results(db)["source"].startswith("alpaca")


def test_synthetic_backtest_finds_no_edge_in_random_data():
    """The simulator has no built-in edge; the evidence gate must not be fooled by it."""
    cal = MarketCalendar()
    syms = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN", "GOOGL"]
    days = synthetic_days(DemoMarket(syms, cal, 11), syms, date(2026, 11, 4), 25)
    res = run_backtest(days, list(ALL_STRATEGIES), BTConfig())
    assert all(r["verdict"] != "evidence" for r in res["strategies"].values())


# ------------------------------------------------------------------ evaluation upgrade (walk-forward, splits, stress)
from dkapp.backtest.engine import (judge_full, recost, replay_portfolio, risk_adjusted, split_days,  # noqa: E402
                                   walk_forward)


def _mk(rets, day=date(2026, 1, 1)):
    return [BTTrade("orb", "X", day, 0, 1, 1, 1, 20, r / 100 * 20, r, "t") for r in rets]


def _folds(n_pos, n=4):
    return [{"fold": k + 1, "days": 5, "trades": 10, "avgRetPct": 0.2 if k < n_pos else -0.2} for k in range(n)]


def test_judge_full_needs_every_check_and_never_trusts_synthetic():
    rng = np.random.default_rng(1)
    good = summarize(_mk(rng.normal(0.3, 0.4, 120)))
    noise = summarize(_mk(rng.normal(0.0, 0.4, 120)))
    assert judge_full(good, good, good, noise, _folds(4), 0.2, 0.01, synthetic=False).verdict == "evidence"
    assert judge_full(good, good, good, noise, _folds(4), 0.2, 0.01, synthetic=True).verdict == "synthetic_only"
    v = judge_full(good, good, good, noise, _folds(1), 0.2, 0.01, False)
    assert v.verdict == "no_edge" and "walk-forward" in v.explanation
    v = judge_full(good, good, good, noise, _folds(4), -0.05, 0.01, False)
    assert v.verdict == "no_edge" and "slippage doubles" in v.explanation
    v = judge_full(good, good, good, noise, _folds(4), 0.2, 0.4, False)
    assert v.verdict == "no_edge" and "adjusted p" in v.explanation
    big = summarize(_mk(rng.normal(3.0, 0.4, 120)))
    small = summarize(_mk(rng.normal(0.4, 0.4, 120)))
    v = judge_full(big, small, small, noise, _folds(4), 0.2, 0.01, False)
    assert v.verdict == "no_edge" and "overfitting" in v.explanation
    few = summarize(_mk(rng.normal(0.3, 0.4, 10)))
    assert judge_full(good, good, few, noise, _folds(4), 0.2, 0.01, False).verdict == "insufficient"


def test_splits_are_chronological_and_disjoint():
    days = [date(2026, 1, 1) + __import__("datetime").timedelta(days=i) for i in range(40)]
    tr, va, te = split_days(days)
    assert tr and va and te
    assert max(tr) < min(va) and max(va) < min(te), "test period must come strictly after training"
    assert len(tr) + len(va) + len(te) == 40
    trades = [t for i, d in enumerate(days) for t in _mk([0.1 if i < 20 else -0.1], d)]
    folds = walk_forward(trades, days, folds=4)
    assert [f["trades"] for f in folds] == [10, 10, 10, 10]
    assert folds[0]["avgRetPct"] > 0 > folds[-1]["avgRetPct"]


def test_doubling_slippage_makes_results_worse():
    t = _fill_trade("orb", "X", date(2026, 11, 4), 1, 2, 100.0, 101.0, "target", BTConfig())
    assert recost(t, BTConfig(), 2.0) < t.ret_pct
    assert recost(t, BTConfig(), 1.0) == pytest.approx(t.ret_pct)


def test_risk_adjusted_counts_idle_days_and_drawdown():
    days = [date(2026, 1, d) for d in range(1, 11)]
    ts = _mk([5.0], days[0]) + _mk([-10.0], days[3])  # +$1 then -$2 on a $100 account
    ra = risk_adjusted(ts, days)
    assert ra["totalReturnPct"] == pytest.approx((1.01 * 0.98 - 1) * 100, rel=1e-6)
    assert ra["maxDrawdownPct"] == pytest.approx(2.0, rel=1e-6)
    assert ra["sharpe"] < 0 and ra["sortino"] <= 0
    assert risk_adjusted([], days)["totalReturnPct"] == 0.0


def test_portfolio_replay_respects_slots_and_one_position_per_symbol():
    cfg = BTConfig(max_positions=2, cooldown_minutes=20)
    d = date(2026, 11, 4)
    ts = []
    for k, sym in enumerate(["A", "B", "C", "D"]):
        t = _fill_trade("orb", sym, d, 10, 40, 100.0, 101.0, "target", cfg)
        t.minute, t.exit_minute, t.score, t.stop_pct, t.target_pct = 10, 40, 0.9 - k * 0.1, 1.5, 3.0
        ts.append(t)
    again = _fill_trade("orb", "A", d, 15, 30, 100.0, 101.0, "target", cfg)
    again.minute, again.exit_minute, again.score, again.stop_pct, again.target_pct = 15, 30, 0.99, 1.5, 3.0
    ts.append(again)
    first = replay_portfolio(ts, cfg, use_ranker=False, spy_by_day={})
    assert [t.symbol for t in first] == ["A", "B"], "only two slots; strongest first; no second A while A is open"
    ranked = replay_portfolio(ts, cfg, use_ranker=True, spy_by_day={})
    assert len(ranked) <= 2


def test_missing_history_is_reported_not_fabricated():
    cal = MarketCalendar()
    syms = ["SPY", "AAPL", "MSFT"]
    days = synthetic_days(DemoMarket(syms, cal, 5), syms, date(2026, 11, 4), 8, warmup=5)
    gone = days[3]
    del gone.bars["AAPL"]
    res = run_backtest(days, list(ALL_STRATEGIES), BTConfig(random_baseline_draws=0), expected_symbols=syms)
    cov = res["coverage"]
    assert cov["symbolDaysExpected"] == 24 and cov["symbolDaysWithData"] == 23
    assert f"AAPL {gone.day}" in cov["missing"]
    from dkapp.backtest.engine import run_day
    import random
    assert not [t for t in run_day(gone, list(ALL_STRATEGIES), BTConfig(), random.Random(0)) if t.symbol == "AAPL"]
    for key in ("approaches", "benchmarks", "splits", "aiNote"):
        assert key in res
    assert set(res["approaches"]) == {"every_signal", "first_come", "ranked", "random"}
    assert res["benchmarks"]["doNothingPct"] == 0.0
    r = next(iter(res["strategies"].values()))
    for key in ("train", "validation", "test", "walkForward", "stressAvgRetPct", "pAdjusted", "riskAdjusted", "regimes"):
        assert key in r


def test_synthetic_run_is_labeled_and_never_evidence():
    cal = MarketCalendar()
    syms = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]
    days = synthetic_days(DemoMarket(syms, cal, 9), syms, date(2026, 11, 4), 12, warmup=5)
    res = run_backtest(days, list(ALL_STRATEGIES), BTConfig(), synthetic=True)
    assert res["synthetic"] is True
    assert all(r["verdict"] in ("synthetic_only", "no_edge", "insufficient") for r in res["strategies"].values())
