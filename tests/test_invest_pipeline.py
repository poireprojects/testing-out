"""The offline pipeline (raw store -> verified data directory) and the commands around it: the end-to-end fixture
demonstration, deterministic replay against the committed run, missing and altered inputs, after-cutoff
restatements, trial packs, the maximum-size budget rendering and the real-source check. No network, no model."""
import hashlib
import json
import shutil
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from autotrader.invest import pipeline, synthetic_raw
from autotrader.invest.__main__ import main
from autotrader.invest.adapters.nport import NportError
from autotrader.invest.adapters.raw import RawError, sha256
from autotrader.invest.budget import max_size_budget, max_size_pack
from autotrader.invest.data import MarketData
from autotrader.invest.registration import FROZEN, canonical_json, config_sha256
from tests.invest_helpers import COMMITTED, SLOTS, no_network  # noqa: F401

COMMITTED_FILES = ("ledger.jsonl", "report.json", "REPORT.md", "summary.json", "request-slot-01.json",
                   "raw-MANIFEST.json", "data-MANIFEST.json")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    base = tmp_path_factory.mktemp("pipe")
    synthetic_raw.build_raw(base / "raw")
    r = pipeline.build_data(base / "raw", base / "data", freeze=synthetic_raw.FREEZE, series_id=synthetic_raw.SERIES_ID,
                            series_name=synthetic_raw.SERIES)
    return base, r


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo") / "run"
    assert main(["dryrun", str(out)]) == 0
    return out


def copy_raw(src: Path, dst: Path, drop=lambda rel: False, edit=None) -> Path:
    """A copy of a raw store without some files, or with some files re-saved (new bytes AND a matching manifest
    entry: a different source response, not a tampered one)."""
    shutil.copytree(src, dst)
    man = json.loads((dst / "MANIFEST.json").read_text())
    for rel in [r for r in man["files"] if drop(r)]:
        del man["files"][rel]
    for rel, fn in (edit or {}).items():
        b = fn((dst / rel).read_bytes())
        (dst / rel).write_bytes(b)
        man["files"][rel]["sha256"] = sha256(b)
    (dst / "MANIFEST.json").write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
    return dst


def build(raw: Path, d: Path):
    return pipeline.build_data(raw, d, freeze=synthetic_raw.FREEZE, series_id=synthetic_raw.SERIES_ID,
                               series_name=synthetic_raw.SERIES)


def entry(raw: Path, cik: int, form: str, report_date: str) -> str:
    sub = json.loads((raw / f"edgar/submissions/CIK{cik:010d}.json").read_text())["filings"]["recent"]
    return next(a for a, f, r in zip(sub["accessionNumber"], sub["form"], sub["reportDate"])
                if f == form and r == report_date)


# ------------------------------------------------------------------ the pipeline
def test_raw_fixtures_become_a_verified_data_directory_with_provenance(built):
    base, r = built
    assert (r["members"], r["problems"]) == (12, {})
    assert r["nport"]["accepted_at"].startswith("2030-11-27") and r["nport"]["report_date"] == "2030-09-30"
    assert r["unsupported_actions"] == [{"symbol": "SYNTH12", "kind": "cash_mergers", "date": "2032-06-01",
                                         "detail": {"id": "merger-SYNTH12", "acquiree_symbol": "SYNTH12",
                                                    "effective_date": "2032-06-01", "rate": "60.00"}}]
    data = MarketData(base / "data")                         # verifies the data manifest
    assert data.synthetic and [m["symbol"] for m in data.members()] == [f"SYNTH{i:02d}" for i in range(1, 13)]
    assert data.universe["review"]["fixture"] and "not a person's review" in data.universe["review"]["reviewer"]
    man = json.loads((base / "raw" / "MANIFEST.json").read_text())["files"]
    prov = json.loads((base / "data" / "provenance.json").read_text())
    assert prov["raw_manifest_sha256"] == hashlib.sha256((base / "raw" / "MANIFEST.json").read_bytes()).hexdigest()
    assert prov["files"] and all(f["raw_sha256"] == man[f["raw_file"]]["sha256"] and f["source_url"] and
                                 f["retrieved_at"] for f in prov["files"])
    rows = json.loads((base / "data" / "review_table.json").read_text())["rows"]
    assert len(rows) == 12 and all(x["status"] == "needs_review" for x in rows)
    filings = json.loads((base / "data" / "filings.json").read_text())
    assert {f["form"] for f in filings} == {"10-K", "10-Q", "8-K"}
    # the fixture's facts start in late 2028: the June and September fiscal-year companies' first 2029 reports lack
    # the prior-year periods a trailing value needs, so they are labelled unavailable (not invented); none is used
    early = [(f["symbol"], f["accepted_at"][:10]) for f in filings if "facts_unavailable" in f]
    assert early == [("SYNTH10", "2029-02-07"), ("SYNTH10", "2029-05-08"), ("SYNTH10", "2029-08-07"),
                     ("SYNTH12", "2029-02-07"), ("SYNTH12", "2029-05-08")]
    for f in filings:
        assert f["provenance"]["header"]["raw_sha256"] and f["provenance"]["document"]["source_url"]
        assert f["passages"] and all(p["text"] for p in f["passages"])
        if f["form"] == "8-K":
            assert f["items"].startswith("2.02") and f["provenance"]["document"]["raw_file"].endswith("-ex991.htm")
        elif "facts_unavailable" not in f:
            fp = f["facts_provenance"]["revenue_ttm"]
            assert fp["method"] in ("annual", "fiscal_year_plus_ytd_minus_prior_ytd") and fp["components"]
            assert all(datetime.fromisoformat(c["public_at"]) <= datetime.fromisoformat(f["accepted_at"])
                       for c in fp["components"])
    # SYNTH01 reports two classes on the class axis (company facts have no share row for it): resolved from the
    # filing's own cover page, summed; its market value is not computed (only one class's price is collected)
    s1 = next(f for f in filings if f["symbol"] == "SYNTH01" and f["form"] == "10-Q")
    sp = s1["facts_provenance"]["shares_outstanding"]
    assert (sp["status"], sp["share_classes"], sp["company_facts_rows"]) == ("resolved", 2, 0)
    assert s1["facts"]["share_classes"] == "2" and int(s1["facts"]["shares_outstanding"]) == sum(
        int(c["value"]) for c in sp["classes"])
    assert "approximation" in s1["facts_provenance"]["eps_diluted_ttm"]["approximation"]
    s2 = next(f for f in filings if f["symbol"] == "SYNTH02" and f["form"] == "10-Q")
    assert s2["facts_provenance"]["shares_outstanding"]["company_facts_rows"] == 1
    assert s2["facts"]["share_classes"] == "1"
    assert r["unresolved_facts"] == {}
    # the corporate-action response: two pages of one untyped query, retrieved after its covered dates
    cov = json.loads((base / "data" / "actions.json").read_text())["coverage"]
    assert r["actions_coverage"]["status"] == "collected" and r["actions_coverage"]["uncovered"] == []
    (chain,) = cov["chains"]
    assert chain["status"] == "complete" and len(chain["pages"]) == 2 and chain["covered_until"] == "2032-01-04"
    assert all(v["intervals"] == [["2030-11-01", "2032-01-04"]] for v in cov["symbols"].values())
    # the N-PORT reconciliation is written out: the newer report of another series is excluded by its ID
    rec = json.loads((base / "data" / "review_table.json").read_text())["reconciliation"]
    assert [(x["status"], x["series_id"]) for x in rec] == [("selected", "S000099001"), ("excluded", "S000099002"),
                                                             ("excluded", "S000099001")]


def test_the_pipeline_is_deterministic(built, tmp_path):
    base, r = built
    assert build(base / "raw", tmp_path / "again")["data_manifest_sha256"] == r["data_manifest_sha256"]
    for name in ("filings.json", "prices.json", "universe.json", "index.json", "provenance.json"):
        assert (tmp_path / "again" / name).read_bytes() == (base / "data" / name).read_bytes()


def test_a_restatement_after_a_cutoff_enters_only_later_filings(built):
    base, _ = built
    raw = base / "raw"
    k_acc = entry(raw, 9900000003, "10-K", "2030-12-31")
    ka_acc = entry(raw, 9900000003, "10-K/A", "2030-12-31")
    q_acc = entry(raw, 9900000003, "10-Q", "2031-03-31")
    filings = {f["accession"]: f for f in json.loads((base / "data" / "filings.json").read_text())}
    assert ka_acc not in filings                                  # an amendment is not itself a pack filing
    k, q = filings[k_acc], filings[q_acc]
    assert k["facts_provenance"]["revenue_ttm"]["components"][0]["accession"] == k_acc
    assert k["facts_provenance"]["revenue_ttm"]["restatements_seen"] == []
    fy, _ytd, _prior = q["facts_provenance"]["revenue_ttm"]["components"]
    assert fy["accession"] == ka_acc and q["facts_provenance"]["revenue_ttm"]["restatements_seen"][0]["by"] == ka_acc
    assert D(fy["value"]) == (D(k["facts_provenance"]["revenue_ttm"]["value"]) * D("1.05")).to_integral_value()
    data = MarketData(base / "data")
    before = data.filings_by("SYNTH03", SLOTS[3].cutoff.close)[0]       # April pack: cutoff 2031-03-31
    after = data.filings_by("SYNTH03", SLOTS[5].cutoff.close)           # June pack: cutoff 2031-05-29
    assert before["accession"] == k_acc and q_acc in {f["accession"] for f in after}


def test_missing_or_unreconciled_source_files_become_collection_failures(built, tmp_path):
    base, _ = built
    acc = entry(base / "raw", 9900000007, "10-Q", "2031-03-31")
    raw = copy_raw(base / "raw", tmp_path / "noheader", drop=lambda rel: rel.endswith(f"{acc}-index-headers.html"))
    r = build(raw, tmp_path / "d1")
    assert r["problems"] == {"SYNTH07": {acc: "its filing header was not collected"}}
    cov = MarketData(tmp_path / "d1").coverage("SYNTH07", SLOTS[5].cutoff.close, SLOTS[5].cutoff.day)
    assert cov["status"] == "collection_failure" and acc in cov["reason"]
    hdr = f"edgar/headers/{acc}-index-headers.html"
    raw2 = copy_raw(base / "raw", tmp_path / "skew", edit={hdr: lambda b: b.replace(b"160500\n", b"160600\n", 1)})
    r2 = build(raw2, tmp_path / "d2")
    assert "differs from the index" in r2["problems"]["SYNTH07"][acc]
    raw3 = copy_raw(base / "raw", tmp_path / "nodoc",
                    drop=lambda rel: rel.startswith(f"edgar/docs/{acc.replace('-', '')}/"))
    assert "was not collected" in build(raw3, tmp_path / "d3")["problems"]["SYNTH07"][acc]
    raw4 = copy_raw(base / "raw", tmp_path / "nofacts", drop=lambda rel: rel.endswith("CIK9900000007.json")
                    and "companyfacts" in rel)
    assert set(build(raw4, tmp_path / "d4")["problems"]["SYNTH07"].values()) == {"company facts were not collected"}


def test_facts_that_cannot_support_a_value_make_the_filing_unavailable_not_a_failure(built, tmp_path):
    base, _ = built
    rel = "edgar/companyfacts/CIK9900000008.json"

    def no_net_income(b):
        j = json.loads(b)
        del j["facts"]["us-gaap"]["NetIncomeLoss"]
        return json.dumps(j).encode()
    raw = copy_raw(base / "raw", tmp_path / "r", edit={rel: no_net_income})
    build(raw, tmp_path / "d")
    f = [x for x in json.loads((tmp_path / "d" / "filings.json").read_text()) if x["symbol"] == "SYNTH08"
         and x["form"] != "8-K"]
    assert f and all("net_income_ttm" in x["facts_unavailable"] for x in f)
    cov = MarketData(tmp_path / "d").coverage("SYNTH08", SLOTS[3].cutoff.close, SLOTS[3].cutoff.day)
    assert cov["status"] == "unavailable" and "net_income_ttm" in cov["reason"]


def test_altered_raw_bytes_and_a_missing_or_unapproved_review_stop_the_build(built, tmp_path):
    base, _ = built
    bad = copy_raw(base / "raw", tmp_path / "tampered")
    doc = next(p for p in sorted((bad / "edgar" / "docs").rglob("*.htm")) if b"Revenue" in p.read_bytes())
    doc.write_bytes(doc.read_bytes().replace(b"Revenue", b"Revenge", 1))     # no manifest update: tampering
    with pytest.raises(RawError, match="altered raw input"):
        build(bad, tmp_path / "d0")
    nor = copy_raw(base / "raw", tmp_path / "noreview", drop=lambda rel: rel.startswith("review/"))
    with pytest.raises(pipeline.PipelineError, match="no mapping review"):
        build(nor, tmp_path / "d1")
    unap = copy_raw(base / "raw", tmp_path / "unapproved", edit={"review/universe_review.json": lambda b: json.dumps(
        dict(json.loads(b), rows=[dict(r, approved=r["symbol"] != "SYNTH05") for r in json.loads(b)["rows"]])).encode()})
    with pytest.raises(NportError, match="lack an approved mapping"):
        build(unap, tmp_path / "d2")
    late = copy_raw(base / "raw", tmp_path / "late")
    with pytest.raises(NportError, match="no Synthetic 500 Index Fund NPORT-P public before"):
        pipeline.build_data(late, tmp_path / "d3", freeze=datetime.fromisoformat("2030-11-01T00:00:00-05:00"),
                            series_id=synthetic_raw.SERIES_ID, series_name=synthetic_raw.SERIES)


# ------------------------------------------------------------------ the end-to-end demonstration
def test_the_demonstration_prepares_requests_records_fixture_decisions_and_scores_outcomes(demo):
    s = json.loads((demo / "summary.json").read_text())
    assert s["verdict"].startswith("NOT A RESULT") and s["model_calls"] == 0 and s["spend_usd"] == "0"
    assert s["decisions"]["fixture"] == 12 and s["decisions"]["genuine"] == 0 and s["complete"]
    assert s["decisions"]["status_counts"] == {"valid": 9, "invalid_answer": 1, "missed_answer": 2, "missing_input": 0}
    assert s["forecasts"] == {"scored": 84, "pending": 24, "missing": 0}
    req = json.loads((demo / "request-slot-01.json").read_text())
    body = canonical_json(req)
    assert hashlib.sha256(body).hexdigest() == s["request_slot_01"]["sha256"] and len(body) == s["request_slot_01"]["bytes"]
    assert req["model"] == FROZEN["model"]["id"] and "SYNTHETIC DATA" in req["messages"][0]["content"]
    pk = json.loads((demo / "packs" / "slot-01.json").read_text())
    assert any("adverse disclosure" in i["source"] for i in pk["items"])          # adverse passages reach the pack
    assert {s_["symbol"] for s_ in pk["candidates"]} == {f"SYNTH{i:02d}" for i in range(1, 13)}
    led = [json.loads(x) for x in (demo / "ledger.jsonl").read_text().splitlines()]
    kinds = {r["type"] for r in led}
    assert {"pack", "request", "answer", "decision", "cost", "forecast_outcome", "mark"} <= kinds
    assert led[0]["payload"]["identity"]["config_sha256"] == config_sha256() == s["config_sha256"]


def test_replay_is_deterministic_resume_appends_nothing_and_matches_the_committed_run(demo, tmp_path):
    again = tmp_path / "again"
    assert main(["dryrun", str(again)]) == 0
    for name in COMMITTED_FILES:
        assert (again / name).read_bytes() == (demo / name).read_bytes(), name
        assert (COMMITTED / name).read_bytes() == (demo / name).read_bytes(), f"committed {name} is stale"
    packs = {p.name: p.read_bytes() for p in (again / "packs").glob("*.json")}
    ledger = (again / "ledger.jsonl").read_bytes()
    assert main(["dryrun", str(again)]) == 2                                      # refuses to overwrite
    assert main(["dryrun", str(again), "--resume"]) == 0
    assert json.loads((again / "summary.json").read_text())["appended_this_run"] == 0
    assert (again / "ledger.jsonl").read_bytes() == ledger                       # frozen records unchanged
    assert {p.name: p.read_bytes() for p in (again / "packs").glob("*.json")} == packs
    committed = json.loads((COMMITTED / "summary.json").read_text())
    assert committed["packs_sha256"] == {k: hashlib.sha256(v).hexdigest() for k, v in sorted(packs.items())}


# ------------------------------------------------------------------ commands
def test_trial_pack_is_an_input_inspection_that_sends_nothing(built, tmp_path, capsys):
    base, _ = built
    assert main(["trial-pack", str(base / "data"), str(tmp_path / "t"), "--first-fill-month", "2031-01", "--slot", "3"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sent"] is False and out["coverage"] == {"complete": 12}
    assert (tmp_path / "t" / "TRIAL-PACK.txt").read_text().startswith("TRIAL EVIDENCE PACK: an inspection of inputs")
    req = json.loads((tmp_path / "t" / "request.json").read_text())
    assert hashlib.sha256(canonical_json(req)).hexdigest() == out["request_sha256"]


def test_the_budget_renders_a_maximum_size_request_and_keeps_paid_dispatch_blocked(tmp_path, capsys):
    pk = max_size_pack()
    lim = FROZEN["evidence"]
    filings = [i for i in pk.items if i.kind == "filing"]
    assert len(filings) == 12 * lim["max_passages_per_candidate"] and all(len(i.text) == lim["max_passage_chars"]
                                                                          for i in filings)
    b = max_size_budget()
    assert b["paid_dispatch"] == "BLOCKED" and "upper bound on billable input tokens" in b["missing_guarantee"]
    assert b["documented_bounds"]["output_per_request_usd"] == "0.3200" and b["max_tokens"] == 16000
    assert b["documented_bounds"]["input_per_request"] == "none documented"
    assert b["request_bytes"] == b["measured_bytes"]["request_total"] > 60_000
    assert b["output_room"]["left_for_thinking_at_that_estimate"] > 0
    assert D(b["estimates_usd"]["twelve_requests"]["high"]) < D("12.50")         # an estimate, not a bound
    assert b["plan"]["retries"] == 0 and b["plan"]["requests_max"] == 12
    assert main(["budget", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["paid_dispatch"] == "BLOCKED"
    assert json.loads((tmp_path / "budget.json").read_text())["request_sha256"] == b["request_sha256"]


def test_the_real_source_check_runs_offline_on_files_named_by_their_url(tmp_path, capsys):
    real = Path(__file__).resolve().parent / "fixtures" / "invest_real"
    prov = json.loads((real / "PROVENANCE.json").read_text())["files"]
    cache = tmp_path / "cache"
    cache.mkdir()
    for name, p in prov.items():
        key = hashlib.sha256(p["source_url"].encode()).hexdigest()[:24]
        (cache / key).write_bytes((real / name).read_bytes())
    (cache / "0123456789abcdef01234567").write_text('{"cik": 1, "facts": {}}')      # URL not provable: skipped
    assert main(["real-check", str(tmp_path / "out.json"), str(cache)]) == 0
    t = json.loads(capsys.readouterr().out)
    assert t["companies"] == 1 and t["asof_points"] == 9 and t["facts_public_before_period_end"] == 0
    r = json.loads((tmp_path / "out.json").read_text())["companies"][0]
    assert r["cik"] == 829224 and r["excluded"] == {"accession_not_in_index": 0, "not_periodic_report": 0, "unit": 0}
    assert set(r["methods_used"]) <= {"annual", "fiscal_year_plus_ytd_minus_prior_ytd"}


def test_forward_refuses_and_request_renders_offline(demo, capsys):
    assert main(["forward"]) == 3
    assert "proposed, not approved" in capsys.readouterr().err
    assert main(["request", str(demo), "1"]) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["model"] == FROZEN["model"]["id"] and "nothing was sent" in cap.err
    assert main(["verify", str(demo)]) == 0 and "hash chain and keys verified" in capsys.readouterr().out
    assert main(["review-packet", str(demo), str(demo / "packet.json")]) == 0
    assert json.loads((demo / "packet.json").read_text())["items"] == []           # genuine claims only: none
    (demo / "packet.json").unlink()


def test_nothing_in_the_pipeline_or_commands_can_reach_the_network(built, tmp_path):
    import socket
    with pytest.raises(Exception, match="networking is disabled"):
        socket.create_connection(("data.sec.gov", 443), timeout=1)
    with pytest.raises(Exception, match="networking is disabled"):
        socket.getaddrinfo("api.anthropic.com", 443)
    base, r = built
    assert build(base / "raw", tmp_path / "d")["data_manifest_sha256"] == r["data_manifest_sha256"]
    assert timedelta(0) == timedelta(0)
