"""Phase 5 development-evaluation preflight (amendment 1): the sample, the inputs, the labels, timing and costs,
the dry run's reconciliation and its fail-closed behaviour, resume after a crash, and the claim review.
Everything runs offline on synthetic data with the 160 real event identities."""
from __future__ import annotations

import ast
import copy
import json
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx2
import pandas as pd
import pytest

from autotrader import execution, fees_hist, labels
from autotrader.deciders import COST_CONVENTIONS, LLMResearcherV2
from autotrader.decision import grounding_problems_v2, parse_ai_decision_v2, sentences
from autotrader.devrun import RunIncomplete, run
from autotrader.evidence import Evidence, EvidencePack, LookAheadError
from autotrader.experiment import (FROZEN, SampleError, canonical_sha256, load_sample, select_rows, verify_sample)
from autotrader.review import check_blind, packet, score
from autotrader.risk import Quote, RiskController, SimPortfolio
from autotrader.policy import OwnerPolicy
from autotrader.sources import CachedEdgar, InputIncomplete, NotCached, ReplaySource
from autotrader.synthetic import make_cache
from stockpilot.clock import ET

ROOT = Path(__file__).resolve().parents[1]
try:
    import anthropic as _anthropic
    PINNED = _anthropic.__version__ == FROZEN["sdk"]["version"]
except ImportError:
    PINNED = False
needs_pinned_sdk = pytest.mark.skipif(not PINNED, reason="the paid path is pinned to anthropic 2.7.0")
SAMPLE = json.loads((ROOT / "research/runs/2026-11-07-phase5/dev-eval/sample.json").read_text())


@pytest.fixture(scope="module")
def syn(tmp_path_factory):
    d = tmp_path_factory.mktemp("syn")
    make_cache(d)
    return d


@pytest.fixture(scope="module")
def dry(syn, tmp_path_factory):
    out = tmp_path_factory.mktemp("dry")
    return out, run(syn, out)


# ------------------------------------------------------------------ 2. the sample
def test_the_sample_is_exactly_the_registered_one():
    s = load_sample()
    assert canonical_sha256(s["events"]) == "e825d6d63dc824801fb9ce4bae18e92af293ba50bae081469dbb211bf738f68b"
    days = {e["day"] for e in s["events"]}
    assert len(s["events"]) == 160 and len(days) == 40
    assert sum(e["day"] > "2026-06-30" for e in s["events"]) == 16        # after the model's training cutoff


@pytest.mark.parametrize("tamper", ["reorder", "fold", "drop", "duplicate", "extra_key"])
def test_any_change_to_the_sample_is_refused(tamper):
    s = copy.deepcopy(SAMPLE)
    ev = s["events"]
    if tamper == "reorder":
        ev[0], ev[1] = ev[1], ev[0]
    elif tamper == "fold":
        ev[0]["fold"] = "eval"
    elif tamper == "drop":
        ev.pop()
    elif tamper == "duplicate":
        ev[-1] = dict(ev[0])
    elif tamper == "extra_key":
        ev[0]["note"] = "x"
    with pytest.raises(SampleError):
        verify_sample(s)
    if tamper in ("fold", "drop", "duplicate", "extra_key"):
        s["sha256"] = canonical_sha256(ev)                   # even a self-consistent file is not the registered one
        with pytest.raises(SampleError):
            verify_sample(s)


def _plan(syn):
    return json.loads((syn / "plan.json").read_text())


def test_rows_match_on_date_ticker_accession_and_fold_exactly(syn):
    plan = _plan(syn)
    rows = select_rows(load_sample(), plan)
    assert len(rows) == 160 and [r["day"] for r in rows] == sorted(r["day"] for r in rows)
    bad = copy.deepcopy(plan)
    bad[5]["fold"] = "eval" if bad[5]["fold"] == "dev" else "dev"          # same ticker and accession, other fold
    with pytest.raises(SampleError):
        select_rows(load_sample(), bad)
    with pytest.raises(SampleError):
        select_rows(load_sample(), plan[1:])                                # a missing row stops everything


def test_the_old_replay_command_now_fails_closed_on_a_missing_row(tmp_path):
    from autotrader.__main__ import main
    d = tmp_path / "d"
    make_cache(d, drop_plan_rows=1)
    with pytest.raises(SampleError):
        main(["replay", str(d), str(tmp_path / "o"), "--sample", str(ROOT / FROZEN["sample"]["path"])])


def test_the_command_line_offers_no_paid_model_ceiling_or_sample_override():
    from autotrader.__main__ import _provider, main
    import argparse
    with pytest.raises(SystemExit):
        _provider(argparse.Namespace(llm="anthropic"), 160)
    with pytest.raises(SystemExit):
        main(["replay", "d", "o", "--llm", "anthropic", "--budget-usd", "99"])     # the option no longer exists


# ------------------------------------------------------------------ evidence version 2
def _row(syn):
    return select_rows(load_sample(), _plan(syn))[0]                        # 2023-01-13, before 2024-10-31


def test_version_1_packs_before_the_research_note_date_fail_the_look_ahead_guard(syn):
    src = ReplaySource(syn, None)
    with pytest.raises(LookAheadError):
        src.pack(_row(syn))                                                  # the defect the amendment removes
    p2 = src.pack(_row(syn), evidence_version="2")
    assert all(i.kind != "research" for i in p2.items)


def test_the_exact_input_diff_is_the_note_removed_and_the_cost_conventions_added(syn):
    rows = select_rows(load_sample(), _plan(syn))
    late = next(r for r in rows if r["day"] > "2024-11-01")
    src = ReplaySource(syn, CachedEdgar(syn / "sec_cache"))
    p1, p2 = src.pack(late), src.pack(late, evidence_version="2")
    u1 = LLMResearcherV2(None).request(p1)[1]
    u2 = LLMResearcherV2(None, cost_note=COST_CONVENTIONS).request(p2)[1]
    removed = [line for line in u1.splitlines() if line not in u2.splitlines()]
    added = [line for line in u2.splitlines() if line not in u1.splitlines()]
    assert removed and all("research" in line or "project's own research" in line or "costs" in line or
                           line.startswith("[E") for line in removed)
    assert added == [COST_CONVENTIONS]
    assert not any(ch.isdigit() for ch in COST_CONVENTIONS.replace("0.05%", "").replace("0.10%", ""))


def test_a_missing_release_is_input_incomplete_never_a_price_only_pack(tmp_path):
    d = tmp_path / "d"
    sym = SAMPLE["events"][0]["symbol"]
    make_cache(d, no_filing_for=(sym,))
    row = next(r for r in select_rows(load_sample(), _plan(d)) if r["symbol"] == sym)
    src = ReplaySource(d, CachedEdgar(d / "sec_cache"))
    with pytest.raises((InputIncomplete, NotCached)):
        src.pack(row, evidence_version="2", require_release=True)


def test_the_cache_reader_never_fetches(tmp_path):
    e = CachedEdgar(tmp_path)
    with pytest.raises(NotCached):
        e.get("https://www.sec.gov/anything")
    assert e.requests == 0


# ------------------------------------------------------------------ labels are separate from inputs
PROMPT_MODULES = ("evidence", "sources", "decision", "deciders", "experiment", "paid", "journal")


@pytest.mark.parametrize("mod", PROMPT_MODULES)
def test_no_input_builder_imports_the_labels(mod):
    tree = ast.parse((ROOT / "autotrader" / f"{mod}.py").read_text())
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names} | \
            {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "labels" not in names and not any("labels" in x for x in names)


def _facts(rows):
    return {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": rows}}}}}


def test_the_eps_label_is_the_first_as_filed_fact_never_a_later_comparative():
    q = {"start": "2025-04-01", "end": "2025-06-30"}
    rows = [q | {"val": 1.30, "form": "10-Q", "filed": "2025-08-05", "accn": "A", "fy": 2025, "fp": "Q2"},
            q | {"val": 1.25, "form": "10-Q", "filed": "2026-08-04", "accn": "B", "fy": 2026, "fp": "Q2"},
            {"start": "2026-04-01", "end": "2026-06-30", "val": 1.6, "form": "10-Q", "filed": "2026-08-04", "accn": "B"}]
    lab = labels.eps_label(_facts(rows), date(2025, 7, 24))
    assert lab["checkable"] and lab["value"] == 1.30 and lab["accession"] == "A"
    lab2 = labels.eps_label(_facts(rows[1:]), date(2025, 7, 24))               # only the later comparative exists
    assert not lab2["checkable"]


def test_a_fourth_quarter_reported_only_annually_is_not_checkable():
    rows = [{"start": "2025-01-01", "end": "2025-12-31", "val": 5.0, "form": "10-K", "filed": "2026-02-20", "accn": "K"}]
    assert not labels.eps_label(_facts(rows), date(2026, 1, 30))["checkable"]


def test_the_eps_tolerance_is_exactly_one_cent():
    lab = {"checkable": True, "value": 1.30}
    assert labels.eps_correct(1.31, lab) is True                    # 1.31 - 1.30 is 0.010000000000000009 in floats
    assert labels.eps_correct(1.3105, lab) is False                 # accepted by the old 0.011 rule
    assert abs(1.3105 - 1.30) <= 0.011
    assert labels.eps_correct(None, lab) is None


# ------------------------------------------------------------------ 3. fees (research schedule only)
@pytest.mark.parametrize("day,sec", [("2023-02-26", 22.90), ("2023-02-27", 8.00), ("2024-05-21", 8.00),
                                     ("2024-05-22", 27.80), ("2025-05-13", 27.80), ("2025-05-14", 0.0),
                                     ("2026-04-03", 0.0), ("2026-04-04", 20.60)])
def test_historical_sec_rates_switch_on_their_effective_dates(day, sec):
    assert fees_hist.rates_on(date.fromisoformat(day)).sec_per_million == pytest.approx(sec)


def test_historical_fees_in_cents_and_the_app_schedule_is_unchanged():
    # 0.2 shares sold for $20.00 on 2023-03-01: SEC 20 x 8/1e6 = $0.00016, TAF 0.2 x 0.000145 = $0.000029,
    # CAT 2 x 0.2 x 0.000003 = $0.0000012; each daily total rounds up to one cent: $0.03 for the day.
    parts = fees_hist.fee_parts(date(2023, 3, 1), 0.2, 20.0)
    assert parts == pytest.approx({"sec": 0.00016, "taf": 0.000029, "cat": 0.0000012})
    per_trade, rounded = fees_hist.daily_fees([parts])
    assert rounded == {"sec": 0.01, "taf": 0.01, "cat": 0.01} and per_trade == [pytest.approx(0.03)]
    # $1,000,000 sold on 2024-06-03: SEC $27.80 exactly; TAF capped at $8.30 for 10,000 shares
    big = fees_hist.fee_parts(date(2024, 6, 3), 10_000, 1_000_000.0)
    assert big["sec"] == pytest.approx(27.80) and big["taf"] == pytest.approx(1.66)
    assert fees_hist.fee_parts(date(2024, 6, 3), 100_000, 1_000_000.0)["taf"] == pytest.approx(8.30)
    assert fees_hist.rates_on(date(2026, 10, 1)).taf_per_share == 0.0
    from stockpilot.risk.fees import rates_on as app_rates
    assert app_rates(date(2023, 3, 1)).sec_rate == 0.0                     # desktop behaviour untouched
    assert len(fees_hist.unverified()) == 6
    with pytest.raises(fees_hist.FeeScheduleGap):
        fees_hist.rates_on(date(2022, 1, 3))


# ------------------------------------------------------------------ execution v2
DAY = date(2024, 3, 5)


class Prices:
    def __init__(self, bars, q, prev=100.0):
        self._bars, self.q, self.prev = bars, q, prev

    def bars15(self, s, day):
        return self._bars

    def prev_close(self, s, day):
        return self.prev


def _bars(lows=None, highs=None, drop=()):
    idx, rows = [], []
    for i in range(26):
        t = datetime.combine(DAY, dtime(9, 30), ET) + timedelta(minutes=15 * i)
        if t.time() in drop:
            continue
        idx.append(t)
        rows.append({"open": 100.0, "high": (highs or {}).get(t.time(), 100.2), "low": (lows or {}).get(t.time(), 99.8),
                     "close": 100.0, "volume": 1000.0})
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def _quotes(hs=2.0, n=40):
    return {f"{DAY.isoformat()}|entry": {"AAA": {"mid": 100.0, "halfSpreadBps": hs, "n": n}},
            f"{DAY.isoformat()}|exit": {"AAA": {"mid": 100.5, "halfSpreadBps": hs, "n": n}}}


def _pack():
    t = datetime.combine(DAY, dtime(10, 0), ET)
    return EvidencePack("AAA:1", "AAA", "XLK", t, [Evidence("E1", "price", "x", t, "AAA prior close $100.00.")], {})


def _dec(exit_time="15:45", stop=1.0, target=None, spread=30, min_move=None, seq=1, decider="price_only"):
    return {"seq": seq, "payload": {"decider": decider, "decision": {
        "action": "long", "instrument": "AAA", "valid": True, "decider": decider,
        "entry": {"max_spread_bps": spread, "min_move_pct": min_move, "max_move_pct": None},
        "exit": {"stop_pct": stop, "target_pct": target, "exit_time": exit_time}}}}


def _exec(prices, variant=execution.VARIANTS[0]):
    ctl = RiskController(OwnerPolicy.load(), {"AAA", "XLK"})
    return execution.DevExecutor(ctl, prices, execution.ProxyQuotes(prices), variant)


def test_quotes_are_labelled_proxies_and_executability_is_not_claimed():
    assert execution.EXECUTABLE_VERIFIED is False
    pq = execution.ProxyQuotes(Prices(_bars(), _quotes())).entry("AAA", DAY)
    assert pq.window_start.time() == dtime(10, 0, 30) and pq.window_end.time() == dtime(10, 1, 30)
    assert "proxy" in pq.convention


def test_serial_model_latency_decides_whether_the_deadline_is_met():
    clocks = execution.model_clocks([12_000, 15_000, None, 4_000])
    assert [c.ready_offset_ms for c in clocks] == [12_000, 27_000, None, 31_000]
    p = Prices(_bars(), _quotes())
    for c, want in ((clocks[1], "filled"), (clocks[3], "missed_deadline"), (clocks[2], "missed_deadline")):
        out = _exec(p).execute_day(DAY, [(_dec(), _pack(), c)])
        assert out[0].status == want
    ideal = _exec(p, execution.VARIANTS[1]).execute_day(DAY, [(_dec(), _pack(), clocks[3])])
    assert ideal[0].status == "filled"


@pytest.mark.parametrize("hhmm", ["11:00", "12:00", "13:00", "14:00", "15:00"])
def test_early_exits_use_that_bars_open_with_the_entry_spread_and_say_so(hhmm):
    out = _exec(Prices(_bars(), _quotes())).execute_day(DAY, [(_dec(exit_time=hhmm), _pack(), execution.RULE_CLOCK)])
    o = out[0].outcome
    assert o["scored"] and o["reason"] == "time" and o["exit_at"].endswith(f"{hhmm}:00-05:00")
    assert any("entry half-spread" in f for f in o["flags"]) and o["reconciles"]


def test_a_15_45_exit_uses_the_exit_window_median():
    o = _exec(Prices(_bars(), _quotes())).execute_day(DAY, [(_dec(), _pack(), execution.RULE_CLOCK)])[0].outcome
    assert o["exit_ref"] == 100.5 and any("15:45:00-15:46:00" in f for f in o["flags"])


def test_incomplete_bars_make_the_trade_unscorable():
    o = _exec(Prices(_bars(drop=(dtime(12, 15),)), _quotes())).execute_day(
        DAY, [(_dec(), _pack(), execution.RULE_CLOCK)])[0]
    assert o.status == "filled" and o.outcome["scored"] is False and "incomplete intraday bars" in o.outcome["flags"][0]


def test_stop_first_when_one_bar_reaches_both_and_the_straddling_bar_never_credits_a_target():
    both = _bars(lows={dtime(11, 0): 98.0}, highs={dtime(11, 0): 103.0})
    o = _exec(Prices(both, _quotes())).execute_day(DAY, [(_dec(target=2), _pack(), execution.RULE_CLOCK)])[0].outcome
    assert o["reason"] == "stop"
    straddle_up = _bars(highs={dtime(10, 0): 104.0})
    o2 = _exec(Prices(straddle_up, _quotes())).execute_day(DAY, [(_dec(target=2), _pack(), execution.RULE_CLOCK)])[0]
    assert o2.outcome["reason"] == "time"
    straddle_dn = _bars(lows={dtime(10, 0): 97.0})
    o3 = _exec(Prices(straddle_dn, _quotes())).execute_day(DAY, [(_dec(), _pack(), execution.RULE_CLOCK)])[0].outcome
    assert o3["reason"] == "stop" and any("straddles" in f for f in o3["flags"])


def test_a_move_condition_without_a_prior_close_is_rejected_not_skipped():
    p = Prices(_bars(), _quotes(), prev=None)
    out = _exec(p).execute_day(DAY, [(_dec(min_move=-5.0), _pack(), execution.RULE_CLOCK)])
    assert out[0].status == "rejected" and any("prior close is missing" in r for r in out[0].reasons)
    assert _exec(p).execute_day(DAY, [(_dec(), _pack(), execution.RULE_CLOCK)])[0].status == "filled"


def test_missing_quotes_reject_the_entry():
    q = {f"{DAY.isoformat()}|entry": {}}
    out = _exec(Prices(_bars(), q)).execute_day(DAY, [(_dec(), _pack(), execution.RULE_CLOCK)])
    assert out[0].status == "rejected" and any("no quote" in r for r in out[0].reasons)


def test_doubled_costs_rerun_fill_eligibility_from_the_same_decision():
    p = Prices(_bars(), _quotes(hs=4.0))                                       # spread 8 bps; 16 bps when doubled
    d = _dec(spread=10)
    assert _exec(p).execute_day(DAY, [(d, _pack(), execution.RULE_CLOCK)])[0].status == "filled"
    two = _exec(p, execution.VARIANTS[2]).execute_day(DAY, [(d, _pack(), execution.RULE_CLOCK)])[0]
    assert two.status == "rejected" and "spread" in two.reasons[0]


def test_fees_are_aggregated_per_decider_per_day_and_every_trade_reconciles():
    p = Prices(_bars(), _quotes())
    ex = _exec(p)
    out = ex.execute_day(DAY, [(_dec(seq=1), _pack(), execution.RULE_CLOCK),
                               (_dec(seq=2, decider="rule_p4h1"), _pack(), execution.RULE_CLOCK)])
    for d in out:
        o = d.outcome
        assert o["reconciles"]
        assert o["gross_usd"] - o["spread_usd"] - o["slippage_usd"] - o["fees_usd"] == pytest.approx(o["pnl_usd"])
        assert o["fees_usd"] == pytest.approx(0.03)                          # one trade per decider: 3 types x 1 cent


def test_contested_slots_follow_the_frozen_order():
    p = Prices(_bars(), _quotes())
    ex = _exec(p)
    pk = _pack()
    entries = [(_dec(seq=i), EvidencePack(f"AAA:{i}", "AAA", "XLK", pk.decision_time, pk.items, {}),
                execution.RULE_CLOCK) for i in range(3)]
    out = ex.execute_day(DAY, entries)
    assert out[0].status == "filled" and all(o.status == "rejected" for o in out[1:])   # one position per symbol


# ------------------------------------------------------------------ 4/5. the dry run
def test_the_dry_run_degrades_without_the_pinned_sdk(syn, tmp_path, monkeypatch):
    import anthropic
    monkeypatch.setattr(anthropic, "__version__", "9.9.9")
    rep = run(syn, tmp_path / "o")
    man = json.loads((tmp_path / "o" / "inputs_manifest.json").read_text())["events"]
    assert rep["status"] == "complete" and not rep["budget_proof"]["proven"]
    assert all("request_not_rendered" in m for m in man.values())


def test_the_dry_run_reconciles_all_160_events_in_every_arm(dry):
    out, rep = dry
    rec = rep["reconciliation"]
    assert rep["status"] == "complete" and rep["dry_run"] and rec["ledger_verified"]
    assert rec["decisions_per_arm"] == {"llm": 160, "no_trade": 160, "price_only": 160, "rule_p4h1": 160}
    assert rec["market_outcomes"] == 160 and rec["proposed_without_disposition"] == 0
    assert rep["gates"]["ai_status_counts"] == {"not_run": 160} and rep["gates"]["all_pass"] is False
    assert not rep["budget_proof"]["proven"]
    rows = (out / "coverage.csv").read_text().strip().splitlines()
    assert len(rows) == 161
    assert len(list((out / "inputs").glob("*.json"))) == 160


def test_the_report_is_reproduced_offline_from_the_files(dry):
    out, rep = dry
    from autotrader.devreport import rebuild
    assert json.loads(json.dumps(rebuild(out), default=str)) == json.loads((out / "results.json").read_text())


def test_a_missing_quote_makes_the_run_incomplete_not_successful(tmp_path):
    d = tmp_path / "d"
    make_cache(d, drop_quotes_for=(SAMPLE["events"][3]["symbol"],))
    rep = run(d, tmp_path / "o")
    assert rep["status"] == "INCOMPLETE" and rep["reconciliation"]["market_outcomes"] < 160


def test_a_missing_plan_row_stops_before_anything_is_built(tmp_path):
    d = tmp_path / "d"
    make_cache(d, drop_plan_rows=1)
    with pytest.raises(SampleError):
        run(d, tmp_path / "o")
    assert not (tmp_path / "o").exists()


def test_a_paid_run_is_blocked_by_the_budget_proof_before_any_client_exists(syn, tmp_path):
    made = []
    with pytest.raises(RunIncomplete):
        run(syn, tmp_path / "o", lambda ident, man: made.append(1), require_paid_proof=True)
    assert made == []


# ------------------------------------------------------------------ the model arm through the journal (fake transport)
def _answer_for(req) -> dict:
    body = json.loads(req.content)
    sch = body["output_config"]["format"]["schema"]["properties"]
    ref = sch["facts"]["items"]["properties"]["refs"]["items"]["enum"][0]
    return {"facts": [{"refs": [ref], "claim": "The company reported results"}], "interpretations": [],
            "uncertainties": [], "priced_in": "unclear", "reported": [], "p_up_pct": 52,
            "expected_net_return_bps": -5, "decision": "no_trade", "trade": None, "invalidation": [],
            "reasons_to_avoid": ["costs"], "rationale": "No edge after costs.", "confidence": "low"}


class FakeModel:
    def __init__(self, die_at=None):
        self.n, self.die_at = 0, die_at

    def __call__(self, req):
        self.n += 1
        if self.die_at is not None and self.n == self.die_at:
            raise KeyboardInterrupt("process killed while the request was in flight")
        usage = {"input_tokens": 3000, "output_tokens": 600, "service_tier": "standard", "inference_geo": "global"}
        return httpx2.Response(200, headers={"request-id": f"req_{self.n}"}, json={
            "id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "stop_reason": "end_turn",
            "stop_sequence": None, "usage": usage, "content": [{"type": "text", "text": json.dumps(_answer_for(req))}]})


def _factory(state, model, clock=None):
    from autotrader.journal import open_journal
    from autotrader.paid import PaidCaller, build_client
    out_holder = {}

    def f(ident, manifest):
        events = sorted(e for e, m in manifest.items() if m["input_status"] == "complete")
        j = open_journal(state, ident.body, ident.experiment_id, out_holder["out"], ceiling_micro=12_500_000,
                         per_call_max_micro=78_000, events=events)
        j.preauthorize({e: 78_000 for e in events})
        c = build_client("test-not-a-key", http_client=httpx2.Client(transport=httpx2.MockTransport(model)), env={})
        kw = {"clock": clock} if clock else {}
        return PaidCaller(j, c, lambda n: 4500, **kw)            # a test bound: the production bound is unproven
    return f, out_holder


@needs_pinned_sdk
def test_the_model_arm_runs_through_the_journal_and_resumes_after_a_crash_without_resending(syn, tmp_path):
    model = FakeModel(die_at=7)
    f, h = _factory(tmp_path / "state", model)
    h["out"] = tmp_path / "o"
    with pytest.raises(KeyboardInterrupt):
        run(syn, h["out"], f)
    assert model.n == 7
    model.die_at = None
    f2, h2 = _factory(tmp_path / "state", model)
    h2["out"] = h["out"]
    rep = run(syn, h["out"], f2)
    assert model.n == 7 + 153                                             # 6 read back, 1 held, 153 new: none resent
    st = rep["gates"]["ai_status_counts"]
    assert st["grounded_valid"] == 159 and st.get("infrastructure_failure") == 1
    assert rep["reconciliation"]["complete"] and rep["reconciliation"]["journal_within_ceiling"]
    j = rep["reconciliation"]["journal"]
    assert j["attempts"] == 160 and j["states"]["held"] == 1 and j["committed_micro"] == 159 * (3000 * 4 + 600 * 20) + 78_000
    assert rep["gates"]["spend"]["pass"]


@needs_pinned_sdk
def test_serial_latency_makes_late_requests_miss_the_deadline(syn, tmp_path):
    t = [0.0]

    def clock():
        t[0] += 10.0                                       # read before and after each call: 10 s per request
        return t[0]
    model = FakeModel()
    f, h = _factory(tmp_path / "state", model, clock)
    h["out"] = tmp_path / "o"
    run(syn, h["out"], f)
    led = [json.loads(x) for x in (h["out"] / "ledger.jsonl").read_text().splitlines()]
    clocks = [r["payload"]["decision"]["clock"] for r in led if r["type"] == "decision" and r["payload"]["decider"] == "llm"]
    assert [c["queue_position"] for c in clocks[:4]] == [1, 2, 3, 4]
    assert [c["ready_offset_ms"] for c in clocks[:4]] == [10_000, 20_000, 30_000, 40_000]
    late = [c for c in clocks if c["ready_offset_ms"] > 30_000]
    assert len(late) == 40                                     # the fourth request of every date is late


def test_a_circuit_breaker_skip_is_never_a_valid_answer():
    from autotrader.runner import Autonomy
    from autotrader.ledger import Ledger
    import tempfile
    led = Ledger(Path(tempfile.mkdtemp()) / "l.jsonl", "replay")
    a = Autonomy(led, OwnerPolicy.load(), [], RiskController(OwnerPolicy.load(), set()))

    class D:
        name = "llm"
    a.model_down.add("llm")
    d = a.decide(D(), _pack())
    assert d["valid"] is False and d["answered"] is False and d["skip"] == "circuit_breaker"


# ------------------------------------------------------------------ claim review
def _review_pack():
    t = datetime(2026, 1, 2, 10, 0, tzinfo=ET)
    return EvidencePack("ZZZ:1", "ZZZ", "XLK", t, [Evidence("E1", "filing", "8-K", t,
                        "Diluted EPS rose to $1.30 from $1.02 a year ago. Revenue was $500 million.")], {})


def test_numeric_overlap_passes_validation_but_the_review_catches_the_reversed_claim():
    pk = _review_pack()
    sents = sentences(pk)
    ans = {"facts": [{"refs": ["E1.S1"], "claim": "EPS fell from 1.30 to 1.02"}], "interpretations": [],
           "uncertainties": [], "priced_in": "x", "reported": [], "p_up_pct": 50, "expected_net_return_bps": 0,
           "decision": "no_trade", "trade": None, "invalidation": [], "reasons_to_avoid": [], "rationale": "nothing here",
           "confidence": "low"}
    d, problems, _ = parse_ai_decision_v2(json.dumps(ans), pk, sents)
    assert problems == []                                                  # automatically valid
    rec = {"payload": {"decider": "llm", "event_id": "ZZZ:1", "decision": {
        "valid": True, "problems": [], "action": "no_trade", "p_up_by_close": 0.5,
        "thesis": {"facts": [{"refs": ["E1.S1"], "claim": ans["facts"][0]["claim"],
                              "evidence": {"E1.S1": sents["E1.S1"]}}], "reported_refs": []}}}}
    items = packet([rec], "seed")
    check_blind(items)
    res = score(items, [{"review_id": items[0]["review_id"], "claim_index": 0, "verdict": "unsupported",
                         "subject": True, "direction": False, "period": True, "basis": True, "sign": True, "unit": True}])
    assert res["verified_accurate"] == 0 and res["automatically_valid_but_unsupported"] == 1


def test_the_review_packet_is_blind_to_decisions_and_outcomes(dry):
    out, _ = dry
    from autotrader.ledger import Ledger
    items = packet(Ledger(out / "ledger.jsonl", "replay").of_type("decision"), "s")
    check_blind(items)
    with pytest.raises(ValueError):
        check_blind([{"claims": [], "trade": {}}])


@pytest.mark.parametrize("hhmm", ["11:00", "15:45"])
def test_sector_etf_trades_use_the_same_exit_conventions(hhmm):
    q = _quotes()
    for w in ("entry", "exit"):
        q[f"{DAY.isoformat()}|{w}"]["XLK"] = dict(q[f"{DAY.isoformat()}|{w}"]["AAA"])
    d = _dec(exit_time=hhmm)
    d["payload"]["decision"]["instrument"] = "XLK"
    o = _exec(Prices(_bars(), q)).execute_day(DAY, [(d, _pack(), execution.RULE_CLOCK)])[0]
    assert o.status == "filled" and o.outcome["symbol"] == "XLK" and o.outcome["scored"] and o.outcome["reconciles"]


def test_financial_history_uses_only_facts_filed_before_the_decision_date():
    from autotrader.sources import xbrl_history

    def edgar(restated_on):
        class E:
            def get(self, url):
                rows = [{"start": "2025-01-01", "end": "2025-03-31", "val": 1.0, "form": "10-Q", "filed": "2025-05-01"},
                        {"start": "2025-01-01", "end": "2025-03-31", "val": 9.9, "form": "10-Q/A", "filed": restated_on},
                        {"start": "2025-04-01", "end": "2025-06-30", "val": 2.0, "form": "10-Q", "filed": "2025-07-21"}]
                return json.dumps({"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": rows}}}}})
        return E()
    hist, last = xbrl_history(edgar("2025-07-25"), 1, date(2025, 7, 21))
    assert [h["eps"] for h in hist] == [1.0] and last == date(2025, 5, 1)     # later restatement and quarter excluded
    hist2, _ = xbrl_history(edgar("2025-07-20"), 1, date(2025, 7, 21))
    assert [h["eps"] for h in hist2] == [9.9]                                 # a restatement already public is used
