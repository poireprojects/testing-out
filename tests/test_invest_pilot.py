"""Investing pilot (autotrader/invest): accounting, rules, timing, idempotency, research-input coverage, crash
recovery and labelling. Offline only: synthetic data, fixture decisions, no model call, no network, no credential."""
import json
import shutil
import subprocess
import sys
from datetime import date, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from autotrader.evidence import Evidence, LookAheadError
from autotrader.invest import synthetic
from autotrader.invest.accounting import Book, resulting, settle, stock_orders
from autotrader.invest.claims import claims, packet, record_review
from autotrader.invest.contract import json_schema, validate
from autotrader.invest.data import MarketData, write_data_dir
from autotrader.invest.engine import FixtureDecider, ModelDecider, NotApproved, derive_decision, reconcile, run
from autotrader.invest.ledger import InvestLedger, LedgerConflict, LedgerError, LedgerLocked, StateLock
from autotrader.invest.pack import MonthPack, build_pack, pack_from_record
from autotrader.invest.registration import FROZEN, config_sha256, et, schedule
from autotrader.invest.report import build_report, markdown, verdict, write_report
from autotrader.invest.universe import UniverseError, select_universe

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "research" / "runs" / "2026-11-11-invest-pilot" / "dry-run"
END = synthetic.end_of_pilot()
SLOTS = schedule(synthetic.FIRST_FILL_MONTH)
ALL_VOO = {i: {"kind": "decision", "label": "100% VOO", "positions": []} for i in range(1, 13)}
STATE_TYPES = ("decision", "cost", "session_open", "mark")


def dry(state, data, script=synthetic.FIXTURE_SCRIPT, now=END, cost=synthetic.FIXTURE_COST_USD):
    dec = FixtureDecider(script, cost)
    res = run(state, data, dec, mode="dry_run", first_fill_month=synthetic.FIRST_FILL_MONTH, now=now)
    return res, dec


def records(state):
    return [json.loads(x) for x in (Path(state) / "ledger.jsonl").read_text().splitlines()]


def by_key(state):
    return {r["key"]: r for r in records(state)}


def marks(state, book):
    return {r["payload"]["session"]: r["payload"]["wealth"] for r in records(state)
            if r["type"] == "mark" and r["payload"]["book"] == book}


def state_of(state):
    """The economic and decision content, by key (record order and timestamps aside)."""
    return {r["key"]: r["payload"] for r in records(state) if r["type"] in STATE_TYPES}


@pytest.fixture(scope="module")
def main_run(tmp_path_factory):
    base = tmp_path_factory.mktemp("invest")
    synthetic.build(base / "data")
    res, dec = dry(base / "state", base / "data")
    return base, res, dec


FLAT_FILING = {"symbol": "STK1", "cik": "1", "accession": "0000000001-30-000001", "form": "10-Q",
               "period_end": "2030-09-30", "accepted_at": et(date(2030, 11, 5), 16, 5).isoformat(),
               "facts": {"shares_outstanding": "1000000000", "revenue_ttm_musd": "4000",
                         "net_income_ttm_musd": "400", "eps_diluted_ttm": "0.40"},
               "passages": [{"section": "MD&A", "text": "Revenue for the quarter was $1,000 million, an increase of "
                                                        "5% from a year earlier. Margins were stable."}]}


def flat_data(d: Path, stock_open="50.00", stock_after=None, filings=None, index="auto"):
    """Constant prices (VOO 100.00, STK1 50.00), no dividends: every amount below is checked by hand.
    `stock_after` (day, price): STK1 trades at that price from that day on. `index`: "auto" lists the filings."""
    days = synthetic.sessions()
    stk = {}
    for x in days:
        px = stock_after[1] if stock_after and x.isoformat() >= stock_after[0] else None
        stk[x.isoformat()] = {"open": px or stock_open, "close": px or "50.00"}
    prices = {"VOO": {x.isoformat(): {"open": "100.00", "close": "100.00"} for x in days}, "STK1": stk}
    filings = [FLAT_FILING] if filings is None else filings
    write_data_dir(d, synthetic=True, sources={"all": "test"}, prices=prices,
                   actions={"splits": [], "dividends": []},
                   universe={"rule": "test", "source": "test",
                             "members": [{"rank": 1, "symbol": "STK1", "name": "Flat Test Co", "cik": "1"}]},
                   filings=filings,
                   index=synthetic.index_for([FLAT_FILING], symbols=["STK1"]) if index == "auto" else index)


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
    assert D(rep["voo_end"]) - D(rep["ai_end_all_in"]) == D(rep["model_costs"]["grown_known_usd"])
    assert D(rep["model_costs"]["nominal_known_usd"]) == D("2.40")       # charged every month, even with no answer


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
    assert launch["plan"][0]["order"]["amount"] == "10.00" and D(launch["plan"][0]["resulting_weight_pct"]) <= 10
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


def test_drifted_holding_above_the_cap_cannot_be_held(tmp_path):
    flat_data(tmp_path / "data")
    book = Book("ai", D("0"), {"VOO": D("0.88"), "STK1": D("0.24")})       # STK1 12 of 100 = 12%
    pk = build_pack(SLOTS[1], book, MarketData(tmp_path / "data"), SLOTS[1].cutoff.close, [])
    _, problems, _ = validate(good(pk, ) | {"voo_weight_pct": 88}, pk)
    assert any("above the 10% cap" in p for p in problems)


def test_rejected_decision_changes_nothing_in_the_dry_run(main_run):
    base, _, _ = main_run
    k = by_key(base / "state")
    assert k["decision|5"]["payload"]["status"] == "invalid_answer"
    assert any("4 stocks" in p for p in k["decision|5"]["payload"]["problems"])
    fill = k["session|ai|2031-05-01|open"]["payload"]
    assert fill["trade"] is None and fill["decision"] == {"slot": 5, "status": "invalid_answer"} and fill["plan"] is None
    before = {s: v["qty"] for s, v in k["session|ai|2031-04-30|close"]["payload"]["positions"].items()}
    assert fill["after"]["qty"] == before


# ------------------------------------------------------------------ position limits and minimum orders
OPENS = {"STK1": D("50"), "VOO": D("100")}
SESSION = date(2031, 3, 3)


def _apply(book, targets, opens=OPENS, cutoff_weights=None):
    orders, plan = stock_orders(book, opens, targets, cutoff_weights)
    trade = settle(book, SESSION, opens, orders)
    return orders, plan, trade, resulting(plan, book, opens)


def test_a_risk_required_reduction_overrides_the_discretionary_minimum():
    b = Book("ai", D("0"), {"STK1": D("0.21"), "VOO": D("0.895")})          # 10.50 of 100.00 = 10.5%
    orders, plan, trade, after = _apply(b, {"STK1": {"action": "reduce", "target_pct": "10"}})
    assert [(o.side, str(o.qty), str(o.amount)) for o in orders] == [("sell", "0.010000000", "0.49")]
    e = plan[0]
    assert e["risk_required"] and e["opening_weight_pct"] == "10.5000" and "regardless of the discretionary" in e["note"]
    assert trade["fees"]["total"] == "0.03"                                  # cent rounding at $100: visible, not hidden
    tol = D(FROZEN["execution"]["cap_tolerance_pp"])
    assert D(10) < D(e["resulting_weight_pct"]) <= 10 + tol and e["within_cap_tolerance"]
    assert D(e["deviation_pp"]) == D(e["resulting_weight_pct"]) - 10       # residual deviation recorded


def test_sub_dollar_discretionary_trades_are_still_skipped_and_say_so():
    b = Book("ai", D("0"), {"STK1": D("0.192"), "VOO": D("0.904")})         # 9.6%
    orders, plan = stock_orders(b, OPENS, {"STK1": {"action": "reduce", "target_pct": "9.5"}})
    assert orders == [] and plan[0]["note"].startswith("skipped") and not plan[0]["risk_required"]
    orders, plan = stock_orders(b, OPENS, {"STK1": {"action": "increase", "target_pct": "9.7"}})
    assert orders == [] and plan[0]["note"].startswith("skipped") and plan[0]["order"] is None


def test_opening_gaps_between_the_cutoff_and_the_fill():
    b = Book("ai", D("0"), {"STK1": D("0.212"), "VOO": D("0.894")})         # 10.6% at the open
    _, plan = stock_orders(b, OPENS, {"STK1": {"action": "hold", "target_pct": None}}, {"STK1": "9.90"})
    assert plan[0]["order"] is None and "drift after the cutoff" in plan[0]["note"]   # a hold never trades
    b = Book("ai", D("0"), {"STK1": D("0.194"), "VOO": D("0.903")})         # cutoff 10.4%, gapped down to 9.7%
    orders, plan = stock_orders(b, OPENS, {"STK1": {"action": "reduce", "target_pct": "10"}}, {"STK1": "10.40"})
    assert orders == [] and plan[0]["note"].startswith("no sale") and plan[0]["cutoff_weight_pct"] == "10.40"
    b = Book("ai", D("0"), {"STK1": D("0.22"), "VOO": D("0.89")})           # cutoff 10.4%, gapped up to 11.0%
    orders, plan, _, _ = _apply(b, {"STK1": {"action": "reduce", "target_pct": "10"}}, cutoff_weights={"STK1": "10.40"})
    assert orders[0].side == "sell" and orders[0].qty == D("0.02") and plan[0]["risk_required"]


def test_three_positions_at_the_aggregate_limit():
    opens = {"A1": D("37.13"), "A2": D("211.07"), "A3": D("9.99"), "VOO": D("503.31")}
    b = Book("ai", D("100.00"))
    targets = {s: {"action": "buy", "target_pct": "10"} for s in ("A1", "A2", "A3")}
    orders, plan, trade, after = _apply(b, targets, opens)
    assert len(orders) == 3 and len(trade["orders"]) == 4 and all(e["order"] for e in plan)   # + the VOO sweep
    for e in plan:                                           # each at most the target; rounding only shaves it
        assert D(e["resulting_weight_pct"]) <= 10 and D(e["deviation_pp"]) <= 0 and e["within_cap_tolerance"]
    assert D(after["stocks_pct"]) <= 30 and D(after["voo_pct"]) + D(after["cash_usd"]) >= 70
    assert D(after["cash_usd"]) < 1                          # the sweep invested the rest
    assert all(len(str(o.qty).split(".")[1]) == 9 for o in orders)   # 9-decimal shares, truncated


def test_missed_months_never_trade_and_a_risk_required_reduction_executes(tmp_path):
    flat_data(tmp_path / "data", stock_after=("2031-01-15", "65.00"))       # STK1 jumps 30% after the purchase
    script = {**ALL_VOO, 1: {"kind": "decision", "positions": [("STK1", "buy", 10)]},
              2: {"kind": "missing"},
              3: {"kind": "decision", "positions": [("STK1", "hold", 0)]},
              4: {"kind": "decision", "positions": [("STK1", "reduce", 10)]}}
    dry(tmp_path / "s", tmp_path / "data", script)
    k = by_key(tmp_path / "s")
    assert k["decision|2"]["payload"]["status"] == "missed_answer"
    assert k["session|ai|2031-02-03|open"]["payload"]["trade"] is None      # missed: no forced trade despite 12.6%
    assert k["decision|3"]["payload"]["status"] == "invalid_answer"         # hold above the cap is refused
    assert k["session|ai|2031-03-03|open"]["payload"]["trade"] is None
    fill = k["session|ai|2031-04-01|open"]["payload"]
    e = fill["plan"][0]
    assert e["risk_required"] and e["order"]["side"] == "sell" and D(e["resulting_weight_pct"]) <= D("10.05")
    rep = build_report(tmp_path / "s")
    assert rep["books"]["ai"]["sessions_with_drift_above_cap"] > 0 and rep["books"]["ai"]["risk_required_reductions"] == 1


# ------------------------------------------------------------------ research-input completeness
def test_no_filings_means_no_request_and_an_inconclusive_record(tmp_path):
    flat_data(tmp_path / "data", filings=[])                 # the index lists a 10-Q; nothing was collected
    res, dec = dry(tmp_path / "s", tmp_path / "data", ALL_VOO)
    rep = build_report(tmp_path / "s")
    assert dec.calls == [] and rep["decision_status_counts"]["missing_input"] == 12
    assert not rep["complete"] and any("missing_input" in x for x in rep["incomplete_because"])
    assert rep["books"]["voo"]["marks"] == rep["books"]["ai"]["marks"] == 251   # portfolio marks retained
    assert rep["cost_status_counts"]["not_requested"] == 12 and rep["model_costs"]["nominal_known_usd"] == "0"
    cov = by_key(tmp_path / "s")["decision|1"]["payload"]["coverage"]
    assert cov[0]["status"] == "collection_failure" and "not collected" in cov[0]["reason"]
    flat_data(tmp_path / "noindex", filings=[], index=None)  # and with no index collected at all
    dry(tmp_path / "s2", tmp_path / "noindex", ALL_VOO)
    assert build_report(tmp_path / "s2")["decision_status_counts"]["missing_input"] == 12


def test_one_candidates_missing_collection_blocks_those_months(tmp_path):
    synthetic.build(tmp_path / "data", omit_filings=("9900000007-31-000003",))  # SYNTH07's May 10-Q, not collected
    _, dec = dry(tmp_path / "s", tmp_path / "data")
    k = by_key(tmp_path / "s")
    st = {i: k[f"decision|{i}"]["payload"]["status"] for i in range(1, 13)}
    assert [i for i, v in st.items() if v == "missing_input"] == [6, 7, 8]   # until its August 10-Q is newer
    assert all(i not in dec.calls for i in (6, 7, 8))
    assert "SYNTH07" in k["decision|6"]["payload"]["problems"][0]
    assert not build_report(tmp_path / "s")["complete"]


def test_a_held_stocks_missing_evidence_blocks_the_month_and_keeps_holdings(tmp_path):
    synthetic.build(tmp_path / "data", omit_filings=("9900000001-31-000002",))  # SYNTH01 (held) 10-K, not collected
    dry(tmp_path / "s", tmp_path / "data")
    k = by_key(tmp_path / "s")
    assert [k[f"decision|{i}"]["payload"]["status"] for i in (3, 4, 5)] == ["missing_input"] * 3
    assert k["session|ai|2031-03-03|open"]["payload"]["trade"] is None
    assert not build_report(tmp_path / "s")["complete"]


def test_legitimately_unavailable_evidence_excludes_a_candidate_with_a_reason(tmp_path):
    synthetic.build(tmp_path / "data")                       # SYNTH12: the index lists no filings at all
    d = tmp_path / "data"
    idx = json.loads((d / "index.json").read_text())
    idx["symbols"]["SYNTH12"]["filings"] = []
    fil = [f for f in json.loads((d / "filings.json").read_text()) if f["symbol"] != "SYNTH12"]
    old = MarketData(d)
    write_data_dir(d, synthetic=True, sources=old.manifest["sources"], prices=old.prices,
                   actions={"splits": old.splits, "dividends": old.dividends}, universe=old.universe, filings=fil,
                   index=idx)
    dry(tmp_path / "s", d)
    rep = build_report(tmp_path / "s")
    assert rep["complete"] and rep["decision_status_counts"]["missing_input"] == 0
    pk = json.loads((tmp_path / "s" / "packs" / "slot-01.json").read_text())
    c12 = next(c for c in pk["candidates"] if c["symbol"] == "SYNTH12")
    assert not c12["eligible"] and "lists no 10-K or 10-Q" in c12["why"]


def test_a_fully_collected_pack_and_an_all_voo_answer_is_a_legitimate_decision(tmp_path):
    synthetic.build(tmp_path / "data")
    dry(tmp_path / "s", tmp_path / "data", ALL_VOO)
    rep = build_report(tmp_path / "s")
    assert rep["complete"] and rep["decision_status_counts"]["valid"] == 12 and not rep["pack_problems"]
    pk = json.loads((tmp_path / "s" / "packs" / "slot-01.json").read_text())
    assert {c["symbol"] for c in pk["coverage"]} == {c["symbol"] for c in pk["candidates"]}
    assert all(c["status"] == "complete" for c in pk["coverage"])
    genuine = derive_decision({"slot": 1, "month": "2031-01", "decider": "model", "genuine": True,
                               "cutoff": SLOTS[0].cutoff.close.isoformat(), "deadline": SLOTS[0].deadline.isoformat()},
                              pack_from_record(pk), {"decision": {"month": "2031-01", "summary": "no stock beats VOO",
                                                                  "voo_weight_pct": 100, "positions": []},
                                                     "frozen_at": (SLOTS[0].cutoff.close + timedelta(hours=1)).isoformat(),
                                                     "lost_in_flight": False, "note": ""})
    assert genuine["status"] == "valid" and genuine["targets"] == {} and genuine["genuine"]


def test_no_backfill_after_the_deadline_and_a_frozen_pack_is_never_rewritten(tmp_path):
    synthetic.build(tmp_path / "gappy", omit_filings=("9900000007-31-000003",))
    synthetic.build(tmp_path / "fixed")
    dry(tmp_path / "s", tmp_path / "gappy")
    first = (tmp_path / "s" / "ledger.jsonl").read_bytes()
    packs = {p.name: p.read_bytes() for p in (tmp_path / "s" / "packs").glob("*.json")}
    _, dec = dry(tmp_path / "s", tmp_path / "fixed")         # the missing filing arrives after the deadlines
    assert (tmp_path / "s" / "ledger.jsonl").read_bytes() == first and dec.calls == []
    assert by_key(tmp_path / "s")["decision|6"]["payload"]["status"] == "missing_input"
    assert {p.name: p.read_bytes() for p in (tmp_path / "s" / "packs").glob("*.json")} == packs


# ------------------------------------------------------------------ late and missing
def test_missing_and_late_decisions_keep_the_holdings(main_run):
    base, _, _ = main_run
    k = by_key(base / "state")
    assert (k["decision|4"]["payload"]["status"], k["decision|4"]["payload"]["reason"]) == ("missed_answer", "no_answer")
    assert (k["decision|6"]["payload"]["status"], k["decision|6"]["payload"]["reason"]) == ("missed_answer", "late")
    assert k["cost|4"]["payload"] | {"basis": None} == {"slot": 4, "month": "2031-04", "genuine": False,
                                                       "status": "reconciled", "usd": "0.20", "basis": None}
    for day, prev in (("2031-04-01", "2031-03-31"), ("2031-06-02", "2031-05-30")):
        o = k[f"session|ai|{day}|open"]["payload"]
        before = {s: v["qty"] for s, v in k[f"session|ai|{prev}|close"]["payload"]["positions"].items()}
        assert not (o["trade"] and any(x["reason"] != "sweep" for x in o["trade"]["orders"]))
        assert {s: q for s, q in o["after"]["qty"].items() if s != "VOO"} == {s: q for s, q in before.items() if s != "VOO"}


def test_a_decision_is_recorded_only_once_it_exists_and_never_backdated(tmp_path):
    synthetic.build(tmp_path / "data")
    cutoff = SLOTS[0].cutoff.close                       # the fixture answers at cutoff + 60 minutes
    _, dec = dry(tmp_path / "s", tmp_path / "data", now=cutoff + timedelta(minutes=30))
    k = by_key(tmp_path / "s")
    assert "decision|1" not in k and "request|1" not in k and "pack|1" in k and dec.calls == []
    dry(tmp_path / "s", tmp_path / "data", now=cutoff + timedelta(hours=3))
    p = by_key(tmp_path / "s")["decision|1"]
    assert p["payload"]["status"] == "valid"
    assert p["recorded_at"] == (cutoff + timedelta(minutes=60)).astimezone(timezone.utc).isoformat()
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
    prices = json.loads((tmp_path / "fixed" / "prices.json").read_text())
    assert fill["SYNTH01"]["ref_open"] == prices["SYNTH01"]["2031-01-02"]["open"]
    assert build_report(tmp_path / "s")["complete"]


def test_missing_month_end_close_makes_the_record_incomplete(tmp_path):
    synthetic.build(tmp_path / "data", drop_prices=[("VOO", "2031-03-10", "close")])
    dry(tmp_path / "s", tmp_path / "data")
    rep = build_report(tmp_path / "s")
    assert not rep["complete"]
    assert rep["books"]["voo"]["marks"] < rep["books"]["voo"]["sessions"]
    assert any("not valued" in x for x in rep["incomplete_because"])
    assert by_key(tmp_path / "s")["gap|voo|2031-03-10|close"]["payload"]["missing"] == ["VOO"]


@pytest.mark.parametrize("mode, fixture, synthetic_data, complete, all_in, voo, genuine_valid, expect", [
    ("dry_run", 12, True, True, "120", "100", 0, "NOT A RESULT"),
    ("forward", 0, False, False, "120", "100", 12, "INCONCLUSIVE"),
    ("forward", 0, False, True, "100", "100", 12, "STOP"),
    ("forward", 0, False, True, "99.99", "100", 0, "STOP"),
    ("forward", 0, False, True, "100.01", "100", 0, "INCONCLUSIVE: no valid genuine decision"),
    ("forward", 0, False, True, "100.01", "100", 1, "ELIGIBLE FOR INDEPENDENT REVIEW"),
])
def test_verdict_rules(mode, fixture, synthetic_data, complete, all_in, voo, genuine_valid, expect):
    assert verdict(mode, fixture, synthetic_data, complete, D(all_in), D(voo), genuine_valid).startswith(expect)


# ------------------------------------------------------------------ crash recovery, billing, locking
@pytest.fixture(scope="module")
def short_run(tmp_path_factory):
    """An uninterrupted three-month reference run (decisions 1-3 and their fills)."""
    base = tmp_path_factory.mktemp("crash")
    synthetic.build(base / "data")
    dry(base / "ref", base / "data", now=SLOTS[2].fill.close)
    return base


SLOT_TYPES = ("pack", "request", "answer", "decision", "cost")


def test_crash_at_every_decision_write_boundary_resumes_to_the_same_state(short_run, tmp_path):
    ref = short_run / "ref"
    lines = (ref / "ledger.jsonl").read_text().splitlines()
    cuts = [i for i, ln in enumerate(lines) if json.loads(ln)["type"] in SLOT_TYPES]
    cuts = sorted(set(cuts + [c - 1 for c in cuts]))         # crash just before and just after each write
    want = state_of(ref)
    for n, i in enumerate(cuts):
        st = tmp_path / f"c{n}"
        shutil.copytree(ref / "packs", st / "packs")         # pack files written before their ledger record survive
        (st / "ledger.jsonl").write_text("\n".join(lines[: i + 1]) + "\n")
        kept = {json.loads(ln)["key"] for ln in lines[: i + 1]}
        _, dec = dry(st, short_run / "data", now=SLOTS[2].fill.close)
        asked_before = {int(k.split("|")[1]) for k in kept if k.startswith("request|")}
        assert not set(dec.calls) & asked_before, f"cut {i}: a request was sent twice"
        in_flight = [s for s in asked_before if f"answer|{s}" not in kept]
        got = state_of(st)
        if not in_flight:
            assert got == want, f"cut after line {i} ({json.loads(lines[i])['key']})"
        else:                                                # the one unrecoverable point: the answer was never stored
            s = in_flight[0]
            assert (got[f"decision|{s}"]["status"], got[f"decision|{s}"]["reason"]) == ("missed_answer", "lost_in_flight")
            assert got[f"cost|{s}"]["status"] == "unreconciled" and got[f"cost|{s}"]["usd"] is None


def test_lost_in_flight_billing_stays_unresolved_until_a_separate_reconciliation(short_run, tmp_path):
    ref = short_run / "ref"
    lines = (ref / "ledger.jsonl").read_text().splitlines()
    i = next(n for n, ln in enumerate(lines) if json.loads(ln)["key"] == "request|2")
    shutil.copytree(ref / "packs", tmp_path / "packs")
    (tmp_path / "ledger.jsonl").write_text("\n".join(lines[: i + 1]) + "\n")
    _, dec = dry(tmp_path, short_run / "data", now=END)
    assert 2 not in dec.calls
    rep = build_report(tmp_path)
    ch = next(c for c in rep["model_costs"]["charges"] if c["slot"] == 2)
    assert not rep["complete"] and ch["usd"] is None and ch["status"] == "unreconciled"
    assert any("unreconciled" in x for x in rep["incomplete_because"])
    before = (tmp_path / "ledger.jsonl").read_bytes()
    with pytest.raises(LedgerError):
        reconcile(tmp_path, 3, "0.20", "usage report", END)  # slot 3's charge is already known
    rec = reconcile(tmp_path, 2, "0.23", "provider usage report row (synthetic test)", END)
    assert rec["key"] == "reconcile|2" and (tmp_path / "ledger.jsonl").read_bytes().startswith(before)
    rep = build_report(tmp_path)
    assert rep["complete"] and next(c for c in rep["model_costs"]["charges"] if c["slot"] == 2)["usd"] == "0.23"
    assert by_key(tmp_path)["decision|2"]["payload"]["status"] == "missed_answer"   # the decision stays missed


def test_unknown_billing_is_never_zero(tmp_path):
    synthetic.build(tmp_path / "data")
    script = {**synthetic.FIXTURE_SCRIPT, 2: dict(synthetic.FIXTURE_SCRIPT[2], cost="unknown")}
    dry(tmp_path / "s", tmp_path / "data", script)
    rep = build_report(tmp_path / "s")
    assert by_key(tmp_path / "s")["cost|2"]["payload"]["status"] == "unreconciled"
    assert not rep["complete"] and D(rep["model_costs"]["nominal_known_usd"]) == D("2.20")


def test_concurrent_starts_are_refused(tmp_path, short_run):
    held = StateLock(tmp_path / "s")
    with pytest.raises(LedgerLocked):
        dry(tmp_path / "s", short_run / "data", now=SLOTS[0].fill.close)
    held.release()
    code = ("import sys; sys.path.insert(0, %r); from autotrader.invest.ledger import StateLock; "
            "l = StateLock(%r); print('held', flush=True); sys.stdin.readline()") % (str(ROOT), str(tmp_path / "s"))
    p = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held"
        with pytest.raises(LedgerLocked):
            dry(tmp_path / "s", short_run / "data", now=SLOTS[0].fill.close)
    finally:
        p.communicate("go\n", timeout=30)
    dry(tmp_path / "s", short_run / "data", now=SLOTS[0].fill.close)    # the lock died with its holder
    assert "decision|1" in by_key(tmp_path / "s")


def test_a_torn_final_write_is_set_aside_and_a_malformed_record_refuses(short_run, tmp_path):
    ref = short_run / "ref"
    lines = (ref / "ledger.jsonl").read_text().splitlines()
    i = next(n for n, ln in enumerate(lines) if json.loads(ln)["key"] == "answer|2")
    shutil.copytree(ref / "packs", tmp_path / "t" / "packs")
    (tmp_path / "t" / "ledger.jsonl").write_text("\n".join(lines[:i]) + "\n" + lines[i][: len(lines[i]) // 2])
    dry(tmp_path / "t", short_run / "data", now=SLOTS[2].fill.close)
    k = by_key(tmp_path / "t")
    rec = next(r for r in k.values() if r["type"] == "recovery")
    assert (tmp_path / "t" / rec["payload"]["moved_to"]).read_text() == lines[i][: len(lines[i]) // 2]
    assert k["decision|2"]["payload"]["reason"] == "lost_in_flight"      # the torn answer was never acknowledged
    bad = lines[:5] + ['{"seq": 5, "not": "a record"}'] + lines[6:]
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "ledger.jsonl").write_text("\n".join(bad) + "\n")
    with pytest.raises(LedgerError, match="verification"):
        dry(tmp_path / "m", short_run / "data", now=SLOTS[2].fill.close)


def test_every_append_is_fsynced(tmp_path, monkeypatch):
    import autotrader.invest.ledger as L
    calls = []
    real = L.os.fsync
    monkeypatch.setattr(L.os, "fsync", lambda fd: (calls.append(fd), real(fd)))
    synthetic.build(tmp_path / "data")
    res, _ = dry(tmp_path / "s", tmp_path / "data", now=SLOTS[0].fill.close)
    assert len(calls) >= res["appended"]


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
    with InvestLedger(base / "state" / "ledger.jsonl", "dry_run",
                      by_key(base / "state")["open"]["payload"]["identity"], END) as led:
        with pytest.raises(LedgerConflict):
            led.put("decision|1", "decision", {"status": "valid", "decision": {"positions": []}}, END)
    copy = tmp_path / "ledger.jsonl"
    lines = (base / "state" / "ledger.jsonl").read_text().splitlines()
    lines[40] = lines[40].replace('"0.', '"9.', 1)
    copy.write_text("\n".join(lines) + "\n")
    ident = json.loads(lines[0])["payload"]["identity"]
    with pytest.raises(LedgerError):
        InvestLedger(copy, "dry_run", ident, END)


# ------------------------------------------------------------------ claims, fixture vs genuine
def test_claim_review_is_blind_append_only_and_never_edits_a_decision(main_run, tmp_path):
    base, _, _ = main_run
    st = tmp_path / "s"
    shutil.copytree(base / "state", st, ignore=shutil.ignore_patterns(".lock"))
    assert claims(records(st)) == [] and len(claims(records(st), genuine_only=False)) == 16
    with pytest.raises(Exception, match="not blinded"):
        from autotrader.review import check_blind
        check_blind([{"action": "buy"}])
    pk = packet(st)                                          # genuine claims only: none in a fixture run
    assert pk["items"] == [] and "valuation" in pk["criteria"]
    c = claims(records(st), genuine_only=False)[0]
    dec_before = by_key(st)[f"decision|{c['slot']}"]
    record_review(st, c["review_id"], "unsupported", ["direction"], "test reviewer", "direction reversed", END)
    k = by_key(st)
    assert k[f"claim_review|{c['review_id']}"]["payload"]["verdict"] == "unsupported"
    assert k[f"decision|{c['slot']}"] == dec_before          # the frozen decision and its trades are untouched
    with pytest.raises(ValueError):
        record_review(st, "nope", "supported", [], "r", "", END)
    with pytest.raises(ValueError):
        record_review(st, c["review_id"], "supported", ["unit"], "r", "", END)


def test_reports_label_fixture_decisions_and_never_call_them_a_result(main_run):
    base, _, _ = main_run
    rep = write_report(base / "state")
    md = (base / "state" / "REPORT.md").read_text()
    assert rep["decisions_fixture"] == 12 and rep["decisions_genuine"] == 0 and rep["decisions_genuine_valid"] == 0
    assert rep["verdict"].startswith("NOT A RESULT") and "FIXTURE DECISIONS, SYNTHETIC DATA" in md
    assert md.count("(FIXTURE)") == 12 and rep["complete"] and rep["packs_frozen"] == 12 and not rep["pack_problems"]
    statuses = [d["status"] for d in rep["decisions"]]
    assert statuses == ["valid"] * 3 + ["missed_answer", "invalid_answer", "missed_answer"] + ["valid"] * 6
    assert "paper-model convention" in md


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
    pk = pack_from_record(rec)
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


def test_the_report_rebuilds_offline_from_the_ledger_and_packs_alone(tmp_path):
    synthetic.build(tmp_path / "data")
    dry(tmp_path / "s", tmp_path / "data")
    first = build_report(tmp_path / "s")
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
