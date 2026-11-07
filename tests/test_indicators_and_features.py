from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from stockpilot.analysis import indicators as ind
from stockpilot.analysis.features import Baseline, feature_frame, latest_features
from stockpilot.clock import ET


def make_bars(closes, start=None, volume=1000.0):
    start = start or datetime(2026, 11, 4, 9, 30, tzinfo=ET)
    idx = pd.date_range(pd.Timestamp(start).tz_convert("UTC"), periods=len(closes), freq="1min")
    c = np.asarray(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    vol = np.full(len(c), volume) if np.isscalar(volume) else np.asarray(volume, dtype=float)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.05, "low": np.minimum(o, c) - 0.05, "close": c,
                         "volume": vol, "vwap": (np.maximum(o, c) + np.minimum(o, c)) / 2}, index=idx)


def test_vwap_matches_hand_calculation():
    df = make_bars([10, 11, 12], volume=[100, 200, 300])
    vw = ind.session_vwap(df)
    expected = (df["vwap"] * df["volume"]).cumsum() / df["volume"].cumsum()
    assert np.allclose(vw.to_numpy(), expected.to_numpy())


def test_vwap_resets_each_day():
    d1 = make_bars([10, 10, 10], volume=100)
    d2 = make_bars([20, 20, 20], start=datetime(2026, 11, 5, 9, 30, tzinfo=ET), volume=100)
    vw = ind.session_vwap(pd.concat([d1, d2]))
    assert vw.iloc[3] == pytest.approx(d2["vwap"].iloc[0])


def test_rsi_extremes_and_neutral():
    up = pd.Series(np.arange(1, 40, dtype=float))
    down = pd.Series(np.arange(40, 1, -1, dtype=float))
    flat = pd.Series(np.full(30, 5.0))
    assert ind.rsi(up).iloc[-1] == pytest.approx(100.0)
    assert ind.rsi(down).iloc[-1] == pytest.approx(0.0, abs=1e-9)
    assert ind.rsi(flat).iloc[-1] == pytest.approx(50.0)


def test_rsi_known_value():
    # alternating +1/-0.5 moves -> RS = 2 in steady state -> RSI 66.7
    x = [100.0]
    for i in range(200):
        x.append(x[-1] + (1.0 if i % 2 == 0 else -0.5))
    r = ind.rsi(pd.Series(x))
    assert (r.iloc[-1] + r.iloc[-2]) / 2 == pytest.approx(66.7, abs=1.0)


def test_ema_and_atr_basic():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    e = ind.ema(s, 3)
    assert e.iloc[0] == 1.0 and e.iloc[-1] < 4.0 and e.is_monotonic_increasing
    df = make_bars([10, 10, 10, 10])
    a = ind.atr(df["high"], df["low"], df["close"], 3)
    assert a.iloc[-1] == pytest.approx(0.1, abs=1e-9)


def test_relative_volume():
    # at the close the whole typical day has traded -> rvol == cum / adv
    assert ind.relative_volume(2_000_000, 1_000_000, 390) == pytest.approx(2.0, rel=1e-6)
    assert ind.relative_volume(0, 1_000_000, 30) == 0.0
    assert ind.relative_volume(100, 0, 30) == 0.0
    # early in the day a big share of volume is expected already (U-shaped profile)
    assert ind.expected_volume_fraction(30) > 30 / 390


def test_prior_high_excludes_current_bar():
    h = pd.Series([1.0, 2.0, 3.0, 10.0])
    ph = ind.prior_high(h, 3)
    assert ph.iloc[3] == 3.0


def test_feature_frame_is_causal_no_lookahead():
    rng = np.random.default_rng(1)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, 200)))
    df = make_bars(closes, volume=rng.integers(500, 5000, 200))
    open_t = datetime(2026, 11, 4, 9, 30, tzinfo=ET)
    full = feature_frame(df, open_t, Baseline(500_000, 99.0))
    for cut in (20, 57, 120, 199):
        part = feature_frame(df.iloc[:cut], open_t, Baseline(500_000, 99.0))
        a = part.iloc[cut - 1].to_numpy(dtype=float)
        b = full.iloc[cut - 1].to_numpy(dtype=float)
        assert np.allclose(a, b, equal_nan=True), f"feature at bar {cut-1} changed when future bars were added"


def test_opening_range_only_known_after_window():
    df = make_bars(np.linspace(100, 101, 30))
    f = feature_frame(df, datetime(2026, 11, 4, 9, 30, tzinfo=ET))
    assert np.isnan(f["or_high"].iloc[13])
    assert not np.isnan(f["or_high"].iloc[14])
    assert f["or_high"].iloc[20] == pytest.approx(df["high"].iloc[:15].max())


def test_latest_features_plain_dict():
    df = make_bars(np.linspace(100, 102, 60))
    lf = latest_features(feature_frame(df, datetime(2026, 11, 4, 9, 30, tzinfo=ET), Baseline(1e6, 99)))
    assert lf["price"] == pytest.approx(102.0)
    assert lf["change_from_prev_close_pct"] == pytest.approx((102 / 99 - 1) * 100, abs=0.01)
    assert all(v is None or isinstance(v, float) for v in lf.values())
