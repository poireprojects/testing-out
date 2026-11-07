"""Regression tests for the phase-4 integrity corrections (scripts/phase4_corrected.py).

C1  a price printed before the 10:00:30 entry can never trigger the stop
C2  regulatory fees are rounded up to a cent once per day per fee type, not once per $20 trade
C3  the ML test cannot be "kept" on a handful of trades
and: the original results stay on disk unchanged; the corrected ones are labelled as not independent evidence.
"""
from __future__ import annotations

import json
import pickle
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dkapp.clock import ET  # noqa: E402

DAY = "2026-03-10"
RUN = ROOT / "research" / "runs" / "2026-11-07-phase4"


def _setup(tmp_path, lows=None, minutes=None):
    import phase4_corrected as c
    import phase4_test as o
    (tmp_path / "bars15").mkdir()
    idx = pd.date_range(datetime(2026, 3, 10, 9, 30, tzinfo=ET), datetime(2026, 3, 10, 15, 45, tzinfo=ET), freq="15min")
    n = len(idx)
    lows = lows or [99.5] * n
    df = pd.DataFrame({"open": [100.0] * n, "high": [101.0] * n, "low": lows, "close": [100.0] * n,
                       "volume": [1e5] * n}, index=idx)
    pickle.dump(df, open(tmp_path / "bars15" / "ABC.pkl", "wb"))
    q = {f"{DAY}|entry": {"ABC": {"mid": 100.0, "halfSpreadBps": 5.0, "n": 50, "askSize": 300}},
         f"{DAY}|exit": {"ABC": {"mid": 102.0, "halfSpreadBps": 2.0, "n": 50, "askSize": 300}}}
    (tmp_path / "quotes.json").write_text(json.dumps(q))
    if minutes is not None:
        (tmp_path / c.MINUTE_FILE).write_text(json.dumps({f"ABC|{DAY}": minutes}))
    return c, o, c.Data(tmp_path), o.Data(tmp_path)


def _minutes(low_at: dict[str, float], open_at: dict[str, float] | None = None):
    open_at = open_at or {}
    return [{"t": f"10:{m:02d}", "open": open_at.get(f"10:{m:02d}", 100.0), "low": low_at.get(f"10:{m:02d}", 99.9)}
            for m in range(15)]


LOWS_10 = [99.5] * 26
LOWS_10[2] = 95.0            # the 09:30, 09:45, 10:00 ... bars: the 10:00 bar (10:00:00-10:14:59) reaches the stop


# ------------------------------------------------------------------ C1
def test_low_before_entry_no_longer_stops_the_trade(tmp_path):
    c, o, D, Do = _setup(tmp_path, lows=LOWS_10, minutes=_minutes({"10:00": 95.0}))
    assert o.trade(Do, "ABC", DAY)["reason"] == "stop"            # the original defect, kept for the record
    r = c.trade(D, "ABC", DAY)
    assert r["filled"] and r["reason"] == "15:45"


def test_low_after_entry_still_stops_at_the_worse_of_stop_or_that_minutes_open(tmp_path):
    c, o, D, _ = _setup(tmp_path, lows=LOWS_10, minutes=_minutes({"10:05": 95.0}, {"10:05": 96.0}))
    r = c.trade(D, "ABC", DAY)
    stop = 100.0 * 1.001 * 0.97                                    # entry pays 5 bps half spread + 5 bps slippage
    assert r["reason"] == "stop" and r["stopResolution"] == "1min"
    assert r["gross"] == pytest.approx((min(stop, 96.0) / 100.0 - 1) * 100)


def test_sensitivity_mode_counts_the_entry_minute_but_never_fills_at_its_pre_entry_open(tmp_path):
    c, o, D, _ = _setup(tmp_path, lows=LOWS_10, minutes=_minutes({"10:00": 90.0}, {"10:00": 90.5}))
    assert c.trade(D, "ABC", DAY)["reason"] == "15:45"
    r = c.trade(D, "ABC", DAY, include_entry_minute=True)
    stop = 100.0 * 1.001 * 0.97
    assert r["reason"] == "stop" and r["gross"] == pytest.approx((stop / 100.0 - 1) * 100)


def test_without_minute_data_the_original_behaviour_is_kept_and_flagged(tmp_path):
    c, o, D, Do = _setup(tmp_path, lows=LOWS_10)
    r, ro = c.trade(D, "ABC", DAY), o.trade(Do, "ABC", DAY)
    assert r["reason"] == ro["reason"] == "stop" and r["stopResolution"] == "15min fallback"
    assert r["net"] == pytest.approx(ro["net"])


@pytest.mark.parametrize("lows", [None, [99.5] * 4 + [95.0] + [99.5] * 21])
def test_corrected_trade_equals_the_original_where_there_was_no_defect(tmp_path, lows):
    c, o, D, Do = _setup(tmp_path, lows=lows)
    r, ro = c.trade(D, "ABC", DAY), o.trade(Do, "ABC", DAY)
    for k in ("gross", "net", "feesPct", "reason"):
        assert r[k] == pytest.approx(ro[k]) if isinstance(ro[k], float) else r[k] == ro[k]


# ------------------------------------------------------------------ C2
def _t(day, sec=0.0004, taf=0.0003, cat=0.00001, before=0.0):
    return {"filled": True, "day": day, "netBeforeFees": before, "feeParts": {"sec": sec, "taf": taf, "cat": cat}}


def test_fees_are_rounded_once_per_day_per_fee_type_and_shared():
    import phase4_corrected as c
    rows = [_t("2026-03-10"), _t("2026-03-10"), _t("2026-03-10"), None, {"filled": False}, _t("2026-03-11")]
    out = c.with_daily_fees(rows)
    assert out[3] is None and out[4] == {"filled": False}
    same_day = [r["feesPct"] / 100 * 20 for r in out[:3]]
    assert sum(same_day) == pytest.approx(0.03)                    # $0.01 SEC + $0.01 TAF + $0.01 CAT for the day
    assert all(f == pytest.approx(0.01) for f in same_day)         # not $0.03 for each trade
    assert out[5]["feesPct"] == pytest.approx(0.15)                # alone on its day: the full $0.03 on $20
    assert all(r["net"] == pytest.approx(-r["feesPct"]) for r in (out[0], out[5]))
    assert "feeBasis" not in rows[0]                               # inputs are not modified


def test_daily_total_is_allocated_in_proportion_to_each_trades_unrounded_fee():
    import phase4_corrected as c
    out = c.with_daily_fees([_t("d", sec=0.003, taf=0.0, cat=0.0), _t("d", sec=0.009, taf=0.0, cat=0.0)])
    fees = [r["feesPct"] / 100 * 20 for r in out]
    assert sum(fees) == pytest.approx(0.02)                        # ceil(0.012) = 0.02
    assert fees[1] == pytest.approx(3 * fees[0])


# ------------------------------------------------------------------ C3
def test_ml_cannot_be_kept_on_two_trades():
    import phase4_corrected as c
    two = {"n": 2, "meanPct": 1.44, "p": 0.0}
    assert not c.ml_accept(two, holm_p=0.0, b2_mean=-0.3, h1_mean=-0.27)
    many = {"n": 150, "meanPct": 0.10, "p": 0.01}
    assert c.ml_accept(many, holm_p=0.03, b2_mean=-0.3, h1_mean=-0.27)
    assert not c.ml_accept(many, holm_p=0.20, b2_mean=-0.3, h1_mean=-0.27)
    assert not c.ml_accept(many | {"meanPct": -0.1}, holm_p=0.01, b2_mean=-0.3, h1_mean=-0.27)


def test_an_unevaluable_ml_test_enters_holm_with_p_one():
    import phase4_corrected as c
    fold = {"H1": {"n": 974, "meanPct": -0.19, "p": 0.97, "top5Share": None},
            "B3_priceOnlyPaired": {"meanExcessPct": -0.006, "p": 0.49}, "B1_sectorPaired": {"meanExcessPct": -0.1},
            "halfYears": {"a": {"meanPct": -0.1}}, "H1_2xCosts": {"meanPct": -0.5}, "B2_allEvents": {"meanPct": -0.2}}
    v = c.verdict({"dev": fold, "eval": fold}, {"result": {"n": 2, "meanPct": 1.44, "p": 0.0}})
    assert v["mlEvaluable"] is False and v["mlKept"] is False and v["rawP"]["ML net > 0"] == 1.0
    assert v["decision"] == "NO-GO"


# ------------------------------------------------------------------ preservation
def test_original_results_are_preserved_and_corrected_ones_are_separate_and_labelled():
    orig = json.loads((RUN / "results.json").read_text())
    assert orig["verdict"]["mlKept"] is True                      # the original record, defect included
    assert orig["verdict"]["decision"] == "NO-GO"
    corr = json.loads((RUN / "corrected" / "results_corrected.json").read_text())
    assert corr["independentEvidence"] is False
    assert corr["verdict"]["mlKept"] is False and corr["verdict"]["decision"] == "NO-GO"
    assert corr["originalVerdict"]["mlKept"] is True
    assert corr["eval"]["H1"]["meanPct"] < 0 and corr["eval"]["H1"]["ci90"][1] < 0
