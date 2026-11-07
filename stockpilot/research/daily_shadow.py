"""Forward shadow record for multi-day strategies. It places no orders and never trades overnight.

Each frozen rule's decision is written to the append-only, hash-chained forward ledger at the moment the
app sees it, before the holding period starts. Each holding is scored only after it has ended, using
closes that came later.

Rules:
* No backfill. A decision may be recorded only inside its window. Outside the window the decision is
  missed, because the computer was off or the app was closed, and it is reported as missed.
* Decisions use adjusted daily bars (splits and dividends) up to the decision day's close only. They must
  include that close; if the bar has not arrived yet, recording waits.
* Simulation only. Nothing here can create an order, and the observations are labelled as shadow
  evidence, separate from paper fills.

Windows (US/Eastern):
* P3-H1 sector relative strength and the SPY benchmark decide on the last trading day of a month (D).
  The decision can be recorded from D's close + 5 minutes until 15 minutes before the next session's
  close, because entry is assumed at that close. The holding ends at the next month's entry close.
* P3-H2 turn of the month enters at the close of the month's second-to-last session (A) and exits at the
  close of the next month's third session. The decision is calendar-only. It can be recorded from the
  previous session's close + 5 minutes until 15 minutes before A's close.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from ..clock import ET, MarketCalendar
from .forward import ForwardLedger
from .phase3 import H1_VERSION, H2_VERSION

log = logging.getLogger(__name__)

SECTORS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
H1 = "P3-H1 sector relative strength (shadow)"
H2 = "P3-H2 turn of the month (shadow)"
BENCH = "P3 benchmark: SPY buy-and-hold (shadow)"
HYPOTHESES = (H1, H2, BENCH)
COST_PER_SIDE_PCT = (3.0 / 2 + 5.0) / 100       # the research cost model: 0.065% per side
STATUS = {
    H1: "rejected in phase-3 development (deflated Sharpe 0.34 < 0.95); observed only",
    H2: "rejected on phase-3 holdout A (2006-2020: +0.04%/window, p=0.40); observed only",
    BENCH: "benchmark for the two rules above",
}
PREREG = "research/PREREGISTRATION-2026-11-07-phase3.md"


@dataclass(frozen=True)
class Decision:
    hypothesis: str
    decision_day: date        # the day whose close the decision is based on (session_date in the ledger)
    entry_day: date           # holding starts at this day's close
    exit_day: date            # holding ends at this day's close
    opens: datetime           # recording window (inclusive)
    closes: datetime


def _sessions_in_month(cal: MarketCalendar, y: int, m: int) -> list[date]:
    d, out = date(y, m, 1), []
    while d.month == m:
        if cal.session(d) is not None:
            out.append(d)
        d += timedelta(days=1)
    return out


def _next_month(y: int, m: int) -> tuple[int, int]:
    return (y + 1, 1) if m == 12 else (y, m + 1)


def _next_session(cal: MarketCalendar, d: date) -> date:
    return cal.next_session(datetime.combine(d, datetime.max.time(), ET)).day


def decisions_for_month(cal: MarketCalendar, y: int, m: int) -> list[Decision]:
    """The three decisions belonging to month (y, m)."""
    cur = _sessions_in_month(cal, y, m)
    ny, nm = _next_month(y, m)
    nxt = _sessions_in_month(cal, ny, nm)
    if len(cur) < 3 or len(nxt) < 3:
        return []
    d_last = cur[-1]
    entry = _next_session(cal, d_last)
    exit_ = _next_session(cal, nxt[-1])
    s_last, s_entry = cal.session(d_last), cal.session(entry)
    out = [Decision(h, d_last, entry, exit_, s_last.close + timedelta(minutes=5), s_entry.close - timedelta(minutes=15))
           for h in (H1, BENCH)]
    a = cur[-2]
    prev = cal.previous_session(cal.session(a).open)
    out.append(Decision(H2, a, a, nxt[2], prev.close + timedelta(minutes=5), cal.session(a).close - timedelta(minutes=15)))
    return out


def decisions_around(cal: MarketCalendar, now: datetime) -> list[Decision]:
    t = now.astimezone(ET)
    py, pm = (t.year - 1, 12) if t.month == 1 else (t.year, t.month - 1)
    return decisions_for_month(cal, py, pm) + decisions_for_month(cal, t.year, t.month)


def _session_days(index) -> list:
    """Bar timestamps -> exchange session dates (tz-aware UTC bars are midnight ET; naive ones are dates)."""
    idx = pd.DatetimeIndex(index)
    return list(idx.tz_convert(ET).date if idx.tz is not None else idx.date)


def momentum_12_1(closes: pd.DataFrame) -> pd.Series:
    """Return from 252 sessions before the last bar to 21 sessions before it (needs 253 bars)."""
    if len(closes) < 253:
        return pd.Series(dtype=float)
    return (closes.iloc[-22] / closes.iloc[-253] - 1.0).dropna()


class DailyShadow:
    def __init__(self, ledger: ForwardLedger, calendar: MarketCalendar, app_version: str = "", mode: str = ""):
        self.ledger, self.cal = ledger, calendar
        self.app_version, self.mode = app_version, mode

    # ------------------------------------------------------------ recording
    def _recorded(self, hyp: str, day: date) -> bool:
        return self.ledger.db.query_one("SELECT id FROM forward_observations WHERE hypothesis=? AND session_date=? LIMIT 1",
                                        (hyp, day.isoformat())) is not None

    def due(self, now: datetime) -> list[Decision]:
        return [d for d in decisions_around(self.cal, now) if d.opens <= now <= d.closes and not self._recorded(d.hypothesis, d.decision_day)]

    def record_due(self, now: datetime, data) -> int:
        n = 0
        for d in self.due(now):
            try:
                if d.hypothesis == H1:
                    payload = self._h1_payload(d, data)
                    if payload is None:
                        continue
                    symbol = "SECTORS"
                else:
                    payload = {"plan": {"kind": "hold", "weights": {"SPY": 1.0}}}
                    symbol = "SPY"
                payload["plan"].update({"entryDay": d.entry_day.isoformat(), "exitDay": d.exit_day.isoformat(),
                                        "entryAt": "close", "exitAt": "close",
                                        "costPerSidePct": 0.0 if d.hypothesis == BENCH else COST_PER_SIDE_PCT})
                payload.update({"rule": {H1: H1_VERSION, H2: H2_VERSION, BENCH: "buy and hold SPY, marked monthly"}[d.hypothesis],
                                "status": STATUS[d.hypothesis], "simulationOnly": True, "noOrders": True,
                                "window": [d.opens.isoformat(), d.closes.isoformat()], "preregistration": PREREG,
                                "appVersion": self.app_version, "mode": self.mode})
                self.ledger.record(time=now, session_date=d.decision_day, symbol=symbol, hypothesis=d.hypothesis,
                                   decision="would-hold", payload=payload)
                n += 1
            except Exception:
                log.exception("daily shadow: recording %s for %s failed", d.hypothesis, d.decision_day)
        return n

    def _h1_payload(self, d: Decision, data) -> Optional[dict]:
        end = self.cal.session(d.decision_day).close + timedelta(minutes=1)
        got = data.get_daily_bars(list(SECTORS), end - timedelta(days=420), end, adjusted=True)
        closes = pd.DataFrame({s: pd.Series(df["close"].values, index=_session_days(df.index))
                               for s, df in got.items() if df is not None and not df.empty}).sort_index()
        if closes.empty:
            return None
        if closes.index[-1] != d.decision_day:          # the decision day's bar must be in (and nothing after it)
            return None
        mom = momentum_12_1(closes)
        if len(mom) < 3:
            return None
        picks = list(mom.nlargest(3).index)
        return {"plan": {"kind": "hold", "weights": {s: 1 / 3 for s in picks}},
                "inputs": {"mom_12_1": {k: float(v) for k, v in mom.items()}, "lastBar": str(closes.index[-1]),
                           "bars": int(len(closes)), "source": "Alpaca daily bars, adjusted for splits and dividends"}}

    # ------------------------------------------------------------ scoring (after the holding ended)
    def pending(self) -> list[dict]:
        rows = self.ledger.db.query(
            "SELECT o.* FROM forward_observations o LEFT JOIN forward_outcomes x ON x.obs_id = o.id "
            f"WHERE x.obs_id IS NULL AND o.hypothesis IN ({','.join('?' * len(HYPOTHESES))}) ORDER BY o.id", HYPOTHESES)
        for r in rows:
            r["p"] = json.loads(r["payload"])
        return rows

    def score_due(self, now: datetime, data) -> int:
        todo = []
        for r in self.pending():
            plan = r["p"].get("plan", {})
            ex = date.fromisoformat(plan["exitDay"])
            s = self.cal.session(ex)
            if s is not None and now >= s.close + timedelta(minutes=30):
                todo.append(r)
        if not todo:
            return 0
        syms = sorted({s for r in todo for s in r["p"]["plan"]["weights"]} | {"SPY"})
        lo = min(date.fromisoformat(r["p"]["plan"]["entryDay"]) for r in todo)
        hi = max(date.fromisoformat(r["p"]["plan"]["exitDay"]) for r in todo)
        start = datetime.combine(lo - timedelta(days=5), datetime.min.time(), ET)
        end = self.cal.session(hi).close + timedelta(minutes=1)
        got = data.get_daily_bars(syms, start, end, adjusted=True)
        closes = {}
        for s, df in got.items():
            if df is None or df.empty:
                continue
            closes[s] = pd.Series(df["close"].values, index=_session_days(df.index))
        n = 0
        for r in todo:
            res = outcome_for(r["p"]["plan"], closes)
            if res is None:
                continue
            for plan_name, v in res.items():
                self.ledger.db.insert("forward_outcomes", obs_id=r["id"], plan=plan_name, computed_at=now.isoformat(),
                                      filled=1, entry_ref=v["entry"], exit_ref=v["exit"], exit_reason=v["reason"],
                                      gross_pct=v["gross"], net_pct=v["net"], mfe_pct=v["mfe"], mae_pct=v["mae"])
            n += 1
        return n

    # ------------------------------------------------------------ reporting
    def status(self, now: datetime) -> dict:
        out = []
        for h in HYPOTHESES:
            rows = self.ledger.db.query(
                "SELECT o.session_date, x.net_pct, s.net_pct spy FROM forward_observations o "
                "LEFT JOIN forward_outcomes x ON x.obs_id = o.id AND x.plan = 'hold' "
                "LEFT JOIN forward_outcomes s ON s.obs_id = o.id AND s.plan = 'spy' "
                "WHERE o.hypothesis = ? ORDER BY o.id", (h,))
            done = [r for r in rows if r["net_pct"] is not None]
            first = date.fromisoformat(rows[0]["session_date"]) if rows else None
            missed = 0
            if first is not None:
                seen = {r["session_date"] for r in rows}
                t = now.astimezone(ET)
                y, m = first.year, first.month
                while (y, m) <= (t.year, t.month):
                    for d in decisions_for_month(self.cal, y, m):
                        if d.hypothesis == h and d.closes < now and d.decision_day >= first and d.decision_day.isoformat() not in seen:
                            missed += 1
                    y, m = _next_month(y, m)
            nets = [r["net_pct"] for r in done]
            exc = [r["net_pct"] - r["spy"] for r in done if r["spy"] is not None]
            out.append({"hypothesis": h, "status": STATUS[h], "recorded": len(rows), "missed": missed,
                        "scored": len(done), "netPct": float(np.mean(nets)) if nets else None,
                        "cumulativePct": float((np.prod([1 + v / 100 for v in nets]) - 1) * 100) if nets else None,
                        "vsSpyPct": float(np.mean(exc)) if exc else None,
                        "verdict": "too early (fewer than 24 scored holdings)" if len(done) < 24 else "see report"})
        nxt = sorted((d for d in decisions_around(self.cal, now) if d.closes >= now), key=lambda d: d.opens)
        return {"strategies": out, "next": [{"hypothesis": d.hypothesis, "opens": d.opens.isoformat(),
                                              "closes": d.closes.isoformat()} for d in nxt[:3]]}


def outcome_for(plan: dict, closes: dict[str, pd.Series]) -> Optional[dict]:
    """Holding return from the entry close to the exit close (weights fixed at entry), net of modelled
    costs on the way in and out, plus SPY over the same days for comparison. None if any bar is missing."""
    e, x = date.fromisoformat(plan["entryDay"]), date.fromisoformat(plan["exitDay"])
    w = plan["weights"]
    paths = []
    for s, wt in w.items():
        c = closes.get(s)
        if c is None or e not in c.index or x not in c.index:
            return None
        seg = c[(c.index >= e) & (c.index <= x)]
        paths.append(wt * (seg / float(c.loc[e])))
    port = sum(paths)
    gross = float(port.iloc[-1] - 1.0) * 100
    cost = 2 * float(plan.get("costPerSidePct", 0.0))
    res = {"hold": {"entry": 1.0, "exit": float(port.iloc[-1]), "reason": f"held from the close of {e} to the close of {x}",
                    "gross": gross, "net": gross - cost, "mfe": float(port.max() - 1) * 100, "mae": float(port.min() - 1) * 100}}
    spy = closes.get("SPY")
    if spy is not None and e in spy.index and x in spy.index:
        seg = spy[(spy.index >= e) & (spy.index <= x)] / float(spy.loc[e])
        g = float(seg.iloc[-1] - 1) * 100
        res["spy"] = {"entry": float(spy.loc[e]), "exit": float(spy.loc[x]), "reason": "SPY over the same days",
                      "gross": g, "net": g, "mfe": float(seg.max() - 1) * 100, "mae": float(seg.min() - 1) * 100}
    return res
