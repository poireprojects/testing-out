"""Decision contract v2 (proposed): limits, assessments of every candidate, typed claims, scenarios, measurable
invalidation, abstention, caps. Offline: a flat synthetic pack, hand-written answers, no model and no network.
Passing these checks says an answer is well formed; nothing here measures or implies investment skill."""
import json
from datetime import timedelta
from decimal import Decimal as D

import pytest

from autotrader.invest import synthetic
from autotrader.invest.accounting import Book
from autotrader.invest.claims import claims, packet, record_review
from autotrader.invest.contract import (MAX_CLAIM_TEXT, MAX_CLAIMS, MAX_NOTE, MAX_TEXT, _eval, consistency_notes,
                                        json_schema, validate)
from autotrader.invest.data import MarketData
from autotrader.invest.engine import FixtureDecider, derive_decision
from autotrader.invest.pack import build_pack, pack_from_record
from tests.invest_helpers import (ALL_VOO, END, P1, P2, SLOTS, answer, assessment, by_key, dry, fact, flat_data,  # noqa: F401
                                  increase, no_network, pe_calc, position, records)


@pytest.fixture
def pack(tmp_path):
    flat_data(tmp_path / "data")
    data = MarketData(tmp_path / "data")
    slot = SLOTS[1]
    book = Book("ai", D("0.00"), {"VOO": D("0.91"), "STK1": D("0.18")})    # STK1 9.00 of 100.00 = 9.00%
    return build_pack(slot, book, data, slot.cutoff.close + timedelta(minutes=5), [])


@pytest.fixture
def empty_pack(tmp_path):
    """Nothing held: buys and abstentions."""
    flat_data(tmp_path / "data0")
    slot = SLOTS[1]
    return build_pack(slot, Book("ai", D("100.00")), MarketData(tmp_path / "data0"),
                      slot.cutoff.close + timedelta(minutes=5), [])


def ok(d, pk):
    targets, problems, voo = validate(d, pk)
    assert problems == [], problems
    return targets, voo


def bad(d, pk, expect):
    targets, problems, _ = validate(d, pk)
    assert targets is None and any(expect in p for p in problems), problems
    return problems


# ------------------------------------------------------------------ well-formed answers
def test_a_well_formed_hold_passes(pack):
    targets, voo = ok(answer(), pack)
    assert targets == {"STK1": {"action": "hold", "target_pct": None}} and voo == D("91.00")


def test_a_stock_selection_with_every_required_part_passes(empty_pack):
    d = answer(positions=[position(action="buy", w=8, claims=[fact(), fact(P2, "impairment charge of $40 million",
                                                                           ["$40 million"], "A $40 million impairment."),
                                                              pe_calc()])], voo=92.0)
    targets, voo = ok(d, empty_pack)
    assert targets == {"STK1": {"action": "buy", "target_pct": "8"}} and voo == D(92)


def test_increase_to_the_cap_with_a_valuation_calculation_passes(pack):
    targets, voo = ok(increase(10), pack)
    assert targets["STK1"] == {"action": "increase", "target_pct": "10"} and voo == 90


def test_a_genuine_form_abstention_with_every_candidate_assessed_is_valid(empty_pack):
    d = answer(positions=[], voo=100.0, assessments=[assessment(rank=1, ex=-1.0, p=0.4)],
               prefer_voo_reason="STK1 trades at 125 times earnings with slowing growth; VOO is the better hold.")
    targets, voo = ok(d, empty_pack)
    assert targets == {} and voo == 100
    base = {"slot": 2, "month": "2031-02", "decider": "model", "genuine": True,
            "cutoff": SLOTS[1].cutoff.close.isoformat(), "deadline": SLOTS[1].deadline.isoformat()}
    rec = derive_decision(base, empty_pack, {"decision": d, "frozen_at": (SLOTS[1].cutoff.close + timedelta(hours=1)).isoformat(),
                                             "lost_in_flight": False, "note": ""})
    assert rec["status"] == "valid" and rec["targets"] == {} and rec["genuine"] and rec["consistency_notes"] == []
    d2 = dict(d, prefer_voo_reason="")
    bad(d2, empty_pack, "prefer_voo_reason is required")


def test_contradictory_evidence_is_cited_and_a_self_contradicting_assessment_is_recorded_not_enforced(empty_pack):
    contra = fact(P2, "demand for it declined 30% during the quarter", ["30%"], "Demand in one line fell 30%.")
    d = answer(positions=[position(action="buy", w=5, claims=[fact(), contra, pe_calc()])], voo=95.0,
               assessments=[assessment(ex=-2.0, p=0.35)])           # buys what its own assessment rates below VOO
    ok(d, empty_pack)
    assert consistency_notes(d) == ["STK1: kept with expected excess -2.0 and probability 0.35"]


# ------------------------------------------------------------------ limits and the whole-decision rule
@pytest.mark.parametrize("mutate, expect", [
    (lambda d: d["positions"].extend([position(s, "buy", 5) for s in ("A1", "A2", "A3")]), "not an eligible candidate"),
    (lambda d: d.update(increase(10.01)), "outside (0, 10]"),
    (lambda d: d.update(increase(8.5)), "is not above the current"),
    (lambda d: d.update(increase(-5)), "outside (0, 10]"),
    (lambda d: d["positions"][0].update(action="reduce", target_weight_pct=10.0, what_changed="x" * 30),
     "is not below the current"),
    (lambda d: d["positions"][0].update(action="sell", target_weight_pct=3, what_changed="x" * 30), "a sale has target 0"),
    (lambda d: d.update(positions=[]), "held STK1 is not addressed"),
    (lambda d: d["positions"][0].update(action="buy"), "already held"),
    (lambda d: d["positions"].append(position("VOO")), "VOO is the remainder"),
    (lambda d: d.update(voo_weight_pct=85.0), "does not match the implied"),
    (lambda d: d.update(month="2031-03"), "is not 2031-02"),
    (lambda d: d["positions"][0].update(thesis_still_holds="ok"), "thesis_still_holds is required"),
    (lambda d: d["positions"][0].update(mispricing=""), "mispricing is required"),
    (lambda d: d["positions"][0].update(contrary_evidence=""), "contrary_evidence is required"),
    (lambda d: d["positions"][0].update(valuation_link="x" * (MAX_TEXT + 1)), "must be text of at most 240"),
    (lambda d: d.update(summary="s" * (MAX_TEXT + 1)), "summary must be text"),
])
def test_limit_and_required_field_breaches_reject_the_whole_decision(pack, mutate, expect):
    d = answer()
    mutate(d)
    bad(d, pack, expect)


# ------------------------------------------------------------------ assessments: units, bounds, coverage
@pytest.mark.parametrize("mutate, expect", [
    (lambda a: a.update(p_positive_excess=55), "a percentage was given; use a fraction"),
    (lambda a: a.update(p_positive_excess=-0.1), "must be a fraction in [0, 1]"),
    (lambda a: a.update(p_positive_excess=1.2), "must be a fraction in [0, 1]"),
    (lambda a: a.update(p_positive_excess="likely"), "must be a fraction"),
    (lambda a: a.update(expected_excess_pct_3m=250), "must be percentage points within [-100, 100]"),
    (lambda a: a.update(expected_excess_pct_3m=None), "must be percentage points"),
    (lambda a: a.update(expected_excess_pct_3m=True), "must be percentage points"),
    (lambda a: a.update(rank=0), "rank 0 must be an integer from 1 to 1"),
    (lambda a: a.update(rank=1.5), "must be an integer"),
    (lambda a: a.update(rank=True), "must be an integer"),
    (lambda a: a.update(rank=2), "must be an integer from 1 to 1"),
    (lambda a: a.update(note="n" * (MAX_NOTE + 1)), "note longer than 100"),
])
def test_invalid_units_and_probabilities_reject_the_decision(pack, mutate, expect):
    d = answer()
    mutate(d["assessments"][0])
    bad(d, pack, expect)


def test_bounds_are_inclusive(pack):
    for ex, p in ((-100, 0), (100, 1), (0, 0.5)):
        ok(answer(assessments=[assessment(ex=ex, p=p)]), pack)


def test_every_eligible_and_held_name_is_assessed_exactly_once(pack):
    bad(answer(assessments=[]), pack, "not assessed: STK1")
    bad(answer(assessments=[assessment(), assessment()]), pack, "assessed more than once: STK1")
    bad(answer(assessments=[assessment(), assessment("ZZZ")]), pack, "neither eligible nor held: ZZZ")
    bad(dict(answer(), assessments="STK1"), pack, "assessments is not a list")


def test_ties_are_allowed_in_ranks(tmp_path):
    two = [{"rank": 1, "symbol": "STK1", "name": "Flat Test Co", "cik": "1"},
           {"rank": 2, "symbol": "STK2", "name": "Flat Test Two", "cik": "2"}]
    f2 = {"symbol": "STK2", "cik": "2", "accession": "0000000002-30-000001", "form": "10-Q", "period_end": "2030-09-30",
          "accepted_at": "2030-11-05T16:05:00-05:00",
          "facts": {"shares_outstanding": "1000000000", "revenue_ttm_musd": "4000", "net_income_ttm_musd": "400",
                    "eps_diluted_ttm": "0.40"},
          "passages": [{"section": "MD&A", "text": "Revenue for the quarter was $900 million, a decrease of 2% from "
                                                   "a year earlier."}]}
    from tests.invest_helpers import FLAT_FILING
    from autotrader.invest.synthetic import index_for, sessions
    px = {x.isoformat(): {"open": "20.00", "close": "20.00"} for x in sessions()}
    flat_data(tmp_path / "d", filings=[FLAT_FILING, f2], members=two, prices_extra={"STK2": px},
              index=index_for([FLAT_FILING, f2], symbols=["STK1", "STK2"]))
    pk = build_pack(SLOTS[1], Book("ai", D("100.00")), MarketData(tmp_path / "d"), SLOTS[1].cutoff.close, [])
    d = answer(positions=[], voo=100.0, prefer_voo_reason="Neither candidate clears the research cost this month.",
               assessments=[assessment("STK1", 1, 0.0, 0.5), assessment("STK2", 1, 0.0, 0.5)])
    ok(d, pk)


# ------------------------------------------------------------------ positions: invalidation and scenarios
@pytest.mark.parametrize("mutate, expect", [
    (lambda p: p["invalidation"].update(unit=""), "invalidation must give"),
    (lambda p: p["invalidation"].update(comparator="about"), "invalidation must give"),
    (lambda p: p["invalidation"].update(threshold="low"), "invalidation must give"),
    (lambda p: p["invalidation"].update(metric=" "), "invalidation must give"),
    (lambda p: p["invalidation"].update(check_by="2031-01-15"), "invalidation must give"),     # before the cutoff
    (lambda p: p["invalidation"].update(check_by="2032-06-30"), "invalidation must give"),     # beyond 400 days
    (lambda p: p["invalidation"].update(check_by="soon"), "invalidation must give"),
    (lambda p: p["scenarios"]["downside"].update(return_pct_3m=5), "downside <= base <= upside"),
    (lambda p: p["scenarios"]["upside"].update(return_pct_3m=-20), "downside <= base <= upside"),
    (lambda p: p["scenarios"]["base"].update(return_pct_3m="2%"), "numeric return_pct_3m"),
    (lambda p: p["scenarios"]["base"].update(return_pct_3m=400), "numeric return_pct_3m"),
    (lambda p: p["scenarios"]["base"].update(reasoning=""), "base scenario reasoning is required"),
    (lambda p: p["scenarios"]["upside"].update(reasoning="r" * 161), "upside scenario reasoning is required"),
])
def test_kept_positions_need_a_measurable_invalidation_and_ordered_scenarios(pack, mutate, expect):
    d = answer()
    mutate(d["positions"][0])
    bad(d, pack, expect)


def test_a_sale_needs_only_what_changed(pack):
    p = position(action="sell", w=0, claims=[])
    p["invalidation"] = {"metric": "", "comparator": "<", "threshold": 0, "unit": "", "check_by": ""}
    d = answer(positions=[p], voo=100.0, prefer_voo_reason="The impairment changes the thesis; VOO until a better one.")
    targets, voo = ok(d, pack)
    assert targets == {"STK1": {"action": "sell", "target_pct": "0"}} and voo == 100
    bad(answer(positions=[dict(p, what_changed="")], voo=100.0, prefer_voo_reason="x" * 30), pack,
        "what_changed is required")


# ------------------------------------------------------------------ typed claims
@pytest.mark.parametrize("claims_, expect", [
    ([fact(quote="Revenue for the quarter was $9,999 million")], "quote not found verbatim"),
    ([fact(figures=["$2,000 million"])], "states numbers not in its quote"),
    ([fact(figures=["about a billion"])], "has no number"),
    ([fact(id_="STK1/0000000001-31-999999/P1")], "unknown item"),
    ([], "no fact cites one of STK1's filing passages"),
    ([dict(fact(), evidence=[])], "a fact needs at least one citation"),
    ([fact(), {"type": "assumption", "text": "Margins hold.", "evidence": fact()["evidence"], "inputs": [],
               "expression": "", "result": 0}], "a assumption is the model's own and carries no citation"),
    ([fact(), {"type": "forecast", "text": "Revenue grows 6%.", "evidence": fact()["evidence"], "inputs": [],
               "expression": "", "result": 0}], "a forecast is the model's own and carries no citation"),
    ([fact(), {"type": "inferred_expectation", "text": "Analyst consensus expects 10% growth.", "evidence": [],
               "inputs": [], "expression": "", "result": 0}], "must not be presented as analysts' or a consensus"),
    ([fact(), {"type": "inferred_expectation", "text": "The price implies growth near 3%.", "evidence": fact()["evidence"],
               "inputs": [], "expression": "", "result": 0}], "may cite only valuation rows"),
    ([fact(text="Analysts expect revenue of $1,000 million.")], "mentions analysts or a consensus that no cited quote"),
    ([fact(), {"type": "opinion", "text": "x", "evidence": [], "inputs": [], "expression": "", "result": 0}],
     "unknown claim type"),
    ([fact(text="t" * (MAX_CLAIM_TEXT + 1))], "text must be 1-160 characters"),
    ([fact()] * (MAX_CLAIMS + 1), "5 claims, at most 4"),
    ([dict(fact(), evidence=fact()["evidence"] * 3)], "at most 2 citations"),
    ([fact(quote="Revenue for the quarter was $1,000 million, an increase of 5% from a year earlier. Margins were "
                 "stable." + " x" * 60)], "a quote is longer than 200"),
])
def test_claim_rules_keep_facts_assumptions_and_inferences_apart(pack, claims_, expect):
    d = answer()
    d["positions"][0]["claims"] = claims_
    bad(d, pack, expect)


@pytest.mark.parametrize("calc, expect", [
    (pe_calc(result=120.0), "differs from the recomputed 125.0000"),
    (pe_calc(price=51.0, result=127.5), "input price=51.0 does not appear in V.STK1"),
    (pe_calc(src=P1, price=50.0, eps=0.4), "does not appear in " + P1),
    (pe_calc(src="V.NOPE"), "cites unknown item 'V.NOPE'"),
    (dict(pe_calc(), expression="__import__('os').system('x')"), "expression cannot be evaluated"),
    (dict(pe_calc(), expression="price ** eps"), "expression cannot be evaluated"),
    (dict(pe_calc(), expression="price / zero"), "expression cannot be evaluated"),
    (dict(pe_calc(), inputs=[]), "a calculation needs 1-4 inputs"),
    (dict(pe_calc(), inputs=[{"name": "Price!", "value": 50, "source_id": "V.STK1"}]), "malformed input"),
])
def test_calculations_are_recomputed_and_their_inputs_sourced(pack, calc, expect):
    bad(increase(10, claims=[fact(), calc]), pack, expect)


def test_assumption_inputs_are_labelled_and_allowed():
    calc = {"type": "calculation", "text": "Price if the multiple falls to 100.", "evidence": [],
            "inputs": [{"name": "eps", "value": 0.4, "source_id": "V.STK1"},
                       {"name": "pe", "value": 100, "source_id": "assumption"}],
            "expression": "eps * pe", "result": 40.0}
    assert _eval(calc["expression"], {"eps": D("0.4"), "pe": D(100)}) == D("40.0")
    assert _eval("-(a + 2) / 4", {"a": D(2)}) == D(-1)


def test_a_buy_without_its_valuation_row_in_a_calculation_is_rejected(empty_pack):
    d = answer(positions=[position(action="buy", w=5, claims=[fact()])], voo=95.0)
    bad(d, empty_pack, "no calculation uses its valuation row V.STK1")


def test_the_schema_requires_every_field_and_enumerates_the_universe():
    s = json_schema(["B", "A"])
    assert s["required"] == ["month", "summary", "voo_weight_pct", "prefer_voo_reason", "assessments", "positions"]
    a = s["properties"]["assessments"]["items"]
    assert a["properties"]["symbol"]["enum"] == ["A", "B"] and a["properties"]["rank"]["type"] == "integer"
    p = s["properties"]["positions"]["items"]
    assert {"mispricing", "valuation_link", "contrary_evidence", "downside", "invalidation", "weight_rationale",
            "scenarios", "claims"} <= set(p["required"])
    c = p["properties"]["claims"]["items"]["properties"]["type"]["enum"]
    assert c == ["fact", "calculation", "assumption", "forecast", "inferred_expectation"]
    assert all(x.get("additionalProperties") is False for x in (s, a, p))


# ------------------------------------------------------------------ fixture variants in a run
def test_fixture_variants_with_bad_units_or_percent_probabilities_are_rejected_and_keep_holdings(tmp_path):
    flat_data(tmp_path / "data")
    script = {**ALL_VOO, 1: {"kind": "decision", "positions": [("STK1", "buy", 8)]},
              2: {"kind": "decision", "variant": "percent_probability", "positions": [("STK1", "hold", 0)]},
              3: {"kind": "decision", "variant": "bad_units", "positions": [("STK1", "hold", 0)]},
              4: {"kind": "decision", "variant": "consensus", "positions": [("STK1", "hold", 0)]}}
    dry(tmp_path / "s", tmp_path / "data", script)
    k = by_key(tmp_path / "s")
    assert k["decision|1"]["payload"]["status"] == "valid"
    for slot, expect in ((2, "a percentage was given"), (3, "percentage points within"),
                         (4, "must not be presented as analysts'")):
        p = k[f"decision|{slot}"]["payload"]
        assert p["status"] == "invalid_answer" and any(expect in x for x in p["problems"]), p["problems"]
    for day in ("2031-02-03", "2031-03-03", "2031-04-01"):
        assert k[f"session|ai|{day}|open"]["payload"]["trade"] is None


def test_an_unsupported_factual_assertion_passes_the_checks_and_stays_visible_to_claim_review(tmp_path):
    synthetic.build(tmp_path / "data")
    dry(tmp_path / "s", tmp_path / "data", {**ALL_VOO, 1: synthetic.FIXTURE_SCRIPT[1], 2: synthetic.FIXTURE_SCRIPT[2]})
    k = by_key(tmp_path / "s")
    assert k["decision|2"]["payload"]["status"] == "valid"           # mechanics pass: quote and number are real
    fell = [c for c in claims(records(tmp_path / "s"), genuine_only=False) if c["text"].startswith("Revenue fell")]
    assert len(fell) == 2 and all(c["slot"] == 2 for c in fell)
    pk = json.loads((tmp_path / "s" / "packs" / "slot-02.json").read_text())
    cited = {i["id"]: i["text"] for i in pk["items"]}[fell[0]["citations"][0]["id"]]
    assert fell[0]["citations"][0]["quote"] in cited and "increase" in fell[0]["citations"][0]["quote"]
    shown = packet(tmp_path / "s", genuine_only=False)                # the reviewer sees it, blinded
    item = next(i for i in shown["items"] if i["review_id"] == fell[0]["review_id"])
    assert item["claim"].startswith("Revenue fell") and item["citations"][0]["cited_text"] == cited
    assert not {"action", "target_weight_pct", "weight", "outcome"} & set(item)
    before = k["decision|2"]
    record_review(tmp_path / "s", fell[0]["review_id"], "unsupported", ["direction"], "test reviewer",
                  "the quote says revenue rose", END)
    after = by_key(tmp_path / "s")
    assert after["decision|2"] == before                              # a verdict never edits the decision
    from autotrader.invest.report import build_report
    rep = build_report(tmp_path / "s")
    assert fell[0]["review_id"] in rep["diagnostics"]["fixture_claim_review"]["unsupported"]
    assert rep["verdict"].startswith("NOT A RESULT")


def test_fixture_decider_answers_stay_inside_the_caps(tmp_path):
    synthetic.build(tmp_path / "data")
    dry(tmp_path / "s", tmp_path / "data")
    for i in range(1, 13):
        p = by_key(tmp_path / "s")[f"decision|{i}"]["payload"]
        if p["decision"]:
            pk = pack_from_record(json.loads((tmp_path / "s" / "packs" / f"slot-{i:02d}.json").read_text()))
            _, problems, _ = validate(p["decision"], pk)
            assert not [x for x in problems if "characters" in x or "at most" in x and "stocks" not in x], problems
    assert FixtureDecider.name == "fixture" and not FixtureDecider.genuine
