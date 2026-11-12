"""Offline adapters (autotrader/invest/adapters): raw store and transports, EDGAR submissions, headers and
reconciliation, XBRL trailing values, passages, N-PORT selection and mapping review, prices and corporate actions.
Pure functions over saved bytes: hand-built cases, the synthetic raw fixture and two REAL SEC excerpts (labelled).
No network: sockets are refused for the whole module."""
import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from autotrader.invest import synthetic_raw
from autotrader.invest.accounting import Book
from autotrader.invest.adapters import edgar, market, nport
from autotrader.invest.adapters.raw import (NetworkDisabled, OfflineTransport, RawError, RawFile, RawStore,
                                            RefusingTransport, write_raw_store)
from autotrader.invest.data import MarketData
from autotrader.invest.pack import build_pack
from autotrader.invest.registration import et
from autotrader.invest.report import build_report
from stockpilot.clock import ET
from tests.invest_helpers import ALL_VOO, SLOTS, by_key, dry, flat_data, no_network  # noqa: F401

REAL = Path(__file__).resolve().parent / "fixtures" / "invest_real"
AT = "2032-01-05T12:00:00+00:00"


def raw(data, url="https://example.invalid/x", kind="edgar_document", rel="x"):
    b = data if isinstance(data, bytes) else (data.encode() if isinstance(data, str) else json.dumps(data).encode())
    return RawFile(rel, url, AT, hashlib.sha256(b).hexdigest(), kind, b)


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    d = tmp_path_factory.mktemp("raw")
    synthetic_raw.build_raw(d)
    return RawStore(d)


# ------------------------------------------------------------------ raw store and transports
def test_altered_raw_bytes_are_refused_and_nothing_live_can_be_fetched(tmp_path):
    write_raw_store(tmp_path, {"a.json": (b'{"x": 1}', "https://data.sec.gov/a", AT, "edgar_tickers")}, synthetic=True)
    st = RawStore(tmp_path)
    assert st.get("a.json").json() == {"x": 1}
    assert st.get("a.json").provenance() == {"raw_file": "a.json", "source_url": "https://data.sec.gov/a",
                                             "retrieved_at": AT, "raw_sha256": hashlib.sha256(b'{"x": 1}').hexdigest()}
    (tmp_path / "a.json").write_bytes(b'{"x": 2}')
    with pytest.raises(RawError, match="altered raw input"):
        st.get("a.json")
    with pytest.raises(NetworkDisabled):
        RefusingTransport().fetch("https://data.sec.gov/submissions/CIK0000000001.json")
    with pytest.raises(NetworkDisabled, match="not in the raw store"):
        OfflineTransport(st).fetch("https://data.sec.gov/never-saved")
    with pytest.raises(RawError, match="altered"):
        OfflineTransport(st).fetch("https://data.sec.gov/a")


@pytest.mark.parametrize("entry, expect", [
    ({"kind": "web_page"}, "unknown kind"),
    ({"retrieved_at": "2032-01-05T12:00:00"}, "without a time zone"),
    ({"source_url": ""}, "missing source_url"),
])
def test_a_raw_manifest_without_full_provenance_is_refused(tmp_path, entry, expect):
    write_raw_store(tmp_path, {"a": (b"x", "https://data.sec.gov/a", AT, "edgar_document")}, synthetic=True)
    man = json.loads((tmp_path / "MANIFEST.json").read_text())
    man["files"]["a"].update(entry)
    (tmp_path / "MANIFEST.json").write_text(json.dumps(man))
    with pytest.raises(RawError, match=expect):
        RawStore(tmp_path)
    with pytest.raises(RawError, match="no raw manifest"):
        RawStore(tmp_path / "missing")


# ------------------------------------------------------------------ submissions and headers
def _page(rows, cik=1, older=()):
    cols = ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "items", "primaryDocument")
    return {"cik": f"{cik:010d}", "name": "Test Co", "tickers": ["TST"],
            "filings": {"recent": {c: [r.get(c, "") for r in rows] for c in cols},
                        "files": [{"name": n} for n in older]}}


ROW = {"accessionNumber": "0000000001-31-000001", "filingDate": "2031-02-10", "reportDate": "2030-12-31",
       "acceptanceDateTime": "2031-02-10T21:05:00.000Z", "form": "10-K", "primaryDocument": "k.htm"}


def test_submissions_pages_merge_and_acceptance_times_are_converted_from_utc_to_eastern():
    older = {c: [v] for c, v in dict(ROW, accessionNumber="0000000001-30-000009", reportDate="2030-09-30",
                                     acceptanceDateTime="2030-07-01T20:05:00.000Z", form="10-Q").items()}
    idx = edgar.parse_submissions([raw(_page([ROW], older=["CIK0000000001-submissions-001.json"])), raw(older)])
    assert [e["accession"] for e in idx["entries"]] == ["0000000001-30-000009", "0000000001-31-000001"]
    assert idx["entries"][1]["accepted_at"] == "2031-02-10T16:05:00-05:00"        # EST
    assert idx["entries"][0]["accepted_at"] == "2030-07-01T16:05:00-04:00"        # EDT
    assert len(idx["provenance"]) == 2 and idx["cik"] == 1
    clash = {c: [v] for c, v in dict(ROW, acceptanceDateTime="2031-02-10T22:05:00.000Z").items()}
    with pytest.raises(edgar.EdgarError, match="two different acceptance times"):
        edgar.parse_submissions([raw(_page([ROW])), raw(clash)])
    short = _page([ROW])
    short["filings"]["recent"]["form"] = []
    with pytest.raises(edgar.EdgarError, match="column form"):
        edgar.parse_submissions([raw(short)])
    with pytest.raises(edgar.EdgarError, match="not a main submissions page"):
        edgar.parse_submissions([raw(older)])


def _hdr(acc="0000000001-31-000001", cik=1, form="10-K", t=None):
    t = t or et(date(2031, 2, 10), 16, 5)
    return raw(synthetic_raw._header(acc, cik, "Test Co", form, t, [], [(form, "k.htm")]), kind="edgar_header")


@pytest.mark.parametrize("header, expect", [
    (_hdr(), []),
    (_hdr(acc="0000000001-31-000002"), ["header accession 0000000001-31-000002 is not 0000000001-31-000001"]),
    (_hdr(cik=2), ["header issuer CIK 2 is not 1"]),
    (_hdr(t=et(date(2031, 2, 10), 16, 6)), ["header acceptance 2031-02-10T16:06:00-05:00 differs from the index "
                                            "2031-02-10T16:05:00-05:00"]),
    (_hdr(form="10-K/A"), ["header form 10-K/A is not 10-K"]),
])
def test_headers_must_reconcile_with_the_index_on_accession_issuer_and_acceptance(header, expect):
    e = edgar.parse_submissions([raw(_page([ROW]))])["entries"][0]
    assert edgar.reconcile(e, edgar.parse_header(header), 1) == expect


# ------------------------------------------------------------------ trailing four quarters
def F(start, end, val, acc, public, concept="Revenues", unit="USD"):
    return {"concept": concept, "role": "revenue", "unit": unit, "start": start, "end": end, "value": str(val),
            "accession": acc, "form": "10-Q", "filed": public[:10], "public_at": public}


T = lambda s: datetime.fromisoformat(s)  # noqa: E731
CAL = [  # a calendar-year company: annual, year-to-date and quarterly facts with repeated comparatives
    F("2029-01-01", "2029-12-31", 3600, "K29", "2030-02-10T16:05:00-05:00"),
    F("2030-01-01", "2030-09-30", 2950, "Q330", "2030-11-05T16:05:00-05:00"),
    F("2029-01-01", "2029-09-30", 2650, "Q330", "2030-11-05T16:05:00-05:00"),
    F("2030-01-01", "2030-12-31", 4000, "K30", "2031-02-10T16:05:00-05:00"),
    F("2030-01-01", "2030-03-31", 900, "Q130", "2030-05-06T16:05:00-04:00"),
    F("2031-01-01", "2031-03-31", 1100, "Q131", "2031-05-06T16:05:00-04:00"),
    F("2030-01-01", "2030-03-31", 900, "Q131", "2031-05-06T16:05:00-04:00"),     # duplicate context, same value
    F("2031-01-01", "2031-06-30", 2300, "Q231", "2031-08-05T16:05:00-04:00"),
    F("2030-01-01", "2030-06-30", 1900, "Q231", "2031-08-05T16:05:00-04:00"),
    F("2031-04-01", "2031-06-30", 1200, "Q231", "2031-08-05T16:05:00-04:00"),
    F("2030-04-01", "2030-06-30", 1000, "Q231", "2031-08-05T16:05:00-04:00"),
]


@pytest.mark.parametrize("asof, method, value, end", [
    ("2031-02-10T16:04:59-05:00", "fiscal_year_plus_ytd_minus_prior_ytd", 3600 + 2950 - 2650, "2030-09-30"),
    ("2031-02-10T16:05:00-05:00", "annual", 4000, "2030-12-31"),                 # public at its acceptance second
    ("2031-05-06T16:05:00-04:00", "fiscal_year_plus_ytd_minus_prior_ytd", 4000 + 1100 - 900, "2031-03-31"),
    ("2031-08-05T16:05:00-04:00", "fiscal_year_plus_ytd_minus_prior_ytd", 4000 + 2300 - 1900, "2031-06-30"),
])
def test_ttm_uses_only_facts_public_by_the_time_and_never_sums_cumulative_values(asof, method, value, end):
    r = edgar.ttm(CAL, edgar.REVENUE, "USD", T(asof))
    assert (r["method"], r["value"], r["period_end"]) == (method, D(value), end)
    assert r["restatements_seen"] == []                     # the repeated Q1 2030 comparative is not a restatement


def test_year_to_date_only_facts_are_never_treated_as_quarters():
    ytd_only = [F("2031-01-01", "2031-03-31", 1100, "A", "2031-05-06T16:05:00-04:00"),
                F("2031-01-01", "2031-06-30", 2300, "B", "2031-08-05T16:05:00-04:00"),
                F("2031-01-01", "2031-09-30", 3500, "C", "2031-11-04T16:05:00-05:00")]
    assert edgar.ttm(ytd_only, edgar.REVENUE, "USD", T("2031-12-01T00:00:00-05:00")) is None


def test_52_53_week_calendars_chain_quarters_and_match_prior_periods_within_a_week():
    q = [F("2030-09-29", "2030-12-28", 1000, "K", "2031-02-20T16:05:00-05:00"),
         F("2030-12-29", "2031-03-29", 1010, "Q1", "2031-05-01T16:05:00-04:00"),
         F("2031-03-30", "2031-06-28", 1020, "Q2", "2031-08-01T16:05:00-04:00"),
         F("2031-06-29", "2031-10-04", 1100, "Q3", "2031-11-10T16:05:00-05:00")]   # a 14-week quarter (98 days)
    r = edgar.ttm(q, edgar.REVENUE, "USD", T("2031-11-11T00:00:00-05:00"))
    assert (r["method"], r["value"]) == ("four_quarters", D(4130)) and [c["accession"] for c in r["components"]] == \
        ["Q3", "Q2", "Q1", "K"]
    # after a 53-week year the comparable prior year-to-date starts 371 days earlier, not 364
    ytd = [F("2029-12-30", "2030-12-28", 4000, "FY", "2031-02-20T16:05:00-05:00"),
           F("2030-12-29", "2031-06-28", 2100, "H1", "2031-08-01T16:05:00-04:00"),
           F("2029-12-23", "2030-06-22", 1900, "H1", "2031-08-01T16:05:00-04:00")]
    r = edgar.ttm(ytd, edgar.REVENUE, "USD", T("2031-08-02T00:00:00-04:00"))
    assert (r["method"], r["value"]) == ("fiscal_year_plus_ytd_minus_prior_ytd", D(4200))


def test_a_restatement_filed_after_a_cutoff_never_enters_an_earlier_value():
    facts = CAL + [F("2030-01-01", "2030-12-31", 4200, "K30A", "2031-04-15T16:05:00-04:00")]   # a 10-K/A
    before = edgar.ttm(facts, edgar.REVENUE, "USD", T("2031-04-15T16:00:00-04:00"))
    assert (before["value"], before["restatements_seen"]) == (D(4000), [])
    after = edgar.ttm(facts, edgar.REVENUE, "USD", T("2031-04-15T16:05:00-04:00"))
    assert after["value"] == D(4200) and after["components"][0]["accession"] == "K30A"
    assert after["restatements_seen"] == [{"period": ("2030-01-01", "2030-12-31"), "was": "4000", "now": "4200",
                                           "by": "K30A"}]


def test_eps_is_labelled_an_approximation_and_company_facts_alone_never_resolve_a_share_count():
    eps = [dict(F(f["start"], f["end"], D(f["value"]) / 1000, f["accession"], f["public_at"],
                  "EarningsPerShareDiluted", "USD/shares")) for f in CAL]
    r = edgar.ttm(eps, edgar.EPS_DILUTED, "USD/shares", T("2031-08-06T00:00:00-04:00"))
    assert r["value"] == D("4.4") and "approximation" in r["approximation"] and r["status"] == "resolved"
    # company facts carry no dimensions: 800 and 200 for one date could be two classes, or a class and a total
    sh = [dict(F(None, "2031-07-25", 800, "Q231", "2031-08-05T16:05:00-04:00", edgar.SHARES, "shares"), start=None),
          dict(F(None, "2031-07-25", 200, "Q231", "2031-08-05T16:05:00-04:00", edgar.SHARES, "shares"), start=None)]
    s = edgar.shares_outstanding(None, 1, "Q231", sh)
    assert s["status"] == "unresolved" and "value" not in s and "cover-page contexts" in s["reason"]
    not_ix = edgar.inline_facts("<html><body><p>1,000 shares outstanding</p></body></html>")
    assert edgar.shares_outstanding(not_ix, 1, "Q231", sh)["reason"].startswith("cover-page contexts unavailable")


def test_company_facts_keep_fact_level_provenance_and_exclude_unknown_accessions_and_units():
    idx = edgar.parse_submissions([raw(_page([ROW]))])
    cf = {"cik": 1, "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [{"start": "2030-01-01", "end": "2030-12-31", "val": 4000000000,
                                        "accn": "0000000001-31-000001", "form": "10-K", "filed": "2031-02-10"},
                                       {"start": "2030-01-01", "end": "2030-12-31", "val": 1,
                                        "accn": "0000000001-31-999999", "form": "10-K", "filed": "2031-02-10"}],
                               "EUR": [{"start": "2030-01-01", "end": "2030-12-31", "val": 1,
                                        "accn": "0000000001-31-000001"}]}},
        "EarningsPerShareDiluted": {"units": {"pure": [{"start": "2030-01-01", "end": "2030-12-31", "val": 1,
                                                        "accn": "0000000001-31-000001"}]}}}}}
    proxy = dict(ROW, accessionNumber="0000000001-31-000007", form="DEF 14A",
                 acceptanceDateTime="2031-03-20T21:05:00.000Z")
    idx = edgar.parse_submissions([raw(_page([ROW, proxy]))])
    cf["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(      # pay-versus-performance figure, rounded
        {"start": "2030-01-01", "end": "2030-12-31", "val": 4000000000, "accn": "0000000001-31-000007",
         "form": "DEF 14A"})
    out = edgar.parse_companyfacts(raw(cf, "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json"), idx)
    assert out["excluded"] == {"accession_not_in_index": 1, "not_periodic_report": 1, "unit": 2}
    assert len(out["facts"]) == 1
    f = out["facts"][0]
    assert (f["accession"], f["public_at"], f["start"], f["end"]) == ("0000000001-31-000001",
                                                                        "2031-02-10T16:05:00-05:00", "2030-01-01",
                                                                        "2030-12-31")
    assert out["provenance"]["source_url"].endswith("CIK0000000001.json") and out["provenance"]["raw_sha256"]
    with pytest.raises(edgar.EdgarError, match="CIK 2"):
        edgar.parse_companyfacts(raw(dict(cf, cik=2)), idx)


def _independent_ttm(per: dict) -> D:
    """Four ~3-month periods back from the latest end, each a reported quarter or, for a fiscal fourth quarter that
    filings report only inside the year, the fiscal year minus its nine-month year-to-date (a second arithmetic
    path, independent of edgar.ttm's method order)."""
    rows = list(per.values())
    days = lambda f: (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days + 1  # noqa: E731
    near = lambda a, b: abs((date.fromisoformat(a) - b).days) <= 7  # noqa: E731
    end, total = max(date.fromisoformat(f["end"]) for f in rows), D(0)
    for _ in range(4):
        q = next((f for f in rows if 80 <= days(f) <= 100 and near(f["end"], end)), None)
        if q is not None:
            total += D(q["value"])
            end = date.fromisoformat(q["start"]) - timedelta(days=1)
            continue
        fy = next(f for f in rows if 350 <= days(f) <= 380 and near(f["end"], end))
        nine = next(f for f in rows if 250 <= days(f) <= 290 and f["start"] == fy["start"])
        total += D(fy["value"]) - D(nine["value"])
        end = date.fromisoformat(nine["end"])
    return total


def test_the_fixture_fiscal_calendars_agree_with_an_independent_quarter_sum(store):
    """Calendar, September, June and 52/53-week fiscal years in the raw fixture, plus two share classes and a
    wrong-unit EPS row: at every 10-Q and 10-K acceptance, edgar.ttm equals an independently derived sum of four
    quarters, and no fact is public before its own period ends."""
    subs = {}
    for f in store.of_kind("edgar_submissions"):
        subs.setdefault(f.source_url.split("CIK")[1][:10], []).append(f)
    checks, methods = 0, set()
    for f in store.of_kind("edgar_companyfacts"):
        cik = f.source_url.split("CIK")[1][:10]
        pages = sorted(subs[cik], key=lambda p: ("filings" not in p.json(), p.source_url))
        idx = edgar.parse_submissions(pages)
        cf = edgar.parse_companyfacts(f, idx)
        assert (cf["excluded"]["unit"] > 0) == cik.endswith("05")          # SYNTH05's 'pure' EPS rows only
        assert all(T(x["public_at"]).date().isoformat() >= x["end"] for x in cf["facts"])
        for e in idx["entries"]:
            if e["form"] not in ("10-K", "10-Q") or e["accepted_at"] < "2030-06-01":
                continue
            t = T(e["accepted_at"])
            r = edgar.ttm(cf["facts"], edgar.REVENUE, "USD", t)
            per, _ = edgar._known(cf["facts"], "Revenues", "USD", t)
            assert r["value"] == _independent_ttm(per), (cik, e["accession"])
            methods.add(r["method"])
            checks += 1
    assert checks == 72 and methods == {"annual", "fiscal_year_plus_ytd_minus_prior_ytd"}   # 12 companies x 6 reports


# ------------------------------------------------------------------ passages
def test_passages_are_verbatim_skip_the_contents_page_and_include_adverse_disclosures():
    doc = synthetic_raw._doc("Test Co", "10-K", D(1234), 5, 12, 33, 17)
    ps = edgar.passages(doc, "10-K")
    paras = edgar.paragraphs(doc)
    assert [p["section"] for p in ps] == ["MD&A: figures", "MD&A: figures", "adverse disclosure (impairment)",
                                          "risk factors"]
    assert all(p["text"] == paras[p["paragraph"]] for p in ps)                  # the document's own paragraphs
    assert ps[0]["text"].startswith("Test Co is an invented") is False and "$1,234 million" in ps[0]["text"]
    assert "$33 million" in ps[2]["text"] and ps[3]["text"].startswith("Our largest customer accounted for 17%")
    assert ps == edgar.passages(doc, "10-K")                                     # deterministic
    short = doc.replace(" Customer concentration also limits our pricing power at contract renewals.", "")
    assert [p["section"] for p in edgar.passages(short, "10-K")][-1] != "risk factors"   # documented fallback
    release = edgar.passages(synthetic_raw._release("Test Co", D(1234), 5), "8-K")
    assert [p["section"] for p in release] == ["earnings release: figures", "earnings release: figures"]
    assert "decline of 3%" in release[1]["text"]


def test_the_highest_priority_adverse_keyword_wins():
    filler = "This paragraph is long enough to count as a passage under the registered rule of one hundred chars. "
    doc = ("<p>" + filler + "We recorded an impairment of goodwill.</p><p>" + filler +
           "There is substantial doubt about our ability to continue as a going concern.</p>")
    ps = edgar.passages(doc, "10-Q")
    assert any(p["section"] == "adverse disclosure (going concern)" and "going concern" in p["text"] for p in ps)


# ------------------------------------------------------------------ N-PORT
def _fund(store):
    reports = {}
    for f in store.of_kind("nport_xml"):
        acc = f.source_url.split("/")[-2]
        reports[f"{acc[:10]}-{acc[10:12]}-{acc[12:]}"] = nport.parse_nport(f)
    fund = edgar.parse_submissions([f for f in store.of_kind("edgar_submissions") if "9900000099" in f.source_url])
    return fund, reports


TARGET = {"series_id": synthetic_raw.SERIES_ID, "series_name": synthetic_raw.SERIES}


def test_nport_selection_uses_the_latest_report_public_before_the_freeze(store):
    fund, reports = _fund(store)
    s = nport.select_report(fund, reports, TARGET, synthetic_raw.FREEZE)
    assert (s["report_date"], s["accepted_at"][:10], s["series_id"]) == ("2030-09-30", "2030-11-27", "S000099001")
    rec = {x["accession"]: x for x in s["reconciliation"]}
    other = rec["9900000099-30-000002"]                               # newer, pre-freeze, another series
    assert other["status"] == "excluded" and other["series_id"] == "S000099002" and "another series" in other["why"]
    post = rec["9900000099-31-000003"]
    assert post["status"] == "excluded" and "after the freeze" in post["why"]
    assert all(x["raw_sha256"] for x in rec.values())
    later = nport.select_report(fund, reports, TARGET, datetime(2031, 6, 1, tzinfo=ET))
    assert later["report_date"] == "2030-12-31"
    with pytest.raises(nport.NportError, match="no SEC series ID"):
        nport.select_report(fund, reports, {"series_name": synthetic_raw.SERIES}, synthetic_raw.FREEZE)
    with pytest.raises(nport.NportError, match="no S000099999 NPORT-P public before"):
        nport.select_report(fund, reports, {"series_id": "S000099999"}, synthetic_raw.FREEZE)
    with pytest.raises(nport.NportError):
        nport.select_report(fund, reports, TARGET, datetime(2030, 11, 1, tzinfo=ET))


def test_the_review_table_never_invents_a_mapping_and_an_unapproved_review_stops_the_freeze(store):
    fund, reports = _fund(store)
    s = nport.select_report(fund, reports, TARGET, synthetic_raw.FREEZE)
    tickers = store.of_kind("edgar_tickers")[0].json()
    table = nport.review_table(s, tickers, 12)
    assert len(table) == 12 and {r["status"] for r in table} == {"needs_review"}     # proposals, never final
    assert table[0]["source"]["nport_accession"] == s["accession"]
    assert "99SYNPF00" not in {r["cusip"] for r in table} and "99SYN01C0" not in {r["cusip"] for r in table}
    gone = {k: v for k, v in tickers.items() if v["ticker"] != "SYNTH03"}
    r3 = next(r for r in nport.review_table(s, gone, 12) if r["cusip"] == "99SYN0300")
    assert r3["status"] == "unresolved" and r3["proposed_cik"] is None and "not in EDGAR's ticker list" in r3["notes"][0]
    renamed = {k: dict(v, title="Other Name Holdings") if v["ticker"] == "SYNTH04" else v for k, v in tickers.items()}
    r4 = next(r for r in nport.review_table(s, renamed, 12) if r["cusip"] == "99SYN0400")
    assert r4["status"] == "needs_review" and "names differ" in r4["notes"][0]
    no_id = dict(s, report=dict(s["report"], holdings=[dict(h, ticker_identifier=None) if h["cusip"] == "99SYN0200"
                                                       else h for h in s["report"]["holdings"]]))
    r2 = next(r for r in nport.review_table(no_id, tickers, 12) if r["cusip"] == "99SYN0200")
    assert r2["status"] == "unresolved" and r2["proposed_symbol"] is None
    review = store.of_kind("universe_review")[0].json()
    u = nport.frozen_universe(table, review)
    assert [m["symbol"] for m in u["members"]] == [f"SYNTH{i:02d}" for i in range(1, 13)] and u["review"]["fixture"]
    with pytest.raises(nport.NportError, match="1 selected holding"):
        nport.frozen_universe(table, dict(review, rows=review["rows"][1:]))
    with pytest.raises(nport.NportError, match="lack an approved mapping"):
        nport.frozen_universe(table, dict(review, rows=[dict(r, approved=False) for r in review["rows"]]))
    with pytest.raises(nport.NportError, match="no reviewer"):
        nport.frozen_universe(table, dict(review, reviewer=""))
    with pytest.raises(nport.NportError, match="without a symbol and CIK"):
        nport.frozen_universe(table, dict(review, rows=[dict(r, cik=None) for r in review["rows"]]))


# ------------------------------------------------------------------ prices and corporate actions
def bars(sym, rows, adjustment="raw"):
    return raw({"symbol": sym, "adjustment": adjustment, "bars": [{"t": t, "o": o, "c": c} for t, o, c in rows]},
               kind="price_bars", rel=f"bars/{sym}")


ACTIONS = "https://vendor.invalid/v1/corporate-actions"


def actions(body, symbols="ABC", start="2031-01-01", end="2031-12-31", token=None, nxt=None, rel="acts/1"):
    """One corporate-actions response page; the request (symbols, dates, page token) is its source URL."""
    url = f"{ACTIONS}?symbols={symbols}&start={start}&end={end}" + (f"&page_token={token}" if token else "")
    return raw({"corporate_actions": body, "next_page_token": nxt}, url=url, kind="corporate_actions", rel=rel)


SPLIT = {"forward_splits": [{"symbol": "ABC", "ex_date": "2031-02-18", "old_rate": 1, "new_rate": 2}]}


def test_unadjusted_bars_with_a_split_step_pass_and_adjusted_ones_are_refused():
    good = [("2031-02-14", "100.00", "100.00"), ("2031-02-18", "50.10", "50.00")]
    out = market.normalize([bars("ABC", good)], [actions(SPLIT)], ["ABC"])
    assert out["actions"]["splits"] == [{"symbol": "ABC", "ex_date": "2031-02-18", "num": 2, "den": 1}]
    assert out["prices"]["ABC"]["2031-02-18"] == {"open": "50.10", "close": "50.00"}
    adjusted = [("2031-02-14", "50.00", "50.00"), ("2031-02-18", "50.10", "50.00")]       # no step: already adjusted
    with pytest.raises(market.MarketError, match="refusing double adjustment"):
        market.normalize([bars("ABC", adjusted)], [actions(SPLIT)], ["ABC"])
    with pytest.raises(market.MarketError, match="not unadjusted"):
        market.normalize([bars("ABC", good, adjustment="split")], [], ["ABC"])


def test_symbol_changes_merge_and_conflicting_bars_are_refused():
    acts = {"name_changes": [{"old_symbol": "OLD", "new_symbol": "NEW", "process_date": "2031-03-02"}]}
    out = market.normalize([bars("OLD", [("2031-02-27", "10", "10")]), bars("NEW", [("2031-03-02", "10.5", "11")]),
                            bars("XYZ", [("2031-02-27", "1", "1")])], [actions(acts, symbols="NEW")], ["NEW"])
    assert out["prices"] == {"NEW": {"2031-02-27": {"open": "10", "close": "10"},
                                     "2031-03-02": {"open": "10.5", "close": "11"}}}              # XYZ: not registered
    assert out["actions"]["symbol_changes"] == [{"old_symbol": "OLD", "new_symbol": "NEW", "process_date": "2031-03-02",
                                                 "registered_as": "NEW"}]
    # NEW was queried, OLD was not: dates before the change are not covered (OLD's actions there are unknown)
    assert out["actions"]["coverage"]["symbols"]["NEW"]["intervals"] == [["2031-03-02", "2031-12-31"]]
    both = market.normalize([], [actions(acts, symbols="NEW,OLD")], ["NEW"])
    assert both["actions"]["coverage"]["symbols"]["NEW"]["intervals"] == [["2031-01-01", "2031-12-31"]]
    with pytest.raises(market.MarketError, match="two different bars"):
        market.normalize([bars("OLD", [("2031-02-27", "10", "10")]), bars("NEW", [("2031-02-27", "9", "9")])],
                         [actions(acts, symbols="NEW")], ["NEW"])


def test_dividends_keep_their_dates_and_unsupported_actions_are_listed_not_modelled():
    acts = {"cash_dividends": [{"symbol": "ABC", "ex_date": "2031-03-27", "payable_date": "2031-03-31", "rate": "0.35"}],
            "cash_mergers": [{"acquiree_symbol": "ABC", "effective_date": "2031-06-01", "rate": "60.00"}],
            "spin_offs": [{"symbol": "ABC", "ex_date": "2031-07-01"}]}
    out = market.normalize([bars("ABC", [("2031-03-26", "10", "10")])], [actions(acts)], ["ABC"])
    assert out["actions"]["dividends"] == [{"symbol": "ABC", "ex_date": "2031-03-27", "pay_date": "2031-03-31",
                                            "amount": "0.35"}]
    assert [(u["kind"], u["date"]) for u in out["actions"]["unsupported"]] == [("cash_mergers", "2031-06-01"),
                                                                               ("spin_offs", "2031-07-01")]
    early = {"cash_dividends": [dict(acts["cash_dividends"][0], payable_date="2031-03-20")]}
    with pytest.raises(market.MarketError, match="before its ex-date"):
        market.normalize([], [actions(early)], ["ABC"])


def test_an_unsupported_action_is_a_data_gap_for_a_holding_and_an_exclusion_for_a_candidate(tmp_path):
    merger = {"splits": [], "dividends": [], "unsupported": [{"symbol": "STK1", "kind": "cash_mergers",
                                                              "date": "2031-03-10", "detail": {"rate": "60.00"}}]}
    flat_data(tmp_path / "held", actions=merger)
    dry(tmp_path / "s", tmp_path / "held", {**ALL_VOO, 1: {"kind": "decision", "positions": [("STK1", "buy", 8)]}})
    k = by_key(tmp_path / "s")
    assert k["gap|ai|2031-03-10|unsupported_action"]["payload"]["missing"] == ["STK1"]
    rep = build_report(tmp_path / "s")
    assert not rep["complete"] and any("unsupported_action" in x for x in rep["incomplete_because"])
    flat_data(tmp_path / "cand", actions=merger)
    data = MarketData(tmp_path / "cand")
    pk = build_pack(SLOTS[3], Book("ai", D("100.00")), data, SLOTS[3].cutoff.close + timedelta(minutes=20), [])
    c = next(x for x in pk.coverage if x["symbol"] == "STK1")
    assert c["status"] == "unavailable" and "unsupported corporate action (cash_mergers, 2031-03-10)" in c["reason"]
    assert "STK1" not in pk.eligible()


# ------------------------------------------------------------------ REAL SEC excerpts (Starbucks, CIK 829224)
def _real():
    prov = json.loads((REAL / "PROVENANCE.json").read_text())["files"]
    sub = REAL / "starbucks_submissions_excerpt.json"
    cf = REAL / "starbucks_companyfacts_excerpt.json"
    rf = lambda p, kind: RawFile(p.name, prov[p.name]["source_url"], prov[p.name]["retrieved_at"],  # noqa: E731
                                 hashlib.sha256(p.read_bytes()).hexdigest(), kind, p.read_bytes())
    idx = edgar.parse_submissions([rf(sub, "edgar_submissions")])
    return idx, edgar.parse_companyfacts(rf(cf, "edgar_companyfacts"), idx)


def test_real_sec_excerpts_parse_with_eastern_acceptance_times_and_no_early_facts():
    idx, cf = _real()
    assert idx["cik"] == 829224 and len(idx["entries"]) == 10
    e = {x["accession"]: x for x in idx["entries"]}
    assert e["0000829224-23-000058"]["accepted_at"] == "2023-11-17T17:31:27-05:00"     # UTC 22:31:27 -> EST
    assert e["0000829224-24-000024"]["accepted_at"] == "2024-04-30T16:10:50-04:00"     # UTC 20:10:50 -> EDT
    assert cf["excluded"] == {"accession_not_in_index": 0, "not_periodic_report": 0, "unit": 0}
    assert len(cf["facts"]) == 97
    assert all(T(f["public_at"]).date().isoformat() >= f["end"] for f in cf["facts"])


def test_real_sec_trailing_values_follow_the_registered_methods():
    idx, cf = _real()
    k23 = edgar.facts_as_of(cf, T("2023-11-17T17:31:27-05:00"))                   # fiscal 2023 10-K
    assert (k23["revenue_ttm"]["method"], k23["revenue_ttm"]["value"]) == ("annual", D("35975600000"))
    assert (k23["net_income_ttm"]["value"], k23["eps_diluted_ttm"]["value"]) == (D("4124500000"), D("3.58"))
    q1 = edgar.ttm(cf["facts"], edgar.REVENUE, "USD", T("2024-01-30T16:13:36-05:00"))  # first quarter of fiscal 2024
    fy, ytd, prior = q1["components"]
    assert q1["method"] == "fiscal_year_plus_ytd_minus_prior_ytd" and q1["period_end"] == "2023-12-31"
    assert (fy["end"], ytd["start"], prior["end"]) == ("2023-10-01", "2023-10-02", "2023-01-01")   # 52/53-week year
    assert q1["value"] == D(fy["value"]) + D(ytd["value"]) - D(prior["value"]) == D("36687000000")
    just_before = edgar.ttm(cf["facts"], edgar.REVENUE, "USD", T("2024-11-20T16:08:05-05:00"))
    assert just_before["period_end"] == "2024-06-30"                              # the fiscal 2024 10-K: 1 s later
    assert edgar.ttm(cf["facts"], edgar.REVENUE, "USD", T("2024-11-20T16:08:06-05:00"))["value"] == D("36176200000")
    # the 10-K's company-facts share count (1,137,100,000) is only a cross-check: without that filing's cover page
    # (its inline XBRL contexts, not in the excerpt) the class structure is not established
    sh = edgar.shares_outstanding(None, 829224, "0000829224-25-000114", cf["facts"])
    assert sh["status"] == "unresolved" and "value" not in sh
    assert k23["revenue_ttm"]["status"] == k23["net_income_ttm"]["status"] == "resolved"
