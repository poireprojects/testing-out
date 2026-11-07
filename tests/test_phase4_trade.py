"""P4-H1 trade mechanics: NBBO fills after latency, 3% stop on later bars, unfillable entries, fees."""
from __future__ import annotations

import json
import pickle
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from stockpilot.clock import ET  # noqa: E402


def _setup(tmp_path, lows=None, entry_q=True, exit_q=True):
    import phase4_test as t
    day = date(2026, 3, 10)
    (tmp_path / "bars15").mkdir()
    idx = pd.date_range(datetime(2026, 3, 10, 9, 30, tzinfo=ET), datetime(2026, 3, 10, 15, 45, tzinfo=ET), freq="15min")
    n = len(idx)
    lows = lows or [99.5] * n
    df = pd.DataFrame({"open": [100.0] * n, "high": [101.0] * n, "low": lows, "close": [100.0] * n, "volume": [1e5] * n}, index=idx)
    pickle.dump(df, open(tmp_path / "bars15" / "ABC.pkl", "wb"))
    q = {}
    if entry_q:
        q["2026-03-10|entry"] = {"ABC": {"mid": 100.0, "halfSpreadBps": 5.0, "n": 50, "askSize": 300}}
    if exit_q:
        q["2026-03-10|exit"] = {"ABC": {"mid": 102.0, "halfSpreadBps": 2.0, "n": 50, "askSize": 300}}
    (tmp_path / "quotes.json").write_text(json.dumps(q))
    return t, t.Data(tmp_path), day


def test_entry_and_exit_pay_half_spread_and_slippage_on_each_side(tmp_path):
    t, D, day = _setup(tmp_path)
    r = t.trade(D, "ABC", "2026-03-10")
    entry = 100.0 * (1 + 10 / 1e4)            # 5 bps half spread + 5 bps slippage
    exit_ = 102.0 * (1 - 7 / 1e4)             # 2 + 5
    assert r["filled"] and r["reason"] == "15:45"
    assert r["gross"] == pytest.approx(2.0)                        # mid to mid
    assert r["net"] == pytest.approx((exit_ / entry - 1) * 100 - r["feesPct"])
    assert 0 < r["feesPct"] <= 0.15                                # a few cents on a $20 trade
    r2 = t.trade(D, "ABC", "2026-03-10", cost_mult=2.0)
    assert r2["net"] < r["net"]


def test_stop_uses_bars_after_10_and_fills_at_the_worse_of_stop_or_open(tmp_path):
    lows = [99.5] * 26
    lows[4] = 95.0                                                 # the 10:30 bar trades through the stop
    t, D, day = _setup(tmp_path, lows=lows)
    r = t.trade(D, "ABC", "2026-03-10")
    entry = 100.0 * 1.001
    assert r["reason"] == "stop"
    assert r["gross"] == pytest.approx((min(entry * 0.97, 100.0) / 100.0 - 1) * 100)
    # a low before 10:00 (the 09:30/09:45 bars) cannot stop a trade entered at 10:00:30
    lows = [99.5] * 26
    lows[0] = 90.0
    (tmp_path / "b").mkdir()
    t, D, day = _setup(tmp_path / "b", lows=lows)
    assert t.trade(D, "ABC", "2026-03-10")["reason"] == "15:45"


def test_no_nbbo_at_entry_is_a_missed_trade_not_a_fill(tmp_path):
    t, D, day = _setup(tmp_path, entry_q=False)
    r = t.trade(D, "ABC", "2026-03-10")
    assert r == {"filled": False, "why": "no NBBO at entry"}


def test_missing_exit_quote_falls_back_to_the_1545_bar_and_is_flagged(tmp_path):
    t, D, day = _setup(tmp_path, exit_q=False)
    r = t.trade(D, "ABC", "2026-03-10")
    assert r["filled"] and r["exitFallback"] and r["gross"] == pytest.approx(0.0)
