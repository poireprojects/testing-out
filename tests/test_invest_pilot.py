"""Investing pilot (autotrader/invest): accounting, rules, timing, idempotency and labelling. Offline only:
synthetic data, fixture decisions, no model call, no network, no credential."""
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from autotrader.evidence import Evidence, LookAheadError
from autotrader.invest import synthetic
from autotrader.invest.accounting import Book
from autotrader.invest.contract import json_schema, validate
from autotrader.invest.data import write_data_dir
from autotrader.invest.engine import FixtureDecider, ModelDecider, NotApproved, run
from autotrader.invest.ledger import InvestLedger, LedgerConflict, LedgerError
from autotrader.invest.pack import MonthPack, build_pack
from autotrader.invest.registration import FROZEN, config_sha256, et, schedule
from autotrader.invest.report import build_report, markdown, verdict, write_report
from autotrader.invest.universe import UniverseError, select_universe

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "research" / "runs" / "2026-11-11-invest-pilot" / "dry-run"
END = synthetic.end_of_pilot()
SLOTS = schedule(synthetic.FIRST_FILL_MONTH)
ALL_VOO = {i: {"kind": "decision", "label": "100% VOO", "positions": []} for i in range(1, 13)}


def dry(state, data, script=synthetic.FIXTURE_SCRIPT, now=END):
    dec = FixtureDecider(script, synthetic.FIXTURE_COST_USD)
    res = run(state, data, dec, mode="dry_run", first_fill_month=synthetic.FIRST_FILL_MONTH, now=now)
    return res, dec


def records(state):
    return [json.loads(x) for x in (Path(state) / "ledger.jsonl").read_text().splitlines()]


def by_key(state):
    return {r["key"]: r for r in records(state)}


def marks(state, book):
    return {r["payload"]["session"]: r["payload"]["wealth"] for r in records(state)
            if r["type"] == "mark" and r["payload"]["book"] == book}


@pytest.fixture(scope="module")
def main_run(tmp_path_factory):
    base = tmp_path_factory.mktemp("invest")
    synthetic.build(base / "data")
    res, dec = dry(base / "state", base / "data")
    return base, res, dec


def flat_data(d: Path, stock_open="50.00"):
    """Constant prices (VOO 100.00, STK1 50.00), no dividends: every amount below is checked by hand."""
    days = synthetic.sessions()
    prices = {"VOO": {x.isoformat(): {"open": "100.00", "close": "100.00"} for x in days},
              "STK1": {x.isoformat(): {"open": stock_open, "close": "50.00"} for x in days}}
    members = [{"rank": 1, "symbol": "STK1", "name": "Flat Test Co", "cik": "1"}]
    filings = [{"symbol": "STK1", "cik": "1", "accession": "0000000001-30-000001", "form": "10-Q",
                "period_end": "2030-09-30", "accepted_at": et(date(2030, 11, 5), 16, 5).isoformat(),
                "facts": {"shares_outstanding": "1000000000", "revenue_ttm_musd": "4000",
                          "net_income_ttm_musd": "400", "eps_diluted_ttm": "0.40"},
                "passages": [{"section": "MD&A", "text": "Revenue for the quarter was $1,000 million, an increase of "
                                                         "5% from a year earlier. Margins were stable."}]}]
    write_data_dir(d, synthetic=True, sources={"all": "test"}, prices=prices,
                   actions={"splits": [], "dividends": []},
                   universe={"rule": "test", "source": "test", "members": members}, filings=filings)


# ------------------------------------------------------------------ parity
def test_all_voo_ai_portfolio_equals_the_benchmark_to_the_cent_before_model_costs(tmp_path):
    synthetic.build(tmp_path / "data", voo_split=("2031-05-15", 3, 1))
    script = {**ALL_VOO, 4: {"kind": "missing"}, 7: {"kind": "late", "positions": []}}
    dry(tmp_path / "s", tmp_path / "data", script)
    voo, ai = marks(tmp_path / "s", "voo"), marks(tmp_path / "s", "ai")
    assert len(voo) == len(ai) == 251 and voo == ai                     # every session, exact Decimal strings
    k = by_key(tmp_path / "s")
    for book in ("voo", "ai"):                                           # dividends and the split went through both
        acts = [a for r in records(tmp_path / "s") if r["type"] == "session_open" and r["payload"]["book"] == book
                for a in r["payload"]["actions"]]
        assert {a["kind"] for a in acts} == {"split", "dividend_entitlement", "dividend_paid"}
    assert k["session|voo|2031-05-15|open"]["payload"]["after"]["qty"] == k["session|ai|2031-05-15|open"]["payload"]["after"]["qty"]
    rep = build_report(tmp_path / "s")
    assert D(rep["excess_usd_before"]) == 0
    assert D(rep["ai_end_all_in"]) < D(rep["voo_end"])                   # after model costs it can only be behind
    assert D(rep["voo_end"]) - D(rep["ai_end_all_in"]) == D(rep["model_costs"]["grown_usd"])
    assert D(rep["model_costs"]["nominal_usd"]) == D("2.40")             # charged every month, even with no answer


# ------------------------------------------------------------------ buy / hold / sell
def test_buy_hold_sell_sequence_with_hand_checked_cents(tmp_path):
    flat_data(tmp_path / "data")
    script = {**ALL_VOO, 1: {"kind": "decision", "positions": [("STK1", "buy", 10)]},
              2: {"kind": "decision", "positions": [("STK1", "hold", 0)]},
              3: {"kind": "decision", "positions": [("STK1", "sell", 0)]}}
    dry(tmp_path / "s", tmp_path / "data", script)
    k = by_key(tmp_path / "s")
    launch = k["session|ai|2031-01-02|open"]["payload"]
    o = {x["symbol"]: x for x in launch["trade"]["orders"]}
    # $10.00 of STK1 at 50 x 1.001 = 50.05 -> 0.199800199 shares; VOO gets the rest less the $0.01 CAT fee
    assert (o["STK1"]["amount"], o["STK1"]["fill"], o["STK1"]["qty"]) == ("10.00", "50.05000", "0.199800199")
    assert (o["VOO"]["amount"], o["VOO"]["qty"]) == ("89.99", "0.899450274")
    assert launch["trade"]["fees"]["total"] == "0.01" and launch["after"]["cash"] == "0.00"
    assert k["decision|2"]["payload"]["status"] == "valid"
    assert k["session|ai|2031-02-03|open"]["payload"]["trade"] is None    # hold: no trade at all
    sale = k["session|ai|2031-03-03|open"]["payload"]
    o = {x["symbol"]: x for x in sale["trade"]["orders"]}
    # 0.199800199 x 49.95 = 9.98 (truncated); SEC, TAF and CAT each round up to $0.01; $9.95 back into VOO
    assert (o["STK1"]["side"], o["STK1"]["amount"]) == ("sell", "9.98")
    assert sale["trade"]["fees"] | {"rates": None} == {"sec": "0.01", "taf": "0.01", "cat": "0.01", "total": "0.03",
                                                        "rates": None}
    assert (o["VOO"]["amount"], o["VOO"]["qty"]) == ("9.95", "0.099450274")
    assert sale["after"] == {"cash": "0.00", "qty": {"VOO": "0.998900548"}, "receivables": []}
    rep = build_report(tmp_path / "s")
    assert D(rep["ai_end_before_model"]) == D("99.8900548")              # 0.998900548 x 100
    assert D(rep["voo_end"]) == D("99.9400299")                          # 99.99 / 100.05 -> 0.999400299 shares
    assert rep["books"]["ai"]["fees"] == "0.04" and rep["books"]["voo"]["fees"] == "0.01"


# ------------------------------------------------------------------ limits and evidence
@pytest.fixture
def pack(tmp_path):
    flat_data(tmp_path / "data")
    from autotrader.invest.data import MarketData
    data = MarketData(tmp_path / "data")
    slot = SLOTS[1]
    book = Book("ai", D("0.00"), {"VOO": D("0.91"), "STK1": D("0.18")})    # STK1 9.00 of 100.00 = 9.00%
    return build_pack(slot, book, data, slot.cutoff.close + timedelta(minutes=5), [])


def good(pack, **over):
    p = {"symbol": "STK1", "action": "hold", "target_weight_pct": 0,
         "why_outperform_voo": "", "main_downside": "", "invalidation": "", "weight_rationale": "", "what_changed": "",
         "thesis_still_holds": "Revenue growth continues as the filing describes; nothing has changed.",
         "evidence": [{"id": "STK1/0000000001-30-000001/P1", "quote": "Revenue for the quarter was $1,000 million",
                       "figures": ["$1,000 million"]}]}
    p.update(over)
    return {"month": pack.month, "summary": "test", "voo_weight_pct": 91.0, "positions": [p]}


def test_a_well_formed_hold_passes(pack):
    targets, problems, voo = validate(good(pack), pack)
    assert problems == [] and targets == {"STK1": {"action": "hold", "target_pct": None}} and voo == D("91.00")


def _increase(pack, w, **over):
    fields = dict(action="increase", target_weight_pct=w, thesis_still_holds="",
                  why_outperform_voo="Growth at 5% with a P/E of 125.00 is priced for less than it delivers.",
                  main_downside="A slowdown in the largest customer would cut revenue.",
                  invalidation="Revenue growth below 2% in the next quarterly report.",
                  weight_rationale="A small position: the valuation is demanding.",
                  evidence=[{"id": "STK1/0000000001-30-000001/P1", "quote": "an increase of 5% from a year earlier",
                             "figures": ["5%"]}, {"id": "V.STK1", "quote": "P/E 125.00", "figures": ["125.00"]}])
    d = good(pack, **(fields | over))
    d["voo_weight_pct"] = 100 - w
    return d


@pytest.mark.parametrize("mutate, expect", [
    (lambda pk, d: d["positions"].extend(
        [dict(d["positions"][0], symbol=s, action="buy", target_weight_pct=5) for s in ("A1", "A2", "A3")]),
     "not an eligible candidate"),
    (lambda pk, d: d.update(_increase(pk, 10.01, )), "outside (0, 10]"),
    (lambda pk, d: d.update(_increase(pk, 8.5)), "is not above the current"),
    (lambda pk, d: d.update(_increase(pk, -5)), "outside (0, 10]"),
    (lambda pk, d: d["positions"][0].update(action="reduce", target_weight_pct=10.0, what_changed="x" * 30),
     "is not below the current"),
    (lambda pk, d: d["positions"][0].update(action="sell", target_weight_pct=3, what_changed="x" * 30), "a sale has target 0"),
    (lambda pk, d: d.update(positions=[]), "held STK1 is not addressed"),
    (lambda pk, d: d["positions"][0].update(action="buy"), "already held"),
    (lambda pk, d: d["positions"].append(dict(d["positions"][0], symbol="VOO")), "VOO is the remainder"),
    (lambda pk, d: d.update(voo_weight_pct=85.0), "does not match the implied"),
    (lambda pk, d: d["positions"][0].update(thesis_still_holds="ok"), "thesis_still_holds is required"),
    (lambda pk, d: d["positions"][0]["evidence"][0].update(quote="Revenue for the quarter was $9,999 million"),
     "quote not found verbatim"),
    (lambda pk, d: d["positions"][0]["evidence"][0].update(figures=["$2,000 million"]), "states numbers not in its quote"),
    (lambda pk, d: d["positions"][0]["evidence"][0].update(id="STK1/0000000001-31-999999/P1"), "unknown item"),
    (lambda pk, d: d["positions"][0].update(evidence=[]), "cites none of STK1's filing passages"),
    (lambda pk, d: d.update(_increase(pk, 10, evidence=[_increase(pk, 10)["positions"][0]["evidence"][0]])),
     "does not cite its valuation row"),
    (lambda pk, d: d.update(_increase(pk, 10, why_outperform_voo="")), "why_outperform_voo is required"),
    (lambda pk, d: d.update(month="2031-03"), "is not 2031-02"),
])
def test_limits_and_evidence_rules_reject_the_whole_decision(pack, mutate, expect):
    d = good(pack)
    mutate(pack, d)
    targets, problems, _ = validate(d, pack)
    assert targets is None and any(expect in p for p in problems), problems


def test_increase_to_the_cap_with_evidence_passes(pack):
    targets, problems, voo = validate(_increase(pack, 10), pack)
    assert problems == [] and targets["STK1"] == {"action": "increase", "target_pct": "10"} and voo == 90


def test_drifted_holding_above_the_cap_cannot_be_held_and_four_stocks_are_refused(tmp_path):
    flat_data(tmp_path / "data")
    from autotrader.invest.data import MarketData
    data = MarketData(tmp_path / "data")
    book = Book("ai", D("0"), {"VOO": D("0.88"), "STK1": D("0.24")})       # STK1 12 of 100 = 12%
    pk = build_pack(SLOTS[1], book, data, SLOTS[1].cutoff.close, [])
    _, problems, _ = validate(good(pk, ) | {"voo_weight_pct": 88}, pk)
    assert any("above the 10% cap" in p for p in problems)


def test_rejected_decision_changes_nothing_in_the_dry_run(main_run):
    base, _, _ = main_run
    k = by_key(base / "state")
    assert k["decision|5"]["payload"]["status"] == "invalid"
    assert any("4 stocks" in p for p in k["decision|5"]["payload"]["problems"])
    fill = k["session|ai|2031-05-01|open"]["payload"]
    assert fill["trade"] is None and fill["decision"] == {"slot": 5, "status": "invalid"}
    before = {s: v["qty"] for s, v in k["session|ai|2031-04-30|close"]["payload"]["positions"].items()}
    assert fill["after"]["qty"] == before


# ------------------------------------------------------------------ late and missing
def test_missing_and_late_decisions_keep_the_holdings(main_run):
    base, _, _ = main_run
    k = by_key(base / "state")
    assert k["decision|4"]["payload"]["status"] == "missed" and k["decision|6"]["payload"]["status"] == "late"
    assert k["cost|4"]["payload"]["usd"] == "0.20"                       # research is charged even with no answer
    for day, prev in (("2031-04-01", "2031-03-31"), ("2031-06-02", "2031-05-30")):
        o = k[f"session|ai|{day}|open"]["payload"]
        before = {s: v["qty"] for s, v in k[f"session|ai|{prev}|close"]["payload"]["positions"].items()}
        assert not (o["trade"] and any(x["reason"] != "sweep" for x in o["trade"]["orders"]))
        assert {s: q for s, q in o["after"]["qty"].items() if s != "VOO"} == {s: q for s, q in before.items() if s != "VOO"}


def test_a_decision_is_recorded_only_once_it_exists_and_never_backdated(tmp_path):
    synthetic.build(tmp_path / "data")
    cutoff = SLOTS[0].cutoff.close                       # the fixture freezes at cutoff + 60 minutes
    dry(tmp_path / "s", tmp_path / "data", now=cutoff + timedelta(minutes=30))
    assert "decision|1" not in by_key(tmp_path / "s")    # not frozen yet: nothing recorded, nothing invented
    dry(tmp_path / "s", tmp_path / "data", now=cutoff + timedelta(hours=3))
    p = by_key(tmp_path / "s")["decision|1"]
    assert p["payload"]["status"] == "valid" and p["recorded_at"] == (cutoff + timedelta(minutes=60)).astimezone(timezone.utc).isoformat()
    assert p["payload"]["built_at"] < p["payload"]["frozen_at"] < p["payload"]["deadline"]


def test_a_filing_accepted_after_the_cutoff_waits_for_the_next_pack(main_run):
    base, _, _ = main_run
    apr = json.loads((base / "state" / "packs" / "slot-04.json").read_text())   # cutoff 2031-03-31 16:00 ET
    may = json.loads((base / "state" / "packs" / "slot-05.json").read_text())
    late = "SYNTH02/9900000002-31-900001/P1"                                    # accepted 16:30 ET that day
    assert late not in {i["id"] for i in apr["items"]} and late in {i["id"] for i in may["items"]}
    with pytest.raises(LookAheadError):
        MonthPack(4, "2031-04", SLOTS[3].cutoff.close, SLOTS[3].deadline, SLOTS[3].cutoff.close, True, "0", "0", "1",
                  [], [], [], [Evidence("X", "filing", "t", SLOTS[3].cutoff.close + timedelta(minutes=1), "late text")])


def test_missing_fill_price_is_never_invented_and_resolves_when_the_data_arrives(tmp_path):
    synthetic.build(tmp_path / "gappy", drop_prices=[("SYNTH01", "2031-01-02", "open")])
    synthetic.build(tmp_path / "fixed")
    early = et(date(2031, 1, 2), 12)
    dry(tmp_path / "s", tmp_path / "gappy", now=early)
    k = by_key(tmp_path / "s")
    assert k["gap|ai|2031-01-02|open"]["payload"]["missing"] == ["SYNTH01"]
    assert "session|ai|2031-01-02|open" not in k and "session|voo|2031-01-02|open" in k
    rep = build_report(tmp_path / "s")
    assert not rep["complete"] and any("unresolved data gap" in x for x in rep["incomplete_because"])
    first = (tmp_path / "s" / "ledger.jsonl").read_bytes()
    dry(tmp_path / "s", tmp_path / "gappy", now=early)                   # retry with the gap still there: no change
    assert (tmp_path / "s" / "ledger.jsonl").read_bytes() == first
    dry(tmp_path / "s", tmp_path / "fixed")                              # the price exists now
    after = (tmp_path / "s" / "ledger.jsonl").read_bytes()
    assert after.startswith(first)                                       # history kept, only appended
    k = by_key(tmp_path / "s")
    assert "gap_resolved|ai|2031-01-02|open" in k
    fill = {o["symbol"]: o for o in k["session|ai|2031-01-02|open"]["payload"]["trade"]["orders"]}
    assert fill["SYNTH01"]["ref_open"] == synthetic_open("fixed", tmp_path, "SYNTH01", "2031-01-02")
    assert build_report(tmp_path / "s")["complete"]


def synthetic_open(name, tmp_path, sym, day):
    return json.loads((tmp_path / name / "prices.json").read_text())[sym][day]["open"]


def test_missing_month_end_close_makes_the_record_incomplete(tmp_path):
    synthetic.build(tmp_path / "data", drop_prices=[("VOO", "2031-03-10", "close")])
    dry(tmp_path / "s", tmp_path / "data")
    rep = build_report(tmp_path / "s")
    assert not rep["complete"]
    assert rep["books"]["voo"]["marks"] < rep["books"]["voo"]["sessions"]
    assert any("not valued" in x for x in rep["incomplete_because"])
    assert by_key(tmp_path / "s")["gap|voo|2031-03-10|close"]["payload"]["missing"] == ["VOO"]


@pytest.mark.parametrize("mode, fixture, synthetic_data, complete, all_in, voo, expect", [
    ("dry_run", 12, True, True, "120", "100", "NOT A RESULT"),
    ("forward", 0, False, False, "120", "100", "INCONCLUSIVE"),
    ("forward", 0, False, True, "100", "100", "STOP"),
    ("forward", 0, False, True, "99.99", "100", "STOP"),
    ("forward", 0, False, True, "100.01", "100", "ELIGIBLE FOR INDEPENDENT REVIEW"),
])
def test_verdict_rules(mode, fixture, synthetic_data, complete, all_in, voo, expect):
    assert verdict(mode, fixture, synthetic_data, complete, D(all_in), D(voo)).startswith(expect)


# ------------------------------------------------------------------ rerun and tamper
def test_rerunning_a_month_changes_nothing_and_never_asks_again(main_run, tmp_path):
    base, _, _ = main_run
    led = base / "state" / "ledger.jsonl"
    before = led.read_bytes()
    res, dec = dry(base / "state", base / "data", script={i: {"kind": "missing"} for i in range(1, 13)})
    assert res["appended"] == 0 and dec.calls == [] and led.read_bytes() == before


def test_changed_prices_after_a_fill_stop_the_run_instead_of_rewriting_it(tmp_path):
    script = {**ALL_VOO, 1: {"kind": "decision", "positions": [("STK1", "buy", 10)]}}
    flat_data(tmp_path / "a")
    dry(tmp_path / "s", tmp_path / "a", script)
    flat_data(tmp_path / "b", stock_open="50.01")
    with pytest.raises(LedgerConflict, match="session\\|ai\\|2031-01-02\\|open"):
        dry(tmp_path / "s", tmp_path / "b", script)


def test_a_frozen_decision_is_never_replaced_and_tampering_is_detected(main_run, tmp_path):
    base, _, _ = main_run
    led = InvestLedger(base / "state" / "ledger.jsonl", "dry_run", by_key(base / "state")["open"]["payload"]["identity"],
                       END)
    with pytest.raises(LedgerConflict):
        led.put("decision|1", "decision", {"status": "valid", "decision": {"positions": []}}, END)
    copy = tmp_path / "ledger.jsonl"
    lines = (base / "state" / "ledger.jsonl").read_text().splitlines()
    lines[40] = lines[40].replace('"0.', '"9.', 1)
    copy.write_text("\n".join(lines) + "\n")
    ident = json.loads(lines[0])["payload"]["identity"]
    with pytest.raises(LedgerError):
        InvestLedger(copy, "dry_run", ident, END)


# ------------------------------------------------------------------ fixture vs genuine
def test_reports_label_fixture_decisions_and_never_call_them_a_result(main_run):
    base, _, _ = main_run
    rep = write_report(base / "state")
    md = (base / "state" / "REPORT.md").read_text()
    assert rep["decisions_fixture"] == 12 and rep["decisions_genuine"] == 0
    assert rep["verdict"].startswith("NOT A RESULT") and "FIXTURE DECISIONS, SYNTHETIC DATA" in md
    assert md.count("(FIXTURE)") == 12 and rep["complete"] and rep["pack_files_verified"] == 12
    statuses = [d["status"] for d in rep["decisions"]]
    assert statuses == ["valid"] * 3 + ["missed", "invalid", "late"] + ["valid"] * 6


def test_fixture_and_genuine_decisions_never_share_a_ledger(main_run, tmp_path):
    base, _, _ = main_run
    with pytest.raises(LedgerError, match="fixture decisions only"):
        run(tmp_path / "x", base / "data", ModelDecider(["SYNTH01"]), mode="dry_run", first_fill_month="2031-01", now=END)
    with pytest.raises(LedgerError, match="genuine decisions and real data"):
        run(tmp_path / "x", base / "data", FixtureDecider({}, "0"), mode="forward", first_fill_month="2031-01", now=END)
    flat_real = tmp_path / "real"
    flat_data(flat_real)
    man = json.loads((flat_real / "MANIFEST.json").read_text())
    (flat_real / "MANIFEST.json").write_text(json.dumps(man | {"synthetic": False}))
    with pytest.raises(NotApproved):
        run(tmp_path / "y", flat_real, ModelDecider(["STK1"]), mode="forward", first_fill_month="2031-01", now=END)
    ident = by_key(base / "state")["open"]["payload"]["identity"]
    with pytest.raises(LedgerError, match="not forward"):
        InvestLedger(base / "state" / "ledger.jsonl", "forward", ident, END)


def test_the_genuine_request_is_rendered_offline_and_never_sent(main_run):
    base, _, _ = main_run
    rec = json.loads((base / "state" / "packs" / "slot-07.json").read_text())
    items = [Evidence(i["id"], i["kind"], i["source"], datetime.fromisoformat(i["available_at"]), i["text"]) for i in rec["items"]]
    pk = MonthPack(rec["slot"], rec["month"], datetime.fromisoformat(rec["cutoff"]), datetime.fromisoformat(rec["deadline"]),
                   datetime.fromisoformat(rec["built_at"]), rec["synthetic"], rec["cash"], rec["investable"],
                   rec["voo_close"], rec["holdings"], rec["candidates"], rec["prior_theses"], items)
    assert pk.sha256 == by_key(base / "state")["decision|7"]["payload"]["pack_sha256"]
    m = ModelDecider([c["symbol"] for c in rec["candidates"]])
    req = m.request(pk)
    assert req["model"] == "claude-opus-5-5" == FROZEN["model"]["id"]
    assert req["max_tokens"] == 16000 and req["output_config"]["effort"] == "medium"
    assert req["output_config"]["format"]["schema"] == json_schema([c["symbol"] for c in rec["candidates"]])
    assert not {"tools", "fallbacks", "thinking", "speed", "betas"} & set(req)
    assert "SYNTHETIC DATA" in req["messages"][0]["content"]
    with pytest.raises(NotApproved):
        m.decide(SLOTS[6], pk)


# ------------------------------------------------------------------ reproducibility
def test_the_dry_run_reproduces_byte_for_byte_and_matches_the_committed_run(main_run, tmp_path):
    base, _, _ = main_run
    synthetic.build(tmp_path / "data")
    dry(tmp_path / "s", tmp_path / "data")
    a = (base / "state" / "ledger.jsonl").read_bytes()
    assert (tmp_path / "s" / "ledger.jsonl").read_bytes() == a
    assert (COMMITTED / "ledger.jsonl").read_bytes() == a
    assert json.loads((COMMITTED / "report.json").read_text()) == build_report(base / "state")
    assert json.loads((COMMITTED / "summary.json").read_text())["config_sha256"] == config_sha256()


def test_the_report_rebuilds_offline_from_the_ledger_alone(tmp_path):
    synthetic.build(tmp_path / "data")
    dry(tmp_path / "s", tmp_path / "data")
    first = build_report(tmp_path / "s")
    import shutil
    shutil.rmtree(tmp_path / "data")                                     # no data, no model, no network
    assert build_report(tmp_path / "s") == first and markdown(first)


# ------------------------------------------------------------------ universe rule
def test_universe_rule_one_class_per_issuer_common_only_ranked_and_mapped():
    rows, cmap = synthetic.nport_rows()
    u = select_universe(rows, cmap, size=12)
    assert [m["symbol"] for m in u] == [f"SYNTH{i:02d}" for i in range(1, 13)]
    assert "99SYNPF00" not in {m["cusip"] for m in u} and "99SYN01C0" not in {m["cusip"] for m in u}
    tie = [dict(rows[0], cusip="99SYN99Z0", lei="TIE"), dict(rows[0], cusip="99SYN99A0", lei="TIE2")]
    u2 = select_universe(tie, {"99SYN99Z0": {"symbol": "Z", "cik": "1"}, "99SYN99A0": {"symbol": "A", "cik": "2"}}, size=1)
    assert u2[0]["symbol"] == "A"                                        # equal value: lower CUSIP first
    with pytest.raises(UniverseError):
        select_universe(rows, {}, size=1)
