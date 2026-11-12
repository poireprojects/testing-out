"""Shared helpers for the investing-pilot tests: offline data builders, ledger readers, contract-v2 answers and the
network block. Everything here is synthetic; no test may send a request, read a credential or open a connection."""
import json
import socket
from datetime import date
from pathlib import Path

import pytest

from autotrader.invest import synthetic
from autotrader.invest.data import write_data_dir
from autotrader.invest.engine import FixtureDecider, run
from autotrader.invest.registration import et, schedule

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "research" / "runs" / "2026-11-11-invest-pilot" / "dry-run"
END = synthetic.end_of_pilot()
SLOTS = schedule(synthetic.FIRST_FILL_MONTH)
ALL_VOO = {i: {"kind": "decision", "label": "100% VOO", "positions": []} for i in range(1, 13)}
STATE_TYPES = ("decision", "cost", "session_open", "mark")


# ------------------------------------------------------------------ external networking is disabled
class NetworkBlocked(RuntimeError):
    pass


def _refuse(*a, **k):
    raise NetworkBlocked("external networking is disabled in the investing-pilot tests")


@pytest.fixture(scope="module", autouse=True)
def no_network():
    """Module-scoped so module fixtures (shared dry runs) are covered too."""
    mp = pytest.MonkeyPatch()
    mp.setattr(socket.socket, "connect", _refuse)
    mp.setattr(socket.socket, "connect_ex", _refuse)
    mp.setattr(socket, "create_connection", _refuse)
    mp.setattr(socket, "getaddrinfo", _refuse)
    yield
    mp.undo()


# ------------------------------------------------------------------ runs and records
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


FLAT_FILING = {"symbol": "STK1", "cik": "1", "accession": "0000000001-30-000001", "form": "10-Q",
               "period_end": "2030-09-30", "accepted_at": et(date(2030, 11, 5), 16, 5).isoformat(),
               "facts": {"shares_outstanding": "1000000000", "share_classes": "1", "revenue_ttm_musd": "4000",
                         "net_income_ttm_musd": "400", "eps_diluted_ttm": "0.40"},
               "passages": [{"section": "MD&A", "text": "Revenue for the quarter was $1,000 million, an increase of "
                                                        "5% from a year earlier. Margins were stable."},
                            {"section": "adverse disclosure (impairment)", "text":
                                "We recorded an impairment charge of $40 million on a discontinued product line; "
                                "demand for it declined 30% during the quarter."}]}


def flat_data(d: Path, stock_open="50.00", stock_after=None, filings=None, index="auto", prices_extra=None,
              actions=None, members=None, cover="window"):
    """Constant prices (VOO 100.00, STK1 50.00), no dividends: every amount below is checked by hand.
    `stock_after` (day, price): STK1 trades at that price from that day on. `index`: "auto" lists the filings.
    `cover`: the corporate-action coverage when `actions` has none: "window" declares every priced symbol covered
    for the whole window (a labelled fixture), a dict overrides some symbols' intervals, None writes no record."""
    days = synthetic.sessions()
    stk = {}
    for x in days:
        px = stock_after[1] if stock_after and x.isoformat() >= stock_after[0] else None
        stk[x.isoformat()] = {"open": px or stock_open, "close": px or "50.00"}
    prices = {"VOO": {x.isoformat(): {"open": "100.00", "close": "100.00"} for x in days}, "STK1": stk}
    prices |= prices_extra or {}
    filings = [FLAT_FILING] if filings is None else filings
    actions = dict(actions or {"splits": [], "dividends": []})
    if "coverage" not in actions and cover is not None:
        actions["coverage"] = synthetic.fixture_coverage(sorted(prices), override=None if cover == "window" else cover)
    write_data_dir(d, synthetic=True, sources={"all": "test"}, prices=prices,
                   actions=actions,
                   universe={"rule": "test", "source": "test",
                             "members": members or [{"rank": 1, "symbol": "STK1", "name": "Flat Test Co", "cik": "1"}]},
                   filings=filings,
                   index=synthetic.index_for([FLAT_FILING], symbols=["STK1"]) if index == "auto" else index)


# ------------------------------------------------------------------ contract-v2 answers for the flat pack
P1 = "STK1/0000000001-30-000001/P1"
P2 = "STK1/0000000001-30-000001/P2"
TXT = "Revenue grows 5% while the price implies a slowdown; the filing shows no sign of one."


def fact(id_=P1, quote="Revenue for the quarter was $1,000 million", figures=("$1,000 million",),
         text="Quarterly revenue was $1,000 million."):
    return {"type": "fact", "text": text, "evidence": [{"id": id_, "quote": quote, "figures": list(figures)}],
            "inputs": [], "expression": "", "result": 0}


def pe_calc(price=50.0, eps=0.4, result=125.0, src="V.STK1"):
    return {"type": "calculation", "text": "P/E from the valuation row: price over trailing diluted EPS.",
            "evidence": [], "inputs": [{"name": "price", "value": price, "source_id": src},
                                       {"name": "eps", "value": eps, "source_id": src}],
            "expression": "price / eps", "result": result}


def assessment(sym="STK1", rank=1, ex=1.5, p=0.55, note="test"):
    return {"symbol": sym, "rank": rank, "expected_excess_pct_3m": ex, "p_positive_excess": p, "note": note}


def position(sym="STK1", action="hold", w=0, claims=None, **over):
    kept = action != "sell"
    p = {"symbol": sym, "action": action, "target_weight_pct": w,
         "mispricing": TXT if kept else "", "valuation_link": "At a P/E of 125.00 the price assumes growth halves." if kept else "",
         "contrary_evidence": "A $40 million impairment and a 30% demand decline in one product line." if kept else "",
         "downside": "If growth stops, the multiple could fall by a third." if kept else "",
         "weight_rationale": "A small position: the valuation is demanding." if kept else "",
         "what_changed": "", "thesis_still_holds": "Revenue growth continues as the filing describes." if action == "hold" else "",
         "invalidation": {"metric": "quarterly revenue growth", "comparator": "<", "threshold": 2, "unit": "percent",
                          "check_by": "2031-05-15"},
         "scenarios": {"downside": {"return_pct_3m": -12, "reasoning": "Growth stalls and the multiple compresses."},
                       "base": {"return_pct_3m": 2, "reasoning": "Growth continues near 5% and the multiple holds."},
                       "upside": {"return_pct_3m": 10, "reasoning": "Growth re-accelerates and margins recover."}},
         "claims": [fact()] if claims is None else claims}
    if action in ("reduce", "sell"):
        p["what_changed"] = "The impairment widened and demand fell further than the thesis allowed."
    p.update(over)
    return p


def answer(month="2031-02", positions=None, voo=91.0, assessments=None, prefer_voo_reason="", summary="test"):
    return {"month": month, "summary": summary, "voo_weight_pct": voo, "prefer_voo_reason": prefer_voo_reason,
            "assessments": [assessment()] if assessments is None else assessments,
            "positions": [position()] if positions is None else positions}


def increase(w, **over):
    claims = over.pop("claims", [fact(), pe_calc()])
    return answer(positions=[position(action="increase", w=w, claims=claims, **over)], voo=100 - w)
