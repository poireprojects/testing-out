"""Phase 4 event tooling: EDGAR parsing, honest timestamps, release matching, vendor flash parsing."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from stockpilot.clock import ET, MarketCalendar
from stockpilot.research.events import (Edgar, EventTimes, FairAccessError, actionable_time, json_acceptance,
                                   match_release, parse_flash, parse_header, session_phase, stated_times)

HEADER = """&lt;SEC-HEADER&gt;0000320193-26-000024.hdr.sgml : 20261102
&lt;ACCEPTANCE-DATETIME&gt;20261102163023
ACCESSION NUMBER:		0000320193-26-000024
CONFORMED SUBMISSION TYPE:	8-K
ITEM INFORMATION:		Results of Operations and Financial Condition
ITEM INFORMATION:		Financial Statements and Exhibits
&lt;DOCUMENT&gt;
&lt;TYPE&gt;8-K
&lt;SEQUENCE&gt;1
&lt;FILENAME&gt;aapl-20261102.htm
&lt;DOCUMENT&gt;
&lt;TYPE&gt;EX-99.1
&lt;SEQUENCE&gt;2
&lt;FILENAME&gt;a8-kex991q4202609262026.htm
"""


def test_header_parsing_and_the_two_sec_clocks_agree():
    h = parse_header(HEADER)
    assert h["acceptance"] == datetime(2026, 11, 2, 16, 30, 23, tzinfo=ET)
    assert [d["type"] for d in h["documents"]] == ["8-K", "EX-99.1"]
    assert len(h["items"]) == 2
    # data.sec.gov's "...Z" value is true UTC: the same instant as the Eastern header time
    assert json_acceptance("2026-11-02T21:30:23.000Z") == h["acceptance"]


def test_fair_access_requires_a_declared_contact(tmp_path):
    with pytest.raises(FairAccessError):
        Edgar("StockPilot research", tmp_path)
    Edgar("StockPilot research someone@example.com", tmp_path)


def test_actionable_time_and_receipt():
    cal = MarketCalendar()
    assert actionable_time(datetime(2026, 11, 2, 16, 30, 23, tzinfo=ET), cal) == datetime(2026, 11, 3, 9, 31, tzinfo=ET)
    assert actionable_time(datetime(2026, 11, 3, 11, 15, 40, tzinfo=ET), cal) == datetime(2026, 11, 3, 11, 17, tzinfo=ET)
    assert actionable_time(datetime(2026, 11, 25, 17, 0, tzinfo=ET), cal) == datetime(2026, 11, 27, 9, 31, tzinfo=ET)
    acc = datetime(2026, 11, 3, 7, 1, tzinfo=ET)
    rel = acc - timedelta(minutes=16)
    t = EventTimes.build(acc, rel, cal)
    assert t.receipt == rel + timedelta(seconds=5)               # the news push beats the EDGAR poll
    assert t.actionable == datetime(2026, 11, 3, 9, 31, tzinfo=ET)
    assert EventTimes.build(acc, None, cal).receipt == acc + timedelta(seconds=60)
    assert session_phase(acc, cal) == "pre-market"


def _n(t, h):
    return {"created": t.isoformat(), "headline": h, "source": "benzinga"}


def test_release_matching_rejects_previews_and_market_wraps():
    acc = datetime(2026, 5, 5, 7, 1, tzinfo=ET)
    news = [_n(acc - timedelta(hours=10), "Insights Ahead: Pfizer's Quarterly Earnings"),
            _n(acc - timedelta(hours=3), "Stock Market Today: Futures Fall As Pfizer, Others Report"),
            _n(acc - timedelta(minutes=16), "Pfizer Q1 Adj. EPS $0.75 Beats $0.72 Estimate, Sales $14.451B Beat $13.785B Estimate"),
            _n(acc - timedelta(minutes=5), "Pfizer Reaffirms FY26 Guidance")]
    t, src, n = match_release(news, acc, "2.02,9.01", "PFIZER INC", "PFE")
    assert t == acc - timedelta(minutes=16) and src.startswith("earnings flash") and n == 4
    # an unrelated company's flash is not this disclosure
    other = [_n(acc - timedelta(minutes=2), "Merck Q1 Adj. EPS $2.10 Beats $2.00 Estimate")]
    assert match_release(other, acc, "2.02", "PFIZER INC", "PFE")[0] is None
    # a generic 8.01 'other events' filing can't be tied to a headline: unverified, never guessed
    assert match_release([_n(acc - timedelta(minutes=1), "Pfizer Announces Update")], acc, "8.01,9.01", "PFIZER INC", "PFE")[0] is None


def test_flash_parsing():
    assert parse_flash("Pfizer Q1 Adj. EPS $0.75 Beats $0.72 Estimate, Sales $14.451B Beat $13.785B Estimate") == {"eps": "beat", "sales": "beat"}
    assert parse_flash("Lucid Gr Q2 Adj. EPS $(2.78) Misses $(2.49) Estimate, Sales $259M Miss $280M Estimate") == {"eps": "miss", "sales": "miss"}
    assert parse_flash("XYZ Q3 EPS $1.00 In-Line, Sales $5B Beat $4.9B Estimate") == {"eps": "inline", "sales": "beat"}
    assert parse_flash("XYZ Q3 EPS $1.00 Up From $0.90 YoY") == {"eps": None, "sales": None}


def test_stated_times_finds_call_times_not_release_times():
    txt = "<p>NEW YORK, May 5 -- Pfizer reported results today. A conference call will be held at 10:00 a.m. EDT.</p>"
    assert stated_times(txt) == ["10:00 a.m. EDT"]
