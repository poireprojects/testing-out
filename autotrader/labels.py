"""Ground-truth labels for the development evaluation, kept apart from everything that builds a model's input.

No prompt builder imports this module (tests/test_phase5_preflight.py checks it), and nothing here is computed
until every decision has been recorded.

* Market label (registered price horizon): did the event's STOCK rise from the 10:00:30 window to the 15:45 window
  of its session? From the cache's minute medians (a proxy, labelled as such). It is the label of the probability
  forecast for every event, traded or not, and it does not depend on whether any trade was filled in time.
* EPS label: the GAAP diluted EPS of the quarter the release reported, from the **first** 10-Q or 10-K filed on or
  after the release day whose own current period is that quarter, never from a later comparative or restatement.
  Checkability is decided from the filings alone, before any model answer is read.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime
from decimal import Decimal
from typing import Optional

from stockpilot.clock import ET

EPS_TOLERANCE = Decimal("0.01")
EPS_TAG = "EarningsPerShareDiluted"
FORMS = ("10-Q", "10-K")


def market_label(quotes, symbol: str, day: date) -> Optional[dict]:
    a, b = quotes.entry(symbol, day), quotes.exit(symbol, day)
    if a is None or b is None:
        return None
    return {"symbol": symbol, "session": day.isoformat(), "entry_mid": a.mid, "exit_mid": b.mid,
            "up": int(b.mid > a.mid), "convention": "10:00:30-10:01:30 median mid to 15:45:00-15:46:00 median mid (proxy)",
            "data_from": datetime.combine(day, dtime(10, 0, 30), ET).isoformat()}


def _quarterly(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if not r.get("start") or not r.get("end") or r.get("form") not in FORMS:
            continue
        days = (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days
        if 75 <= days <= 100:
            out.append(r)
    return out


def eps_label(facts: dict, release_day: date, max_lag_days: int = 120) -> dict:
    """{'checkable': bool, 'reason': ..., and when checkable: value, period, accession, form, fy, fp, filed}."""
    rows = facts.get("facts", {}).get("us-gaap", {}).get(EPS_TAG, {}).get("units", {}).get("USD/shares", [])
    q = _quarterly(rows)
    ends = sorted({r["end"] for r in q if date.fromisoformat(r["end"]) < release_day
                   and (release_day - date.fromisoformat(r["end"])).days <= max_lag_days})
    if not ends:
        return {"checkable": False, "reason": "no three-month diluted EPS fact for a quarter ending in the 120 days "
                                              "before the release (often a fourth quarter reported only annually)"}
    period_end = ends[-1]
    later = sorted((r for r in q if r["end"] == period_end and date.fromisoformat(r["filed"]) >= release_day),
                   key=lambda r: (r["filed"], r.get("accn", "")))
    for r in later:
        own = [x for x in rows if x.get("accn") == r.get("accn") and x.get("start") and x.get("end")]
        latest_end = max(x["end"] for x in own) if own else r["end"]
        if latest_end != period_end:
            continue                       # this filing's current period is later: the fact is a comparative
        if (date.fromisoformat(r["filed"]) - release_day).days > 100:
            break
        return {"checkable": True, "value": float(r["val"]), "period_start": r["start"], "period_end": period_end,
                "accession": r.get("accn"), "form": r.get("form"), "fy": r.get("fy"), "fp": r.get("fp"),
                "filed": r["filed"], "source": f"SEC XBRL company facts, us-gaap:{EPS_TAG} (USD/shares)",
                "rule": "first 10-Q/10-K filed on or after the release whose current period is the release quarter"}
    return {"checkable": False, "period_end": period_end,
            "reason": "no first as-filed 10-Q/10-K fact for the release quarter within 100 days of the release"}


def eps_correct(reported: Optional[float], label: dict) -> Optional[bool]:
    """Exactly within $0.01 in decimal arithmetic (the registration's rule). None when not comparable."""
    if reported is None or not label.get("checkable"):
        return None
    return abs(Decimal(repr(float(reported))) - Decimal(repr(float(label["value"])))) <= EPS_TOLERANCE
