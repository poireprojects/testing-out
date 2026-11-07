"""Phase 3 research machinery: the daily portfolio simulator, the frozen rules, and the data guards."""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from stockpilot.research.portfolio import SimCosts, perf, simulate
from stockpilot.research.phase3 import h1_targets, h2_targets, tom_day_flags, tom_windows, window_returns
from stockpilot.research.stats import deflated_sharpe, expected_max_sharpe, moving_block_bootstrap, probabilistic_sharpe
from stockpilot.risk.fees import CostModel


def _days(n, start="2001-01-02"):
    return pd.bdate_range(start, periods=n)


def test_simulator_without_costs_tracks_the_asset_exactly():
    idx = _days(10)
    r = pd.DataFrame({"A": [0.01, -0.02, 0.03, 0.0, 0.01, 0.02, -0.01, 0.0, 0.01, 0.005]}, index=idx)
    res = simulate(r, {idx[0]: pd.Series({"A": 1.0})}, 1000.0, SimCosts(0, 0, None))
    # bought at the close of day 0, so day 0's return is not earned
    expected = 1000.0 * np.prod(1 + r["A"].values[1:])
    assert res.daily["equity"].iloc[-1] == pytest.approx(expected)
    assert res.daily["exposure"].iloc[-1] == pytest.approx(1.0)


def test_costs_and_daily_cent_rounding_of_regulatory_fees():
    idx = _days(3)
    r = pd.DataFrame({"A": [0.0, 0.0, 0.0]}, index=idx)
    t = {idx[0]: pd.Series({"A": 1.0}), idx[2]: pd.Series({"A": 0.0})}
    res = simulate(r, t, 20.0, SimCosts(3.0, 5.0, CostModel()))
    per_side = (1.5 + 5.0) / 1e4
    bought = (20.0 - 0.05) / (1 + per_side)             # 5 cents kept back for the day's rounded fees
    buy_cost = bought * per_side + 0.01                 # CAT on the buy rounds up to a cent
    sell_cost = bought * per_side + 0.01 + 0.01 + 0.01  # SEC, TAF (2027 rate) and CAT, each rounded up
    assert res.daily["cost"].iloc[0] == pytest.approx(buy_cost, abs=1e-6)
    assert res.daily["cost"].iloc[2] == pytest.approx(sell_cost, abs=1e-6)
    assert res.daily["equity"].iloc[-1] == pytest.approx(20.0 - buy_cost - sell_cost, abs=1e-6)
    assert (res.daily["equity"] > 0).all()
    # the same trades on $10,000 pay a far smaller share in fees
    big = simulate(r, t, 10_000.0, SimCosts(3.0, 5.0, CostModel()))
    assert big.daily["cost"].sum() / 10_000 < res.daily["cost"].sum() / 20.0


def test_buy_delay_waits_for_settlement():
    idx = _days(4)
    r = pd.DataFrame({"A": [0.0, 0.10, 0.0, 0.0]}, index=idx)
    now = simulate(r, {idx[0]: pd.Series({"A": 1.0})}, 100.0, SimCosts(0, 0, None))
    late = simulate(r, {idx[0]: pd.Series({"A": 1.0})}, 100.0, SimCosts(0, 0, None), buy_delay=1)
    assert now.daily["equity"].iloc[-1] == pytest.approx(110.0)
    assert late.daily["equity"].iloc[-1] == pytest.approx(100.0)      # bought after the +10% day


def test_no_leverage_and_no_negative_weights():
    idx = _days(3)
    r = pd.DataFrame({"A": [0.0, 0.01, 0.0], "B": [0.0, 0.02, 0.0]}, index=idx)
    res = simulate(r, {idx[0]: pd.Series({"A": 1.5, "B": 1.5})}, 100.0, SimCosts(0, 0, None))
    assert res.daily["exposure"].max() <= 1.0 + 1e-9
    res2 = simulate(r, {idx[0]: pd.Series({"A": -1.0, "B": 0.5})}, 100.0, SimCosts(0, 0, None))
    assert res2.daily["exposure"].iloc[0] == pytest.approx(0.5, abs=1e-9)   # weights then drift


def test_h1_picks_use_only_past_prices_and_trade_the_next_day():
    idx = _days(400)
    rng = np.random.default_rng(3)
    p = pd.DataFrame(np.cumprod(1 + rng.normal(0.0003, 0.01, (400, 6)), axis=0), index=idx, columns=list("ABCDEF"))
    t1, log1 = h1_targets(p)
    p2 = p.copy()
    cut = log1[0]["decision"]
    p2.loc[p2.index > cut] *= np.linspace(1, 3, (p2.index > cut).sum())[:, None] * np.array([1, 0.2, 5, 1, 1, 1])
    t2, log2 = h1_targets(p2)
    assert log1[0]["picks"] == log2[0]["picks"]                     # future prices cannot change a decision
    i = list(idx).index(cut)
    assert log1[0]["execution"] == idx[i + 1]


def test_turn_of_month_windows():
    idx = pd.bdate_range("2003-01-01", "2003-04-30")
    wins = tom_windows(idx)
    a, b = wins[0]
    jan = idx[(idx.month == 1)]
    feb = idx[(idx.month == 2)]
    assert a == jan[-2] and b == feb[2]
    f = tom_day_flags(idx, wins)
    assert f[jan[-1]] and f[feb[0]] and f[feb[2]] and not f[feb[3]] and not f[jan[-2]]
    # a hole in the data (e.g. a removed holdout) never produces a window across it
    holed = idx[(idx < "2003-02-20") | (idx > "2003-04-10")]
    assert all((y - x).days < 20 for x, y in tom_windows(holed))
    t, _ = h2_targets(idx)
    assert set(round(float(v.iloc[0])) for v in t.values()) == {0, 1}
    r = pd.Series(0.001, index=idx)
    wr = window_returns(r, wins)
    assert wr.iloc[0] == pytest.approx(1.001 ** 4 - 1)


def test_overfitting_aware_statistics():
    rng = np.random.default_rng(5)
    noise = rng.normal(0, 0.01, 2000)
    good = rng.normal(0.002, 0.01, 2000)
    assert probabilistic_sharpe(good) > 0.99 and probabilistic_sharpe(noise) < 0.99
    # more trials raise the bar
    assert expected_max_sharpe(1000, 0.0025) > expected_max_sharpe(10, 0.0025) > 0
    assert deflated_sharpe(good, 1000, 0.0025)["dsr"] < probabilistic_sharpe(good)
    assert moving_block_bootstrap(noise, 12)["p"] > 0.05 and moving_block_bootstrap(good, 12)["p"] < 0.01


def test_academic_loader_refuses_reserved_rows_and_marks_holdout_use(tmp_path):
    from stockpilot.research.academic import load_series
    from stockpilot.research.ledger import DataLedger, Period, ReservedData
    led = DataLedger([Period(date(1990, 1, 1), date(1999, 12, 31), "examined", "t"),
                      Period(date(2000, 1, 1), date(2000, 12, 31), "reserved", "t")])
    (tmp_path / "dev").mkdir()
    (tmp_path / "holdA").mkdir()
    (tmp_path / "dev" / "x.csv").write_text("date,A\n1999-12-30,1.0\n2000-01-03,2.0\n")
    with pytest.raises(ReservedData):
        load_series(tmp_path, "x", ledger=led)                       # a reserved row slipped into dev/
    (tmp_path / "dev" / "x.csv").write_text("date,A\n1999-12-30,1.0\n")
    assert load_series(tmp_path, "x", ledger=led)["A"].iloc[0] == pytest.approx(0.01)
    (tmp_path / "holdA" / "x.csv").write_text("date,A\n2000-01-03,2.0\n")
    m = tmp_path / "marker.json"
    load_series(tmp_path, "x", purpose="holdout", ledger=led, marker=m)
    assert json.loads(m.read_text())["days"] == ["2000-01-03", "2000-01-03"]
    with pytest.raises(ReservedData):
        load_series(tmp_path, "x", purpose="holdout", ledger=led, marker=m)


def test_perf_metrics():
    idx = _days(504)
    x = pd.Series(0.0004, index=idx)
    x.iloc[100] = -0.2
    p = perf(x)
    assert p["maxDrawdownPct"] == pytest.approx(-20.0, abs=0.01)
    assert p["years"] == pytest.approx(2.0)


def test_shipped_phase3_summary_matches_the_committed_results():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    run = root / "research/runs/2026-11-07-phase3"
    hold = json.loads((run / "holdout.json").read_text())
    dev = json.loads((run / "dev.json").read_text())
    s = json.loads((root / "stockpilot/assets/research/phase3-summary.json").read_text())
    rows = {r["key"]: r for r in s["rows"]}
    ho = hold["results"]["P3-H2"]
    assert rows["h2_hold"]["netPct"] == pytest.approx(ho["primary"]["meanPct"]) and rows["h2_hold"]["trades"] == ho["windows"]
    assert rows["h2_hold"]["verdict"] == hold["verdicts"]["P3-H2"]["verdict"] == "rejected"
    assert rows["h1_dev"]["netPct"] == pytest.approx(dev["H1"]["1926-2005"]["primary"]["meanPct"])
    assert dev["devVerdicts"]["P3-H1"]["pass"] is False and rows["h1_dev"]["verdict"] == "rejected"
    # the vault was never opened
    assert not (root / "research/HOLDOUT_VAULT_USED.json").exists()
    ledger = json.loads((root / "research/ledger.json").read_text())
    vault = [p for p in ledger["periods"] if p["start"] == "2020-07-27"]
    assert vault and all(p["status"] == "reserved" for p in vault)
