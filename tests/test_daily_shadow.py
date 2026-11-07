"""Forward shadow record for multi-day strategies: decided in its window, never backfilled, scored after."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from stockpilot.clock import ET, MarketCalendar
from stockpilot.research.daily_shadow import (BENCH, H1, H2, SECTORS, DailyShadow, decisions_for_month, outcome_for)
from stockpilot.research.forward import ForwardLedger
from stockpilot.storage.db import Database


class FakeDaily:
    """Adjusted daily closes for the sector ETFs and SPY; refuses to return bars after `end`."""

    def __init__(self, cal: MarketCalendar, start=date(2025, 6, 2), end=date(2027, 2, 26)):
        days, d = [], start
        while d <= end:
            if cal.session(d) is not None:
                days.append(d)
            d += timedelta(days=1)
        self.days = days
        rng = np.random.default_rng(4)
        drift = {s: 0.0002 * (i + 1) for i, s in enumerate(SECTORS)} | {"SPY": 0.0004}
        self.px = {s: pd.Series(100 * np.cumprod(1 + rng.normal(mu, 0.01, len(days))), index=days) for s, mu in drift.items()}
        self.calls = []

    def get_daily_bars(self, symbols, start, end, adjusted=False):
        assert adjusted, "multi-day research must use adjusted bars"
        self.calls.append((tuple(symbols), start, end))
        e = end.astimezone(ET).date()
        out = {}
        for s in symbols:
            c = self.px[s]
            c = c[(c.index >= start.astimezone(ET).date()) & (c.index <= e)]
            if e in c.index and end < datetime.combine(e, datetime.min.time(), ET) + timedelta(hours=16):
                c = c[c.index < e]                     # today's bar is not final before the close
            idx = pd.DatetimeIndex([datetime.combine(d, datetime.min.time(), ET) for d in c.index]).tz_convert("UTC")
            out[s] = pd.DataFrame({"open": c.values, "high": c.values, "low": c.values, "close": c.values,
                                   "volume": 1e6, "vwap": c.values}, index=idx)
        return out


def _shadow(tmp_path):
    cal = MarketCalendar()
    return DailyShadow(ForwardLedger(Database(tmp_path / "paper.db", "paper")), cal, "test", "paper"), cal


def test_decision_windows_follow_the_exchange_calendar():
    cal = MarketCalendar()
    ds = {d.hypothesis: d for d in decisions_for_month(cal, 2026, 11)}
    h1 = ds[H1]
    assert h1.decision_day == date(2026, 11, 30) and h1.entry_day == date(2026, 12, 1)
    assert h1.exit_day == date(2027, 1, 4)                 # Jan 1 is a holiday: next entry is Jan 4
    assert h1.opens == datetime(2026, 11, 30, 16, 5, tzinfo=ET)
    h2 = ds[H2]
    # Nov 2026: Thanksgiving Thu 26th, half day Fri 27th -> day -2 is the 27th, exit is Dec's 3rd session
    assert h2.entry_day == date(2026, 11, 27) and h2.exit_day == date(2026, 12, 3)
    assert h2.opens == datetime(2026, 11, 25, 16, 5, tzinfo=ET)
    assert h2.closes == datetime(2026, 11, 27, 12, 45, tzinfo=ET)    # 15 minutes before the 13:00 close
    assert ds[BENCH].entry_day == h1.entry_day and ds[BENCH].exit_day == h1.exit_day


def test_records_once_inside_the_window_and_never_backfills(tmp_path):
    sh, cal = _shadow(tmp_path)
    data = FakeDaily(cal)
    before = datetime(2026, 11, 30, 15, 0, tzinfo=ET)          # D's close has not happened: H1 not due
    assert H1 not in {d.hypothesis for d in sh.due(before)}
    assert sh.record_due(datetime(2026, 11, 26, 10, 0, tzinfo=ET), data) == 1   # H2 (calendar only)
    t = datetime(2026, 11, 30, 18, 0, tzinfo=ET)
    assert sh.record_due(t, data) == 2                          # H1 + benchmark
    assert sh.record_due(t + timedelta(minutes=30), data) == 0  # once
    rows = sh.ledger.db.query("SELECT hypothesis, session_date, payload, time FROM forward_observations ORDER BY id")
    import json
    p1 = json.loads(next(r["payload"] for r in rows if r["hypothesis"] == H1))
    assert len(p1["plan"]["weights"]) == 3 and p1["inputs"]["lastBar"] == "2026-11-30"
    assert p1["simulationOnly"] and p1["noOrders"]
    # the H1 data request never reached past the decision day's close
    assert all(c[2] <= datetime(2026, 11, 30, 16, 1, tzinfo=ET) for c in data.calls)
    # a decision whose window has passed is missed, not backfilled
    late = datetime(2027, 1, 27, 9, 0, tzinfo=ET)               # December's windows closed, January's not open
    sh.record_due(late, data)
    assert not sh._recorded(H1, date(2026, 12, 31))
    st = sh.status(late)
    s1 = next(s for s in st["strategies"] if s["hypothesis"] == H1)
    assert s1["recorded"] == 1 and s1["missed"] >= 1


def test_scores_only_after_the_holding_ends_with_later_closes(tmp_path):
    sh, cal = _shadow(tmp_path)
    data = FakeDaily(cal)
    sh.record_due(datetime(2026, 11, 26, 10, 0, tzinfo=ET), data)          # H2: Nov 27 -> Dec 3
    assert sh.score_due(datetime(2026, 12, 3, 15, 0, tzinfo=ET), data) == 0  # exit close not reached
    assert sh.score_due(datetime(2026, 12, 3, 17, 0, tzinfo=ET), data) == 1
    out = {r["plan"]: r for r in sh.ledger.db.query("SELECT * FROM forward_outcomes")}
    spy = data.px["SPY"]
    gross = (spy.loc[date(2026, 12, 3)] / spy.loc[date(2026, 11, 27)] - 1) * 100
    assert out["hold"]["gross_pct"] == pytest.approx(gross)
    assert out["hold"]["net_pct"] == pytest.approx(gross - 0.13)
    assert out["spy"]["gross_pct"] == pytest.approx(gross)
    assert sh.score_due(datetime(2026, 12, 4, 17, 0, tzinfo=ET), data) == 0  # scored once
    assert sh.ledger.verify() == (True, None)


def test_outcome_needs_every_bar():
    c = pd.Series([100.0, 101.0, 102.0], index=[date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)])
    plan = {"weights": {"A": 0.5, "B": 0.5}, "entryDay": "2026-01-05", "exitDay": "2026-01-07", "costPerSidePct": 0.065}
    assert outcome_for(plan, {"A": c}) is None
    r = outcome_for(plan, {"A": c, "B": c * 2})
    assert r["hold"]["gross"] == pytest.approx(2.0) and r["hold"]["net"] == pytest.approx(2.0 - 0.13)


def test_engine_runs_the_daily_shadow_outside_demo_mode_only(tmp_path, monkeypatch):
    from tests.harness import make_rig
    rig = make_rig(tmp_path, mode="paper", day=date(2026, 11, 25), start_offset_min=400)   # after the close
    eng = rig.engine
    data = FakeDaily(eng.calendar)
    monkeypatch.setattr(eng.data, "get_daily_bars", data.get_daily_bars)
    eng._daily_shadow_tick(rig.clock.now())
    assert rig.db.scalar("SELECT COUNT(*) FROM forward_observations") == 0      # demo prices: never recorded
    monkeypatch.setattr(type(eng.data), "is_demo", property(lambda self: False))
    eng._daily_shadow_tick(rig.clock.now())
    rows = rig.db.query("SELECT hypothesis FROM forward_observations")
    assert [r["hypothesis"] for r in rows] == [H2]                               # Nov 27 entry window is open
    st = eng._forward_status()
    assert st["daily"]["strategies"][1]["recorded"] == 1
    assert rig.db.scalar("SELECT COUNT(*) FROM orders") == 0


def test_naive_and_aware_bar_timestamps_map_to_the_same_session_day():
    from stockpilot.research.daily_shadow import _session_days
    aware = pd.DatetimeIndex([datetime(2026, 11, 30, tzinfo=ET)]).tz_convert("UTC")
    naive = pd.DatetimeIndex([datetime(2026, 11, 30)])
    assert _session_days(aware) == _session_days(naive) == [date(2026, 11, 30)]
