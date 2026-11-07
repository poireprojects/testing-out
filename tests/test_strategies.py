from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from stockpilot.analysis.features import Baseline, feature_frame
from stockpilot.backtest.engine import FrameView
from stockpilot.clock import ET
from stockpilot.strategies import ALL_STRATEGIES, Context

OPEN = datetime(2026, 11, 4, 9, 30, tzinfo=ET)


def bars_from(closes, volumes, wick=0.03):
    idx = pd.date_range(pd.Timestamp(OPEN).tz_convert("UTC"), periods=len(closes), freq="1min")
    c = np.asarray(closes, float)
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + wick, "low": np.minimum(o, c) - wick, "close": c,
                         "volume": np.asarray(volumes, float), "vwap": (o + c) / 2}, index=idx)


def frame(closes, volumes, adv=None):
    df = bars_from(closes, volumes)
    return feature_frame(df, OPEN, Baseline(adv if adv is not None else float(np.sum(volumes)) / 3, closes[0]))


def first_signal(strategy_id, f, ctx=None):
    s = ALL_STRATEGIES[strategy_id]
    for i in range(len(f)):
        idea = s.entry(f, i, ctx or Context("TEST", True))
        if idea is not None:
            return i, idea
    return None, None


def test_no_signals_on_flat_quiet_market():
    f = frame([100.0] * 120, [1000] * 120, adv=1e7)
    for sid in ALL_STRATEGIES:
        i, idea = first_signal(sid, f)
        assert idea is None, f"{sid} fired on a flat, quiet market"


def test_opening_range_breakout_fires_on_volume_breakout():
    rng = np.random.default_rng(0)
    closes = list(100 + rng.normal(0, 0.15, 15))  # opening range ~100
    closes += [100.1] * 10 + [101.2, 101.4]       # break above the range
    vols = [5000] * 25 + [30000, 20000]
    f = frame(closes, vols, adv=200000)
    i, idea = first_signal("orb", f)
    assert idea is not None and i == 25
    assert idea.stop_price < closes[25] < idea.target_price
    assert any("opening-range" in r for r in idea.reasons)


def noisy_uptrend(n, start, drift, noise, seed):
    r = np.random.default_rng(seed)
    steps = drift + r.normal(0, noise, n)
    steps[::3] = -abs(steps[::3]) * 0.8  # regular pullbacks keep RSI out of the extreme zone
    return list(start + np.cumsum(steps))


def test_momentum_fires_on_strong_rising_move_with_volume():
    closes = list(100 + np.random.default_rng(1).normal(0, 0.03, 20)) + noisy_uptrend(40, 100.0, 0.07, 0.03, 2)
    vols = [2000] * 20 + list(np.linspace(5000, 12000, 40))
    f = frame(closes, vols, adv=60000)
    i, idea = first_signal("momentum", f)
    assert idea is not None, "momentum should fire on a strong, volume-backed rise"
    assert idea.max_hold_minutes == 60
    assert 0 < (closes[i] - idea.stop_price) / closes[i] < 0.05


def test_momentum_refuses_overextended_straight_line():
    closes = list(np.linspace(100, 100.2, 20)) + list(np.linspace(100.25, 102.0, 30))  # RSI pinned at 100
    vols = [2000] * 20 + list(np.linspace(4000, 9000, 30))
    assert first_signal("momentum", frame(closes, vols, adv=60000))[1] is None


def test_volume_breakout_requires_volume_burst():
    base = list(100 + 0.05 * np.sin(np.arange(40)))
    jump = base + [100.13]
    quiet = frame(jump, [3000] * 41, adv=100000)
    loud = frame(jump, [3000] * 40 + [20000], adv=100000)
    assert first_signal("volume_breakout", quiet)[1] is None
    assert first_signal("volume_breakout", loud)[0] == 40


def test_vwap_reclaim():
    closes = list(np.linspace(100.5, 99.0, 25)) + [99.05, 99.1, 99.05, 99.1, 99.15, 99.2, 99.25, 99.35]
    closes += [99.9]  # crosses back above VWAP on this bar
    vols = [3000] * 33 + [15000]
    f = frame(closes, vols, adv=100000)
    assert f["close"].iat[32] <= f["vwap_s"].iat[32] < f["close"].iat[33]
    i, idea = first_signal("vwap", f)
    assert idea is not None and i == 33
    assert idea.stop_price < f["vwap_s"].iat[33]


def test_signals_do_not_change_when_future_bars_are_added():
    rng = np.random.default_rng(4)
    closes = list(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.002, 240))))
    vols = list(rng.integers(2000, 20000, 240))
    full = frame(closes, vols, adv=1e6)
    for sid, strat in ALL_STRATEGIES.items():
        for i in range(30, 200, 7):
            part = frame(closes[: i + 1], vols[: i + 1], adv=1e6)
            a = strat.entry(part, i, Context("X", True))
            b = strat.entry(full, i, Context("X", True))
            assert (a is None) == (b is None), f"{sid} at bar {i} depends on future data"
            if a is not None:
                assert a.stop_price == pytest.approx(b.stop_price) and a.score == pytest.approx(b.score)


def test_frameview_gives_identical_decisions():
    rng = np.random.default_rng(9)
    closes = list(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.003, 200))))
    vols = list(rng.integers(1000, 30000, 200))
    f = frame(closes, vols, adv=5e5)
    fv = FrameView(f)
    for sid, strat in ALL_STRATEGIES.items():
        for i in range(len(f)):
            a, b = strat.entry(f, i, Context("X", False)), strat.entry(fv, i, Context("X", False))
            assert (a is None) == (b is None), sid
            ea, eb = strat.exit(f, i, 100.0, max(0, i - 5)), strat.exit(fv, i, 100.0, max(0, i - 5))
            assert ea == eb, sid


def test_strategies_are_long_only_with_sane_brackets():
    rng = np.random.default_rng(2)
    found = 0
    for seed in range(15):
        r = np.random.default_rng(seed)
        closes = list(100 * np.exp(np.cumsum(r.normal(0.0008, 0.003, 200))))
        vols = list(r.integers(1000, 40000, 200))
        f = frame(closes, vols, adv=4e5)
        for sid, strat in ALL_STRATEGIES.items():
            for i in range(len(f)):
                idea = strat.entry(f, i, Context("X", True))
                if idea:
                    found += 1
                    px = f["close"].iat[i]
                    assert idea.stop_price < px < idea.target_price
                    assert 0 <= idea.score <= 1
    assert found > 0
