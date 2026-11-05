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
