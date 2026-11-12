"""Data-integrity gate (2026-11-12): corporate-action coverage, the latest eligible N-PORT report, share counts from
filing context, and the reporting basis of trailing values. Each test states the pass criterion it checks. They fail
on the audited revision 0f19afb (see research/runs/2026-11-11-invest-pilot/READINESS.md for the run) and pass here.
Offline: synthetic fixtures, hand-built source records and two labelled REAL SEC excerpts; sockets refused."""
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from autotrader.invest import pipeline, synthetic, synthetic_raw
from autotrader.invest.accounting import Book
from autotrader.invest.adapters import edgar, market, nport
from autotrader.invest.adapters.raw import RawFile, sha256
from autotrader.invest.data import MarketData
from autotrader.invest.engine import ModelDecider
from autotrader.invest.pack import build_pack
from autotrader.invest.report import build_report
from autotrader.invest.universe import select_universe
from stockpilot.clock import ET
from tests.invest_helpers import (ALL_VOO, END, FLAT_FILING, SLOTS, by_key, dry, flat_data, marks,  # noqa: F401
                                  no_network, records)

AT = "2032-01-05T12:00:00+00:00"
REAL = Path(__file__).resolve().parent / "fixtures" / "invest_real" / "dominion"


def raw(obj, url="https://example.invalid/x", kind="edgar_document", rel="x", at=AT):
    b = obj if isinstance(obj, bytes) else (obj.encode() if isinstance(obj, str) else json.dumps(obj).encode())
    return RawFile(rel, url, at, hashlib.sha256(b).hexdigest(), kind, b)


# ================================================================== 1. corporate-action coverage
ACTIONS = "https://vendor.invalid/v1/corporate-actions"


def page(body, symbols="VOO", start="2030-11-01", end="2031-12-31", token=None, nxt=None, rel="p1", at=AT,
         types=None):
    url = (f"{ACTIONS}?symbols={symbols}&start={start}&end={end}" + (f"&types={types}" if types else "")
           + (f"&page_token={token}" if token else ""))
    return raw({"corporate_actions": body, "next_page_token": nxt}, url=url, kind="corporate_actions", rel=rel, at=at)


def cover_of(pages, registered=("VOO",)):
    return market.normalize([], pages, list(registered))["actions"]["coverage"]


def test_no_response_covers_nothing_and_an_empty_local_list_is_never_verified_absence():
    """Missing file -> fails safely: normalize([], ..., ["VOO"]) no longer reads as 'no dividends or splits'."""
    cov = cover_of([])
    assert cov["status"] == "missing" and cov["symbols"]["VOO"]["intervals"] == []
    assert "no complete corporate-action query" in cov["symbols"]["VOO"]["note"]


def test_an_explicitly_empty_fully_collected_response_is_verified_absence():
    """A fully collected, explicitly empty response succeeds: covered from its start to the day before retrieval."""
    cov = cover_of([page({})])
    (chain,) = cov["chains"]
    assert chain["status"] == "complete" and chain["covered_until"] == "2031-12-31"
    assert cov["symbols"]["VOO"]["intervals"] == [["2030-11-01", "2031-12-31"]]
    stale = cover_of([page({}, at="2031-06-15T12:00:00+00:00")])              # retrieved mid-range
    assert stale["symbols"]["VOO"]["intervals"] == [["2030-11-01", "2031-06-14"]]


@pytest.mark.parametrize("pages, reason", [
    ([page({"cash_dividends": []}, nxt="t2")], "page token 't2' was not collected"),        # pagination unfinished
    ([page({}, token="t2", rel="p2")], "the first page is missing"),
    ([page({}, nxt="t2"), page({}, token="t2", nxt="t2", rel="p2")], "pagination loops"),
    ([page({}), page({}, token="zz", rel="p2")], "outside the pagination chain"),
    ([page({}, types="cash_dividend")], "filtered by type"),
    ([raw({"message": "forbidden"}, url=f"{ACTIONS}?symbols=VOO&start=2030-11-01&end=2031-12-31",
          kind="corporate_actions")], "not a corporate-actions response"),
    ([raw({"corporate_actions": {}}, url=f"{ACTIONS}?symbols=VOO&start=2030-11-01&end=2031-12-31",
          kind="corporate_actions")], "does not state its pagination"),
])
def test_an_incomplete_or_failed_response_covers_nothing(pages, reason):
    """An incomplete paginated response fails safely."""
    cov = cover_of(pages)
    assert cov["symbols"]["VOO"]["intervals"] == [] and any(reason in c["reason"] for c in cov["chains"])


def test_an_uncovered_symbol_and_an_uncovered_date_range_fail_safely(tmp_path):
    """An uncovered symbol and an uncovered date range each block exactly the uses that need them."""
    cov = cover_of([page({}, symbols="VOO", end="2031-03-26")], registered=("VOO", "STK1"))
    assert cov["symbols"]["STK1"]["intervals"] == []
    assert cov["symbols"]["VOO"]["intervals"] == [["2030-11-01", "2031-03-26"]]
    flat_data(tmp_path / "d", actions={"splits": [], "dividends": [], "coverage": cov})
    data = MarketData(tmp_path / "d")
    assert data.actions_gap("VOO", date(2031, 3, 25), date(2031, 3, 26)) is None
    assert "not covered" in data.actions_gap("VOO", date(2031, 3, 26), date(2031, 3, 27))
    assert "not covered" in data.actions_gap("STK1", date(2031, 1, 2), date(2031, 1, 3))
    dry(tmp_path / "s", tmp_path / "d", ALL_VOO)
    k = by_key(tmp_path / "s")
    assert k["gap|voo|2031-03-27|corporate_actions"]["payload"]["missing"] == ["VOO"]
    assert "session|voo|2031-03-26|close" in k and "session|voo|2031-03-27|open" not in k
    assert k["decision|1"]["payload"]["status"] == "missing_input"                # STK1's inputs: no action record
    assert "corporate actions of STK1" in k["decision|1"]["payload"]["problems"][0]
    rep = build_report(tmp_path / "s")
    assert not rep["complete"] and any("corporate_actions (VOO)" in x for x in rep["incomplete_because"])


def test_a_raw_store_without_any_action_response_builds_but_blocks_every_use(tmp_path):
    """Missing file at the pipeline: the data directory says nothing is covered; books gap, the pack fails."""
    src = tmp_path / "raw"
    synthetic_raw.build_raw(src)
    man = json.loads((src / "MANIFEST.json").read_text())
    man["files"] = {k: v for k, v in man["files"].items() if v["kind"] != "corporate_actions"}
    (src / "MANIFEST.json").write_text(json.dumps(man))
    r = pipeline.build_data(src, tmp_path / "data", freeze=synthetic_raw.FREEZE, series_id=synthetic_raw.SERIES_ID)
    assert r["actions_coverage"]["status"] == "missing" and len(r["actions_coverage"]["uncovered"]) == 13
    dry(tmp_path / "s", tmp_path / "data", ALL_VOO, now=SLOTS[1].deadline + timedelta(hours=1))
    k = by_key(tmp_path / "s")
    assert k["gap|voo|2031-01-03|corporate_actions"]["payload"]["missing"] == ["VOO"]
    assert k["decision|1"]["payload"]["status"] == "missing_input"


VOO_DIV = {"symbol": "VOO", "ex_date": "2031-03-27", "pay_date": "2031-03-31", "amount": "1.70"}
VOO_SPLIT = {"symbol": "VOO", "ex_date": "2031-05-15", "num": 2, "den": 1}


def split_dividend_data(d: Path, cover="window"):
    """VOO 100.00 until its 2-for-1 split on 2031-05-15, 50.00 after; a $1.70 dividend (ex 03-27, paid 03-31)."""
    days = synthetic.sessions()
    voo = {x.isoformat(): {"open": "100.00", "close": "100.00"} if x.isoformat() < "2031-05-15"
           else {"open": "50.00", "close": "50.00"} for x in days}
    flat_data(d, prices_extra={"VOO": voo}, actions={"splits": [VOO_SPLIT], "dividends": [VOO_DIV]}, cover=cover)


def test_a_split_and_a_dividend_reconcile_to_hand_calculated_shares_cash_and_wealth(tmp_path):
    """Hand calculation (CAT fee 0.01 per buy session, rounded up to a cent; VOO fills at open x 1.0005):
    launch 2031-01-02: 99.99 / 100.05 -> 0.999400299 VOO, cash 0.00 (100.00 less the 0.01 fee);
    ex-date 03-27: entitlement floor(0.999400299 x 1.70) = 1.69 receivable; wealth 99.9400299 + 1.69;
    pay date 03-31: cash 1.69 -> sweep 1.68 / 100.05 -> 0.016791604 VOO (fee 0.01), cash 0.00, 1.016191903 VOO;
    split 05-15: 2 x 1.016191903 = 2.032383806 VOO at 50.00 = 101.6191903."""
    split_dividend_data(tmp_path / "d")
    dry(tmp_path / "s", tmp_path / "d", ALL_VOO)
    k = by_key(tmp_path / "s")
    launch = k["session|voo|2031-01-02|open"]["payload"]
    assert launch["after"] == {"cash": "0.00", "qty": {"VOO": "0.999400299"}, "receivables": []}
    ex = k["session|voo|2031-03-27|open"]["payload"]
    assert [a["kind"] for a in ex["actions"]] == ["dividend_entitlement"] and ex["actions"][0]["amount"] == "1.69"
    assert ex["after"]["receivables"][0]["amount"] == "1.69" and ex["after"]["cash"] == "0.00"
    m = marks(tmp_path / "s", "voo")
    assert D(m["2031-03-26"]) == D("99.9400299") and D(m["2031-03-27"]) == D("101.6300299")
    paid = k["session|voo|2031-03-31|open"]["payload"]
    assert [a["kind"] for a in paid["actions"]] == ["dividend_paid"]
    assert {o["symbol"]: (o["amount"], o["qty"]) for o in paid["trade"]["orders"]} == {"VOO": ("1.68", "0.016791604")}
    assert paid["after"] == {"cash": "0.00", "qty": {"VOO": "1.016191903"}, "receivables": []}
    sp = k["session|voo|2031-05-15|open"]["payload"]["actions"]
    assert sp == [{"kind": "split", "symbol": "VOO", "ex_date": "2031-05-15", "ratio": "2:1",
                   "qty_before": "1.016191903", "qty_after": "2.032383806"}]
    assert D(m["2031-05-14"]) == D("101.6191903") == D(m["2031-05-15"]) == D(m["2031-12-31"])
    assert marks(tmp_path / "s", "ai") == m                                       # 100% VOO: the same to the cent


def test_a_coverage_gap_resolves_after_a_restart_without_applying_any_action_twice(tmp_path):
    """Resolution after a restart: the gap is kept, resolved by an appended record, and the dividend and split are
    applied exactly once (the same hand-calculated wealth); no decision is rewritten."""
    split_dividend_data(tmp_path / "short", cover={"VOO": [["2030-11-03", "2031-03-26"]]})
    split_dividend_data(tmp_path / "full")
    dry(tmp_path / "s", tmp_path / "short", ALL_VOO, now=datetime(2031, 3, 30, 12, tzinfo=ET))
    first = (tmp_path / "s" / "ledger.jsonl").read_bytes()
    k = by_key(tmp_path / "s")
    assert {key for key in k if key.startswith("gap|")} == {f"gap|{b}|2031-03-27|corporate_actions"
                                                            for b in ("voo", "ai", "shadow")}
    dry(tmp_path / "s", tmp_path / "full", ALL_VOO)                                # restart with the collected data
    after = (tmp_path / "s" / "ledger.jsonl").read_bytes()
    assert after.startswith(first)                                                # append only
    k = by_key(tmp_path / "s")
    assert all(f"gap_resolved|{b}|2031-03-27|corporate_actions" in k for b in ("voo", "ai", "shadow"))
    acts = [a for r in records(tmp_path / "s") if r["type"] == "session_open" and r["payload"]["book"] == "voo"
            for a in r["payload"]["actions"]]
    assert [a["kind"] for a in acts] == ["dividend_entitlement", "dividend_paid", "split"]
    assert D(marks(tmp_path / "s", "voo")["2031-12-31"]) == D("101.6191903")
    old = [json.loads(x) for x in first.decode().splitlines() if json.loads(x)["type"] == "decision"]
    assert all(k[d["key"]]["payload"] == d["payload"] for d in old)               # decisions untouched
    dry(tmp_path / "s", tmp_path / "full", ALL_VOO)
    assert (tmp_path / "s" / "ledger.jsonl").read_bytes() == after                # a further rerun adds nothing
    assert build_report(tmp_path / "s")["complete"]


def test_a_shadow_only_gap_never_invalidates_the_primary_record_and_outcomes_resolve_from_later_data(tmp_path):
    """SYNTH12 (held only by the shadow) is covered through the last cutoff only: the shadow and SYNTH12's
    December-horizon outcome stop with explained gaps, while the primary record stays complete. Later-collected
    actions resolve both by appended records (outcome accounting may use later data)."""
    synthetic.build(tmp_path / "short", action_coverage={"SYNTH12": [["2030-11-03", "2031-11-28"]]})
    synthetic.build(tmp_path / "full")
    dry(tmp_path / "s", tmp_path / "short")
    k = by_key(tmp_path / "s")
    assert k["gap|shadow|2031-12-01|corporate_actions"]["payload"]["missing"] == ["SYNTH12"]
    assert not any(key.startswith(("gap|voo|", "gap|ai|")) for key in k)
    assert k["outcome_gap|10|SYNTH12|corporate_actions_not_covered"]["payload"]["end"] == "2031-12-31"
    assert "forecast_outcome|10|SYNTH12" not in k and "forecast_outcome|9|SYNTH12" in k
    rep = build_report(tmp_path / "s")
    assert rep["complete"] and not rep["diagnostics"]["shadow"]["complete"]
    miss = rep["diagnostics"]["forecasts"]["missing_rows"]
    assert [(m["slot"], m["symbol"], m["why"]) for m in miss] == [(10, "SYNTH12", "corporate actions not covered "
                                                                                 "over the horizon")]
    dry(tmp_path / "s", tmp_path / "full")
    k = by_key(tmp_path / "s")
    assert "gap_resolved|shadow|2031-12-01|corporate_actions" in k and "forecast_outcome|10|SYNTH12" in k
    rep = build_report(tmp_path / "s")
    assert rep["diagnostics"]["shadow"]["complete"] and rep["diagnostics"]["forecasts"]["missing"] == 0


def test_decision_inputs_use_only_actions_dated_by_their_cutoff_and_need_coverage_only_until_then(tmp_path):
    """Dates per purpose: a merger effective after a cutoff does not touch that month's pack; coverage that stops
    at a cutoff serves that pack and blocks the next one."""
    merger = [{"symbol": "STK1", "kind": "cash_mergers", "date": "2031-03-10", "detail": {"rate": "60.00"}}]
    flat_data(tmp_path / "d", actions={"splits": [], "dividends": [], "unsupported": merger})
    data = MarketData(tmp_path / "d")
    book = Book("ai", D("100.00"))
    feb = build_pack(SLOTS[2], book, data, SLOTS[2].cutoff.close, [])            # cutoff 2031-02-28
    apr = build_pack(SLOTS[3], book, data, SLOTS[3].cutoff.close, [])            # cutoff 2031-03-31
    assert feb.eligible() == ["STK1"] and "STK1" not in apr.eligible()
    flat_data(tmp_path / "e", cover={"STK1": [["2030-11-03", "2031-02-28"]]})
    data = MarketData(tmp_path / "e")
    assert build_pack(SLOTS[2], book, data, SLOTS[2].cutoff.close, []).eligible() == ["STK1"]
    with pytest.raises(Exception, match="corporate actions of STK1 from 2030-11-04 to 2031-03-31 are not covered"):
        build_pack(SLOTS[3], book, data, SLOTS[3].cutoff.close, [])


# ================================================================== 2. the latest eligible N-PORT report
FREEZE = datetime(2031, 3, 15, 12, tzinfo=ET)
TGT = {"series_id": "S000000001", "series_name": "Target Fund"}
OLD = ("0000000099-30-000001", "2030-09-30", "2030-11-25T10:00:00-05:00")
NEW = ("0000000099-31-000002", "2030-12-31", "2031-02-25T10:00:00-05:00")


def holdings(n=14, value=1000):
    return [{"name": f"Company {i:02d}", "lei": f"LEI{i:02d}", "title": "", "cusip": f"C{i:08d}", "isin": None,
             "ticker_identifier": None, "value_usd": str(value - i), "asset_cat": "EC"} for i in range(n)]


def report(acc, rd, sid="S000000001", hold=None, cik="0000000099"):
    return {"series_id": sid, "series_name": "Target Fund" if sid == "S000000001" else "Other Fund", "reg_cik": cik,
            "reg_name": "Trust", "report_date": rd, "submission_type": "NPORT-P",
            "holdings": holdings() if hold is None else hold,
            "provenance": {"raw_sha256": hashlib.sha256(acc.encode()).hexdigest()}}


def index(*entries, retrieved="2031-04-01T00:00:00+00:00"):
    return {"cik": 99, "entries": [{"accession": a, "form": f, "accepted_at": t, "report_date": rd}
                                   for a, f, rd, t in entries], "provenance": [{"retrieved_at": retrieved}]}


def nport_case(entries, reports, freeze=FREEZE, headers=None):
    return nport.select_report(index(*entries), reports, TGT, freeze, headers)


def test_a_newer_pre_freeze_filing_that_was_not_collected_blocks_selection():
    """Missing newer report -> blocked (the audited code selected the older one)."""
    with pytest.raises(nport.NportError, match="cannot be proven.*0000000099-31-000002 .unclassified"):
        nport_case([(OLD[0], "NPORT-P", OLD[1], OLD[2]), (NEW[0], "NPORT-P", NEW[1], NEW[2])],
                   {OLD[0]: report(OLD[0], OLD[1])})


def test_a_verified_newer_report_of_another_series_is_excluded_with_its_evidence():
    """Verified newer report for another series -> the target series' report is selected (XML or header)."""
    e = [(OLD[0], "NPORT-P", OLD[1], OLD[2]), (NEW[0], "NPORT-P", NEW[1], NEW[2])]
    s = nport_case(e, {OLD[0]: report(OLD[0], OLD[1]), NEW[0]: report(NEW[0], NEW[1], sid="S000000002")})
    assert s["accession"] == OLD[0]
    other = next(x for x in s["reconciliation"] if x["accession"] == NEW[0])
    assert (other["status"], other["series_id"], other["series_source"]) == ("excluded", "S000000002", "report XML")
    by_header = nport_case(e, {OLD[0]: report(OLD[0], OLD[1])}, headers={NEW[0]: ["S000000002"]})
    assert by_header["accession"] == OLD[0]
    assert next(x for x in by_header["reconciliation"] if x["accession"] == NEW[0])["series_source"] == "filing header"
    with pytest.raises(nport.NportError, match="uncollected"):                  # the header says it is the target
        nport_case(e, {OLD[0]: report(OLD[0], OLD[1])}, headers={NEW[0]: ["S000000001"]})


def test_the_newer_eligible_report_is_selected_and_a_post_freeze_one_is_excluded():
    """Newer eligible target-series report -> selected; post-freeze public report -> excluded."""
    late = ("0000000099-31-000009", "2031-01-31", "2031-03-20T10:00:00-04:00")
    s = nport_case([(OLD[0], "NPORT-P", OLD[1], OLD[2]), (NEW[0], "NPORT-P", NEW[1], NEW[2]),
                    (late[0], "NPORT-P", late[1], late[2])],
                   {OLD[0]: report(OLD[0], OLD[1]), NEW[0]: report(NEW[0], NEW[1]), late[0]: report(late[0], late[1])})
    assert (s["accession"], s["report_date"], s["public_by"]) == (NEW[0], "2030-12-31", "2031-03-02T00:00:00-05:00")
    st = {x["accession"]: (x["status"], x["why"]) for x in s["reconciliation"]}
    assert st[late[0]] == ("excluded", "accepted at or after the freeze") and st[OLD[0]][0] == "excluded"


def test_acceptance_alone_does_not_prove_publication_before_the_freeze():
    """Deferred disclosure: accepted before the freeze but within 60 days of its period end -> blocked."""
    e = [(OLD[0], "NPORT-P", OLD[1], OLD[2]), (NEW[0], "NPORT-P", NEW[1], NEW[2])]
    r = {OLD[0]: report(OLD[0], OLD[1]), NEW[0]: report(NEW[0], NEW[1])}
    with pytest.raises(nport.NportError, match="not certainly public before it: the 60-day date is 2031-03-02"):
        nport_case(e, r, freeze=datetime(2031, 3, 1, 12, tzinfo=ET))
    with pytest.raises(nport.NportError, match="retrieved 2031-02-01.*before the freeze"):
        nport.select_report(index(*e, retrieved="2031-02-01T00:00:00+00:00"), r, TGT, FREEZE)


@pytest.mark.parametrize("bad, match", [
    (dict(sid=None), "names no series ID"),                                     # unresolved series
    (dict(hold=[]), "incomplete: the report XML lists no holdings"),            # incomplete holdings
    (dict(cik="0000000042"), "registrant CIK 0000000042 is not the index's 99"),
])
def test_unresolved_series_incomplete_holdings_or_another_registrant_block_selection(bad, match):
    with pytest.raises(nport.NportError, match=match):
        nport_case([(OLD[0], "NPORT-P", OLD[1], OLD[2]), (NEW[0], "NPORT-P", NEW[1], NEW[2])],
                   {OLD[0]: report(OLD[0], OLD[1]), NEW[0]: report(NEW[0], NEW[1], **bad)})


def test_amendments_resolve_by_reporting_period_under_the_documented_interpretation():
    """A public same-period NPORT-P/A supersedes the original; an amendment of an older period never makes that
    period the latest; a superseding amendment without holdings blocks."""
    amd = ("0000000099-31-000005", "2030-12-31", "2031-03-05T10:00:00-05:00")
    old_amd = ("0000000099-31-000006", "2030-09-30", "2031-03-06T10:00:00-05:00")
    e = [(OLD[0], "NPORT-P", OLD[1], OLD[2]), (NEW[0], "NPORT-P", NEW[1], NEW[2]),
         (amd[0], "NPORT-P/A", amd[1], amd[2]), (old_amd[0], "NPORT-P/A", old_amd[1], old_amd[2])]
    r = {OLD[0]: report(OLD[0], OLD[1]), NEW[0]: report(NEW[0], NEW[1]), amd[0]: report(amd[0], amd[1]),
         old_amd[0]: report(old_amd[0], old_amd[1])}
    s = nport_case(e, r)
    assert (s["accession"], s["form"]) == (amd[0], "NPORT-P/A")
    st = {x["accession"]: x["status"] for x in s["reconciliation"]}
    assert st == {OLD[0]: "excluded", NEW[0]: "superseded", amd[0]: "selected", old_amd[0]: "excluded"}
    with pytest.raises(nport.NportError, match="lists no holdings"):
        nport_case(e, dict(r, **{amd[0]: report(amd[0], amd[1], hold=[])}))


def test_ties_between_versions_or_duplicate_originals_block_and_equal_values_rank_by_cusip():
    dup = ("0000000099-31-000003", "2030-12-31", "2031-02-26T10:00:00-05:00")
    e = [(NEW[0], "NPORT-P", NEW[1], NEW[2]), (dup[0], "NPORT-P", dup[1], dup[2])]
    with pytest.raises(nport.NportError, match="2 original NPORT-P reports for 2030-12-31 with different holdings"):
        nport_case(e, {NEW[0]: report(NEW[0], NEW[1]), dup[0]: report(dup[0], dup[1], hold=holdings(value=2000))})
    same = nport_case(e, {NEW[0]: report(NEW[0], NEW[1]), dup[0]: report(dup[0], dup[1])})   # a refiled duplicate
    assert same["accession"] == dup[0]
    tie = [(NEW[0], "NPORT-P", NEW[1], NEW[2]), (dup[0], "NPORT-P/A", dup[1], NEW[2])]
    with pytest.raises(nport.NportError, match="accepted at the same time"):
        nport_case(tie, {NEW[0]: report(NEW[0], NEW[1]), dup[0]: report(dup[0], dup[1])})
    rows = [dict(h, value_usd="500") for h in holdings(3)][::-1]
    assert [m["cusip"] for m in select_universe(rows, {h["cusip"]: {"symbol": "X", "cik": "1"} for h in rows}, 3)] == \
        ["C00000000", "C00000001", "C00000002"]


def test_issuers_without_an_lei_are_not_merged_and_the_universe_needs_twelve_distinct_issuers():
    """N-PORT writes <lei>N/A</lei> (273 rows in the real iShares Russell 1000 report): such issuers are told apart by
    name; fewer than twelve distinct issuers, a selected holding without a CUSIP or a repeated CUSIP is refused."""
    rows = [dict(h, lei="N/A") for h in holdings(14)]
    picked = select_universe(rows, {h["cusip"]: {"symbol": h["cusip"], "cik": "1"} for h in rows}, 12)
    assert len({m["cusip"] for m in picked}) == 12                              # the audited code kept one
    sel = {"accession": "A", "accepted_at": "t", "report_date": "2030-12-31", "series_id": "S000000001",
           "report": {"holdings": holdings(11) + [dict(holdings(1)[0], cusip="C99999999", name="Company 00 Class B")]}}
    with pytest.raises(nport.NportError, match="11 distinct common-equity issuer"):
        nport.review_table(sel, {}, 12)
    no_cusip = dict(sel, report={"holdings": [dict(h, cusip=None) if i == 0 else h
                                              for i, h in enumerate(holdings(14))]})
    with pytest.raises(nport.NportError, match="has no CUSIP"):
        nport.review_table(no_cusip, {}, 12)
    twice = dict(sel, report={"holdings": holdings(13) + [dict(holdings(1)[0], lei="LEIX", name="Another")]})
    with pytest.raises(nport.NportError, match="CUSIP C00000000 appears on two"):
        nport.review_table(twice, {}, 12)


def test_duplicate_issuer_mappings_and_a_short_review_table_block_the_freeze():
    sel = {"accession": "A", "accepted_at": "t", "report_date": "2030-12-31", "series_id": "S000000001",
           "report": {"holdings": holdings(14)}}
    table = nport.review_table(sel, {}, 12)
    assert {r["status"] for r in table} == {"unresolved"}                         # no ticker identifier: no guess
    rows = [{"cusip": r["cusip"], "symbol": f"S{i}", "cik": str(100 + i), "approved": True}
            for i, r in enumerate(table)]
    review = {"reviewer": "test", "reviewed_at": "2031-03-10", "rows": rows}
    assert len(nport.frozen_universe(table, review, 12)["members"]) == 12
    for key, dup in (("symbol", "S0"), ("cik", "100")):
        bad = dict(review, rows=[dict(r, **{key: dup}) if i == 1 else r for i, r in enumerate(rows)])
        with pytest.raises(nport.NportError, match=f"duplicate issuer mapping: {key}"):
            nport.frozen_universe(table, bad, 12)
    with pytest.raises(nport.NportError, match="11 row"):
        nport.frozen_universe(table[:11], review, 12)


# ================================================================== 3. share counts from filing context
def doc(facts, contexts):
    return ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
            'xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
            'xmlns:dei="http://xbrl.sec.gov/dei/2024" xmlns:us-gaap="http://fasb.org/us-gaap/2024"><body>'
            '<div style="display:none"><ix:header><ix:resources>' + contexts + '</ix:resources></ix:header></div>'
            '<p>Cover&nbsp;page: ' + facts + '</p></body></html>')


def ctx(i, member=None, cik="0000000001", inst="2031-07-25", axis="us-gaap:StatementClassOfStockAxis"):
    seg = (f'<xbrli:segment><xbrldi:explicitMember dimension="{axis}">{member}</xbrldi:explicitMember>'
           '</xbrli:segment>') if member else ""
    return (f'<xbrli:context id="{i}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}'
            f'</xbrli:identifier>{seg}</xbrli:entity><xbrli:period><xbrli:instant>{inst}</xbrli:instant>'
            '</xbrli:period></xbrli:context>')


def fact(c, v, scale="0"):
    return (f'<ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="{c}" unitRef="shares" '
            f'format="ixt:num-dot-decimal" decimals="INF" scale="{scale}">{v}</ix:nonFraction>')


A, B = "us-gaap:CommonClassAMember", "us-gaap:CommonClassBMember"
SHARE_CASES = {
    "one class": (fact("c", "1,000"), ctx("c"), ("resolved", D(1000), 1)),
    "repeated identical facts": (fact("c", "1,000") + fact("c", "1,000") + fact("c2", "1", scale="3"),
                                 ctx("c") + ctx("c2"), ("resolved", D(1000), 1)),
    "equal-sized distinct classes": (fact("a", "500") + fact("b", "500"), ctx("a", A) + ctx("b", B),
                                     ("resolved", D(1000), 2)),
    "unequal classes": (fact("a", "800") + fact("b", "200"), ctx("a", A) + ctx("b", B), ("resolved", D(1000), 2)),
    "aggregate plus components": (fact("t", "1,000") + fact("a", "800") + fact("b", "200"),
                                  ctx("t") + ctx("a", A) + ctx("b", B), ("resolved", D(1000), 2)),
    "aggregate that its components contradict": (fact("t", "1,000") + fact("a", "800"), ctx("t") + ctx("a", A),
                                                 ("unresolved", "disagree", None)),
    "missing dimensions": (fact("t1", "800") + fact("t2", "200"), ctx("t1") + ctx("t2"),
                           ("unresolved", "two different counts for the same context", None)),
    "another legal entity": (fact("t", "1,000") + fact("v", "7"), ctx("t") +
                             ctx("v", "d:SubsidiaryMember", axis="dei:LegalEntityAxis"), ("resolved", D(1000), 1)),
    "unknown dimension": (fact("t", "1,000"), ctx("t", "x:FooMember", axis="x:SeriesAxis"),
                          ("unresolved", "unrecognized dimension", None)),
}


@pytest.mark.parametrize("case", list(SHARE_CASES))
def test_share_counts_resolve_by_context_or_say_unresolved(case):
    """One class, repeated identical facts, equal-sized distinct classes, unequal classes, aggregate plus components
    and missing dimensions each give a hand-verified value or an explicit unresolved status."""
    facts_xml, ctx_xml, (status, expect, classes) = SHARE_CASES[case]
    r = edgar.shares_outstanding(edgar.inline_facts(doc(facts_xml, ctx_xml)), 1, "A1")
    assert r["status"] == status
    if status == "resolved":
        assert (r["value"], r["share_classes"]) == (expect, classes)
    else:
        assert expect in r["reason"] and "value" not in r


def test_company_facts_must_agree_with_the_cover_page_and_never_stand_in_for_it():
    cf = [{"concept": edgar.SHARES, "accession": "A1", "end": "2031-07-25", "value": "999"}]
    r = edgar.shares_outstanding(edgar.inline_facts(doc(fact("c", "1,000"), ctx("c"))), 1, "A1", cf)
    assert r["status"] == "unresolved" and "company facts report 999" in r["reason"]
    ok = edgar.shares_outstanding(edgar.inline_facts(doc(fact("c", "1,000"), ctx("c"))), 1, "A1",
                                  [dict(cf[0], value="1000")])
    assert ok["status"] == "resolved" and ok["company_facts_rows"] == 1
    assert edgar.shares_outstanding(None, 1, "A1", cf)["status"] == "unresolved"
    bad = edgar.inline_facts("<html xmlns:ix='http://www.xbrl.org/2013/inlineXBRL'><body><p>x</body></html>")
    assert bad["status"] == "unparseable" and edgar.shares_outstanding(bad, 1, "A1")["status"] == "unresolved"


def _pack_for(tmp_path, facts, status=None):
    f = dict(FLAT_FILING, facts=facts) | ({"facts_status": status} if status else {})
    flat_data(tmp_path, filings=[f])
    return build_pack(SLOTS[1], Book("ai", D("100.00")), MarketData(tmp_path), SLOTS[1].cutoff.close, [])


def test_no_ambiguous_count_reaches_the_rendered_request_as_an_established_fact(tmp_path):
    """An unresolved count keeps the candidate (no 'legitimately unavailable' bypass) and suppresses market value
    and price/sales; several classes suppress market value (only one class's price is collected)."""
    base = {k: v for k, v in FLAT_FILING["facts"].items() if k not in ("shares_outstanding", "share_classes")}
    why = {"shares_outstanding": {"status": "unresolved", "reason": "two different counts for the same context"}}
    pk = _pack_for(tmp_path / "u", base, why)
    assert pk.eligible() == ["STK1"]
    cov = next(c for c in pk.coverage if c["symbol"] == "STK1")
    assert cov["status"] == "complete"
    assert cov["unresolved_inputs"] == {"shares_outstanding": why["shares_outstanding"]["reason"]}
    v = pk.get("V.STK1").text
    assert "shares outstanding unresolved (class structure not established from the filing)" in v
    assert "market value not computed (share count unresolved)" in v and "price/sales not computed" in v
    assert "P/E 125.00" in v                                                     # price over EPS: still valid
    prompt = ModelDecider(["STK1"]).request(pk)["messages"][0]["content"]
    row = next(line for line in prompt.splitlines() if line.startswith("Valuation of STK1"))
    assert "1,000,000,000" not in prompt and not re.search(r"market value [\d,]+ million", row)
    assert not re.search(r"price/sales \d", row)
    multi = _pack_for(tmp_path / "m", dict(FLAT_FILING["facts"], share_classes="2")).get("V.STK1").text
    assert "shares outstanding 1,000,000,000 in 2 share classes" in multi
    assert "market value not computed (2 share classes; only this class's price is collected)" in multi
    single = _pack_for(tmp_path / "s", dict(FLAT_FILING["facts"])).get("V.STK1").text
    assert "market value 50,000 million USD" in single and "price/sales 12.50" in single
    flat_data(tmp_path / "x", filings=[dict(FLAT_FILING, facts=base)])            # absent, not unresolved: failure
    c = MarketData(tmp_path / "x").coverage("STK1", SLOTS[1].cutoff.close, SLOTS[1].cutoff.day)
    assert c["status"] == "collection_failure" and "without its XBRL facts" in c["reason"]


def test_a_conflicting_cover_page_in_the_raw_store_becomes_an_explicit_unresolved_input(tmp_path):
    src = tmp_path / "raw"
    synthetic_raw.build_raw(src)
    man = json.loads((src / "MANIFEST.json").read_text())
    rel = next(r for r, m in sorted(man["files"].items()) if m["kind"] == "edgar_document" and "/990000000231" in r
               and r.endswith("20310331.htm"))                               # SYNTH02's first-quarter 2031 10-Q
    b = (src / rel).read_bytes()
    b = b.replace(b'contextRef="cover0"', b'contextRef="cover0"', 1).replace(
        b"</ix:nonFraction> shares of common stock", b"</ix:nonFraction> shares of common stock and "
        b'<ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="cover0" unitRef="shares" '
        b'decimals="INF">5</ix:nonFraction> more', 1)
    (src / rel).write_bytes(b)
    man["files"][rel]["sha256"] = sha256(b)
    (src / "MANIFEST.json").write_text(json.dumps(man))
    r = pipeline.build_data(src, tmp_path / "d", freeze=synthetic_raw.FREEZE, series_id=synthetic_raw.SERIES_ID)
    (key,) = r["unresolved_facts"]
    assert key.startswith("SYNTH02 ") and r["unresolved_facts"][key] == ["shares_outstanding"]
    data = MarketData(tmp_path / "d")
    c = data.coverage("SYNTH02", SLOTS[5].cutoff.close, SLOTS[5].cutoff.day)     # the June pack uses that 10-Q
    assert c["status"] == "complete" and "two different counts" in c["unresolved_inputs"]["shares_outstanding"]


# ================================================================== 4. reporting basis of trailing values
def _dominion():
    prov = json.loads((REAL / "PROVENANCE.json").read_text())["files"]
    rf = lambda name, kind: RawFile(name, prov[name]["source_url"], prov[name]["retrieved_at"],  # noqa: E731
                                    hashlib.sha256((REAL / name).read_bytes()).hexdigest(), kind,
                                    (REAL / name).read_bytes())
    idx = edgar.parse_submissions([rf("dominion_submissions_excerpt.json", "edgar_submissions")])
    return edgar.parse_companyfacts(rf("dominion_netincome_excerpt.json", "edgar_companyfacts"), idx)


@pytest.mark.parametrize("asof, value, fy_basis_prior, comparative", [
    ("2025-08-01T10:36:31-04:00", "2583000000", "1021000000", "966000000"),
    ("2025-10-31T11:49:54-04:00", "2655000000", "1979000000", "1900000000"),
])
def test_the_dominion_trailing_net_income_mixes_reporting_bases_and_is_unresolved(asof, value, fy_basis_prior,
                                                                                    comparative):
    """REAL SEC data (Dominion Energy, CIK 715957). The production method combines FY2024 as first reported
    (2,124M, 10-K 0000950170-25-028387) with 2024 comparatives recast in the 2025 10-Qs. On the 10-K's own
    quarterly figures the prior-year period is 1,021M (H1: 441 + 580) and 1,979M (9M: 441 + 580 + 958), not 966M and
    1,900M: a mixed reporting basis. The value is kept for review and marked unresolved."""
    cf = _dominion()
    r = edgar.ttm(cf["facts"], edgar.NET_INCOME, "USD", datetime.fromisoformat(asof))
    assert (r["method"], str(r["value"]), r["status"]) == ("fiscal_year_plus_ytd_minus_prior_ytd", value, "unresolved")
    fy, ytd, prior = r["components"]
    assert (fy["accession"], fy["value"]) == ("0000950170-25-028387", "2124000000")
    assert prior["value"] == comparative and prior["accession"] == ytd["accession"]
    b = r["basis"]
    assert b["status"] == "mixed" and b["annual_report_prior_period_value"] == fy_basis_prior
    assert "mixed reporting basis" in r["unresolved_reason"]
    k25 = edgar.ttm(cf["facts"], edgar.NET_INCOME, "USD", datetime.fromisoformat("2026-02-24T00:00:00-05:00"))
    assert (k25["method"], str(k25["value"]), k25["status"]) == ("annual", "2998000000", "resolved")


def test_consistent_restatements_resolve_and_rounding_is_not_a_restatement():
    def F(start, end, val, acc, public):
        return {"concept": "Revenues", "role": "revenue", "unit": "USD", "start": start, "end": end,
                "value": str(val), "accession": acc, "form": "10-Q", "public_at": public}
    k = "2031-02-10T16:05:00-05:00"
    q1 = "2031-05-06T16:05:00-04:00"
    base = [F("2030-01-01", "2030-12-31", 4000_000_000, "K30", k), F("2030-01-01", "2030-03-31", 900_000_000, "K30", k),
            F("2031-01-01", "2031-03-31", 1100_000_000, "Q131", q1)]
    same = edgar.ttm(base + [F("2030-01-01", "2030-03-31", 900_000_000, "Q131", q1)], edgar.REVENUE, "USD",
                     datetime.fromisoformat(q1))
    assert same["status"] == "resolved" and same["basis"]["test"] == "annual report's own prior-period figures"
    recast = edgar.ttm(base + [F("2030-01-01", "2030-03-31", 850_000_000, "Q131", q1)], edgar.REVENUE, "USD",
                       datetime.fromisoformat(q1))
    assert recast["status"] == "unresolved" and recast["basis"]["status"] == "mixed"
    precise = [F("2029-01-01", "2029-12-31", 3_600_000_000, "K29", "2030-02-10T16:05:00-05:00"),
               F("2029-01-01", "2029-03-31", 3_727_400_000, "Q129", "2029-05-06T16:05:00-04:00"),
               F("2030-01-01", "2030-03-31", 3_800_000_000, "Q130", "2030-05-06T16:05:00-04:00"),
               F("2029-01-01", "2029-03-31", 3_727_000_000, "Q130", "2030-05-06T16:05:00-04:00")]   # in millions now
    r = edgar.ttm(precise, edgar.REVENUE, "USD", datetime.fromisoformat("2030-05-06T16:05:00-04:00"))
    assert r["restatements_seen"] == [] and r["status"] == "resolved"
    revised = precise[:3] + [F("2029-01-01", "2029-03-31", 3_729_000_000, "Q130", "2030-05-06T16:05:00-04:00")]
    r = edgar.ttm(revised, edgar.REVENUE, "USD", datetime.fromisoformat("2030-05-06T16:05:00-04:00"))
    assert len(r["restatements_seen"]) == 1 and r["status"] == "unresolved"
    assert r["basis"]["status"] == "not_established"


def test_an_unresolved_trailing_value_is_named_and_its_ratios_are_not_computed(tmp_path):
    base = {k: v for k, v in FLAT_FILING["facts"].items() if k != "eps_diluted_ttm"}
    pk = _pack_for(tmp_path, base, {"eps_diluted_ttm": {"status": "unresolved", "reason": "mixed reporting basis: x"}})
    v = pk.get("V.STK1").text
    assert "diluted EPS, trailing four quarters unresolved (reporting basis not established)" in v
    assert "P/E not computed (EPS unresolved)" in v and "market value 50,000 million USD" in v


def test_proxy_statement_figures_never_enter_trailing_values():
    """Company facts also carry DEF 14A pay-versus-performance figures rounded to millions (Chevron's FY2025 net
    income: 12,299M in the 10-K, 12,300M in the proxy). Only periodic reports count."""
    def page_(rows):
        cols = ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "items", "primaryDocument")
        recent = {c: [r.get(c, "") for r in rows] for c in cols}
        return raw({"cik": "0000000001", "name": "T", "filings": {"recent": recent, "files": []}})
    idx = edgar.parse_submissions([page_([
        {"accessionNumber": "K", "form": "10-K", "acceptanceDateTime": "2026-02-24T21:05:00.000Z"},
        {"accessionNumber": "P", "form": "DEF 14A", "acceptanceDateTime": "2026-04-07T21:05:00.000Z"}])])
    cf = {"cik": 1, "facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        {"start": "2025-01-01", "end": "2025-12-31", "val": 12299000000, "accn": "K"},
        {"start": "2025-01-01", "end": "2025-12-31", "val": 12300000000, "accn": "P"}]}}}}}
    out = edgar.parse_companyfacts(raw(cf), idx)
    assert out["excluded"]["not_periodic_report"] == 1
    r = edgar.ttm(out["facts"], edgar.NET_INCOME, "USD", datetime.fromisoformat("2026-05-01T00:00:00-04:00"))
    assert str(r["value"]) == "12299000000" and r["restatements_seen"] == []
