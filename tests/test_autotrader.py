"""Phase 5 autonomous-trader prototype: data integrity, model-response validity, timestamps, duplicate
decisions, risk rejection, safe failure, the simulator, and one complete observation -> outcome pass."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from autotrader.decision import parse_ai_decision
from autotrader.deciders import LLMResearcher, ModelFailure, NoTrade, PriceOnly, RecordedProvider, RuleP4H1, SYSTEM
from autotrader.evidence import Evidence, EvidencePack, LookAheadError
from autotrader.ledger import DuplicateDecision, Ledger, LedgerError
from autotrader.policy import OwnerPolicy, PolicyError, refuse_exposed_key
from autotrader.report import daily_report
from autotrader.risk import Health, Quote, RiskController, SimPortfolio, SimPosition
from autotrader.runner import Autonomy
from autotrader.simulate import daily_fees, simulate_exit
from dkapp.clock import ET

DAY = date(2026, 3, 10)                                         # a Tuesday
T10 = datetime(2026, 3, 10, 10, 0, tzinfo=ET)
ENTRY = T10 + timedelta(seconds=30)
RELEASE = ("Acme Corp reports first quarter results. Revenue of $4,527 million increased 12%. "
           "GAAP diluted EPS of $1.30 compared with $1.02 a year ago. The company raised its full-year outlook.")
UNIVERSE = {"ABC", "XLK", "SPY"}


def pack(release=RELEASE, t=T10, extra=None, structured=None) -> EvidencePack:
    items = [Evidence("E1", "filing", "SEC EDGAR 8-K 0000000000-26-000001, EX-99.1", t - timedelta(hours=3), release),
             Evidence("E2", "price", "15-minute bars", t, "ABC prior close $50.00. Move from the prior close to 10:00: +1.20%.")]
    return EvidencePack("ABC:0000000000-26-000001", "ABC", "XLK", t, items + (extra or []),
                        structured=structured if structured is not None else
                        {"surprise": {"eps": "beat", "sales": "beat"}, "react": 0.004})


def answer(**over) -> dict:
    a = {"facts": [{"evidence_id": "E1", "quote": "GAAP diluted EPS of $1.30 compared with $1.02 a year ago",
                    "claim": "EPS rose from 1.02 to 1.30"},
                   {"evidence_id": "E1", "quote": "raised its full-year outlook", "claim": "guidance raised"}],
         "interpretations": ["earnings momentum"], "uncertainties": ["guidance details unknown"],
         "priced_in": "a 1.2% move already reflects part of it",
         "reported": {"eps_diluted": 1.30, "revenue_usd_millions": 4527},
         "p_up_by_close": 0.56, "expected_net_return_pct": 0.1, "decision": "long", "instrument": "ABC",
         "entry": {"max_spread_bps": 30, "min_move_pct": 0.0, "max_move_pct": 4.0},
         "exit": {"stop_pct": 2.0, "target_pct": None, "exit_time": "15:45"},
         "invalidation": ["falls below the prior close"], "reasons_to_avoid": ["costs exceed the typical drift"],
         "rationale": "raised outlook not fully priced in", "confidence": "low"}
    a.update(over)
    return a


def quote(sym="ABC", t=ENTRY, mid=50.6, spread_bps=10.0, prev=50.0) -> Quote:
    h = mid * spread_bps / 2 / 1e4
    return Quote(sym, mid - h, mid + h, t, prev)


@pytest.fixture
def policy():
    return OwnerPolicy.load()


@pytest.fixture
def ctl(policy, tmp_path):
    return RiskController(policy, UNIVERSE, tmp_path)


def long_decision(**over):
    d, probs = parse_ai_decision(json.dumps(answer(**over)), pack())
    assert d is not None and not probs, probs
    return {"decider": "llm", "action": "long", "instrument": d.instrument, "entry": d.entry.model_dump(),
            "exit": d.exit.model_dump(), "valid": True}


# ------------------------------------------------------------------ data integrity
def test_evidence_after_the_decision_time_is_refused():
    late = Evidence("E3", "filing", "8-K/A", T10 + timedelta(minutes=1), "later news")
    with pytest.raises(LookAheadError):
        pack(extra=[late])


def test_naive_times_and_duplicate_ids_are_refused():
    with pytest.raises(ValueError):
        EvidencePack("x", "ABC", "XLK", datetime(2026, 3, 10, 10), [])
    dup = Evidence("E1", "filing", "x", T10, "y")
    with pytest.raises(ValueError):
        pack(extra=[dup])


def test_pack_hash_changes_when_any_evidence_changes():
    assert pack().sha256 == pack().sha256
    assert pack().sha256 != pack(release=RELEASE + " ").sha256


def test_vendor_text_is_redacted_in_records_but_hashed():
    v = Evidence("E3", "vendor_headline", "vendor", T10, "Acme beats estimates", redistributable=False)
    rec = pack(extra=[v]).record(redact=True)["items"][2]
    assert "Acme" not in rec["text"] and rec["sha256"] == v.sha256


def test_ledger_detects_edits_deletions_and_reordering(tmp_path):
    p = tmp_path / "l.jsonl"
    led = Ledger(p, "replay")
    for i in range(3):
        led.append("note", {"i": i})
    assert led.verify()[0]
    lines = p.read_text().splitlines()
    p.write_text("\n".join(lines[:2] + [lines[2].replace('"i": 1', '"i": 9')] + lines[3:]) + "\n")
    with pytest.raises(LedgerError):
        Ledger(p, "replay")
    p.write_text("\n".join([lines[0], lines[2], lines[3]]) + "\n")       # a record deleted
    with pytest.raises(LedgerError):
        Ledger(p, "replay")
    p.write_text("\n".join([lines[0], lines[2], lines[1], lines[3]]) + "\n")
    with pytest.raises(LedgerError):
        Ledger(p, "replay")


def test_a_replay_ledger_can_never_be_reopened_as_forward(tmp_path):
    Ledger(tmp_path / "l.jsonl", "replay")
    with pytest.raises(LedgerError):
        Ledger(tmp_path / "l.jsonl", "forward")


# ------------------------------------------------------------------ model-response validity
def test_a_grounded_answer_is_accepted():
    d, probs = parse_ai_decision(json.dumps(answer()), pack())
    assert d is not None and probs == []


@pytest.mark.parametrize("text,needle", [
    ("I think you should buy ABC.", "not JSON"),
    (json.dumps(answer() | {"position_usd": 1000}), "schema"),                     # the AI cannot size or widen risk
    (json.dumps(answer(p_up_by_close=1.4)), "schema"),
    (json.dumps(answer(decision="short")), "schema"),                               # no shorting
    (json.dumps(answer(facts=[])), "schema"),                                       # must ground something
])
def test_invalid_answers_are_rejected(text, needle):
    d, probs = parse_ai_decision(text, pack())
    assert probs and any(needle in p for p in probs)


@pytest.mark.parametrize("over,needle", [
    ({"facts": [{"evidence_id": "E1", "quote": "EPS of $1.45 beat estimates", "claim": "beat"}]}, "quote not found"),
    ({"facts": [{"evidence_id": "E1", "quote": "raised its full-year outlook", "claim": "raised to 9% growth"}]},
     "numbers not in its quote"),
    ({"facts": [{"evidence_id": "E7", "quote": "raised its full-year outlook", "claim": "raised"}]}, "unknown evidence"),
    ({"reported": {"eps_diluted": 1.28, "revenue_usd_millions": None}}, "eps_diluted=1.28 does not appear"),
    ({"reported": {"eps_diluted": None, "revenue_usd_millions": 1200}}, "revenue_usd_millions=1200 does not appear"),
    ({"instrument": "TSLA"}, "not the event's stock"),
    ({"reasons_to_avoid": []}, "at least one reason to avoid"),
    ({"invalidation": []}, "invalidate"),
])
def test_ungrounded_or_out_of_scope_answers_are_rejected(over, needle):
    d, probs = parse_ai_decision(json.dumps(answer(**over)), pack())
    assert any(needle in p for p in probs), probs


def test_a_no_trade_answer_may_leave_entry_and_exit_at_zero():
    a = answer(decision="no_trade", entry={"max_spread_bps": 0, "min_move_pct": None, "max_move_pct": None},
               exit={"stop_pct": 0, "target_pct": 0, "exit_time": "15:45"}, reasons_to_avoid=[], invalidation=[])
    d, probs = parse_ai_decision(json.dumps(a), pack())
    assert d is not None and probs == [] and d.exit.target_pct is None


def test_a_trade_without_a_stop_is_invalid():
    d, probs = parse_ai_decision(json.dumps(answer(exit={"stop_pct": 0, "target_pct": None, "exit_time": "15:45"})), pack())
    assert d is None and "stop" in probs[0]


def test_llm_researcher_turns_an_invalid_answer_into_no_trade():
    p = pack()
    user = p.prompt_text() + "\n\nInvestigate this event and decide now. JSON only."
    import hashlib
    key = hashlib.sha256((SYSTEM + user).encode()).hexdigest()
    bad = json.dumps(answer(reported={"eps_diluted": 9.99, "revenue_usd_millions": None}))
    d = LLMResearcher(RecordedProvider({key: bad})).decide(p)
    assert d["action"] == "no_trade" and d["valid"] is False and d["problems"]
    good = LLMResearcher(RecordedProvider({key: json.dumps(answer())})).decide(p)
    assert good["action"] == "long" and good["valid"] and good["thesis"]["facts"]


# ------------------------------------------------------------------ timestamps and duplicates
def test_outcomes_cannot_use_prices_from_before_the_decision(tmp_path):
    led = Ledger(tmp_path / "l.jsonl", "replay")
    rec = led.record_decision("ev", "llm", "h", T10, {"action": "long"})
    with pytest.raises(LedgerError):
        led.record_outcome(rec["seq"], {}, data_from=T10)
    led.record_outcome(rec["seq"], {}, data_from=ENTRY)
    with pytest.raises(LedgerError):
        led.record_outcome(rec["seq"], {}, data_from=ENTRY)                         # one outcome per decision


def test_forward_outcomes_must_use_prices_that_did_not_exist_when_the_decision_was_written(tmp_path):
    led = Ledger(tmp_path / "f.jsonl", "forward")
    written = datetime(2026, 3, 10, 12, 0, tzinfo=ET)
    rec = led.record_decision("ev", "llm", "h", T10, {"action": "long"}, now=written)
    with pytest.raises(LedgerError):
        led.record_outcome(rec["seq"], {}, data_from=ENTRY)                         # prices already existed
    led.record_outcome(rec["seq"], {}, data_from=written + timedelta(minutes=1))
    with pytest.raises(LedgerError):
        led.record_decision("ev2", "llm", "h", written + timedelta(days=1), {}, now=written)   # dated in the future


def test_a_second_decision_on_the_same_event_is_refused_even_after_reopening(tmp_path):
    p = tmp_path / "l.jsonl"
    Ledger(p, "replay").record_decision("ev", "llm", "h", T10, {"action": "no_trade"})
    led = Ledger(p, "replay")
    with pytest.raises(DuplicateDecision):
        led.record_decision("ev", "llm", "h", T10, {"action": "long"})
    led.record_decision("ev", "price_only", "h", T10, {"action": "long"})          # another decider is fine


# ------------------------------------------------------------------ risk controller
def test_policy_limits_are_the_apps_and_only_simulation_is_permitted(policy, tmp_path):
    r = policy.risk_settings().risk
    assert (r.starting_capital, r.max_position_usd, r.max_positions, r.daily_loss_limit_usd) == (100.0, 20.0, 2, 2.0)
    assert policy.mode == "simulate"
    bad = tmp_path / "p.json"
    for change in ({"mode": "live"}, {"mode": "paper"}, {"long_only": False}):
        d = json.loads(Path("autotrader/owner_policy.json").read_text()) | change
        bad.write_text(json.dumps(d))
        with pytest.raises(PolicyError):
            OwnerPolicy.load(bad)


def test_exposed_and_live_keys_are_refused():
    with pytest.raises(PolicyError):
        refuse_exposed_key("PKJB7UQXXXXXXXXXXXXXXXXXXX")
    with pytest.raises(PolicyError):
        refuse_exposed_key("AKABCDEF")
    refuse_exposed_key("PKNEWROTATEDKEY")


def test_an_approved_trade_is_sized_by_the_controller_not_the_ai(ctl):
    d = long_decision() | {"notional": 1000, "qty": 500}
    v = ctl.check(d, pack(), quote(), SimPortfolio(), Health(), ENTRY)
    assert v["approved"], v["reasons"]
    assert 0 < v["notional"] <= 20.0


@pytest.mark.parametrize("change,needle", [
    (lambda d, q, pf, h: (d | {"instrument": "TSLA"}, q, pf, h), "not the event's stock"),
    (lambda d, q, pf, h: (d | {"instrument": "SPXW260320C"}, q, pf, h), "not"),
    (lambda d, q, pf, h: (d | {"valid": False}, q, pf, h), "failed validation"),
    (lambda d, q, pf, h: (d | {"exit": d["exit"] | {"stop_pct": 5.0}}, q, pf, h), "max 3.0%"),
    (lambda d, q, pf, h: (d | {"exit": d["exit"] | {"exit_time": "16:00"}}, q, pf, h), "after the owner's limit"),
    (lambda d, q, pf, h: (d | {"exit": d["exit"] | {"stop_pct": None}}, q, pf, h), "no stop"),
    (lambda d, q, pf, h: (d, None, pf, h), "no quote"),
    (lambda d, q, pf, h: (d, quote(t=ENTRY - timedelta(minutes=5)), pf, h), "old"),
    (lambda d, q, pf, h: (d, quote(spread_bps=60), pf, h), "spread"),
    (lambda d, q, pf, h: (d, quote(mid=55.0), pf, h), "entry condition: move"),
    (lambda d, q, pf, h: (d, q, SimPortfolio(open=[SimPosition("X1", 1, 10, 9.7, T10), SimPosition("X2", 1, 10, 9.7, T10)]), h),
     "2 of 2"),
    (lambda d, q, pf, h: (d, q, SimPortfolio(day=DAY, realized_today=-2.0), h), "daily loss limit"),
    (lambda d, q, pf, h: (d, q, pf, Health(data_failures=3)), "safe state"),
    (lambda d, q, pf, h: (d, q, pf, Health(ledger_ok=False)), "ledger"),
])
def test_out_of_bounds_proposals_are_rejected_automatically(ctl, change, needle):
    d, q, pf, h = change(long_decision(), quote(), SimPortfolio(), Health())
    v = ctl.check(d, pack(), q, pf, h, ENTRY)
    assert not v["approved"] and any(needle in r for r in v["reasons"]), v["reasons"]


def test_kill_switch_rejects_everything(ctl, tmp_path):
    (tmp_path / "KILL").write_text("stop")
    v = ctl.check(long_decision(), pack(), quote(), SimPortfolio(), Health(), ENTRY)
    assert not v["approved"] and "kill switch" in v["reasons"][0]


def test_trades_outside_market_hours_are_rejected(ctl):
    t = datetime(2026, 3, 10, 9, 31, tzinfo=ET)
    v = ctl.check(long_decision(), pack(t=t - timedelta(minutes=1)), quote(t=t), SimPortfolio(), Health(), t)
    assert not v["approved"]


def test_an_internal_controller_error_rejects(ctl):
    v = ctl.check({"action": "long", "valid": True, "instrument": "ABC", "exit": "garbage"}, pack(), quote(),
                  SimPortfolio(), Health(), ENTRY)
    assert not v["approved"] and v.get("error")


# ------------------------------------------------------------------ simulator
def _bars(lows, highs=None, opens=None):
    idx = pd.date_range(datetime(2026, 3, 10, 9, 30, tzinfo=ET), periods=len(lows), freq="15min")
    highs = highs or [100.5] * len(lows)
    opens = opens or [100.0] * len(lows)
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": [100.0] * len(lows)}, index=idx)


def test_the_bar_straddling_entry_counts_a_stop_pessimistically_and_never_a_target():
    exit_t = datetime(2026, 3, 10, 15, 45, tzinfo=ET)
    lows = [99.5] * 26
    lows[2] = 96.0                                                    # the 10:00 bar reaches the 97 stop
    r = simulate_exit(ENTRY, 100.0, 5.0, 97.0, None, exit_t, _bars(lows), None)
    assert r["reason"] == "stop" and r["flags"]
    highs = [100.5] * 26
    highs[2] = 105.0                                                  # target only in the straddling bar
    r = simulate_exit(ENTRY, 100.0, 5.0, 97.0, 104.0, exit_t, _bars([99.5] * 26, highs), quote(t=exit_t, mid=100.2))
    assert r["reason"] == "time"


def test_stop_and_target_in_the_same_bar_assumes_the_stop():
    lows, highs = [99.5] * 26, [100.5] * 26
    lows[4], highs[4] = 96.0, 105.0
    r = simulate_exit(ENTRY, 100.0, 5.0, 97.0, 104.0, datetime(2026, 3, 10, 15, 45, tzinfo=ET), _bars(lows, highs), None)
    assert r["reason"] == "stop"


def test_fees_are_rounded_once_per_day():
    t = {"fee_parts": {"sec": 0.0004, "taf": 0.0003, "cat": 0.00001}}
    assert sum(daily_fees([t, t, t])) == pytest.approx(0.03)


# ------------------------------------------------------------------ the autonomous loop
class FakePrices:
    def __init__(self, entry_mid=50.6, exit_mid=51.0):
        self.e, self.x = entry_mid, exit_mid

    def quote(self, s, t):
        if t.time() == ENTRY.time():
            return quote(s, t, self.e)
        if t.hour == 15 and t.minute == 45:
            return quote(s, t, self.x)
        return None

    def bars15(self, s, d):
        return _bars([50.4] * 26, [51.2] * 26, [50.5] * 26)


class Boom:
    name, model = "broken", "x"

    def complete_json(self, *a, **k):
        raise ConnectionError("model server down")


def _auto(tmp_path, deciders, prices=None):
    pol = OwnerPolicy.load()
    led = Ledger(tmp_path / "ledger.jsonl", "replay")
    return Autonomy(led, pol, deciders, RiskController(pol, UNIVERSE, tmp_path), prices or FakePrices()), led


def test_full_pass_observation_to_recorded_simulated_result(tmp_path):
    p = pack()
    import hashlib
    user = p.prompt_text() + "\n\nInvestigate this event and decide now. JSON only."
    key = hashlib.sha256((SYSTEM + user).encode()).hexdigest()
    auto, led = _auto(tmp_path, [NoTrade(), RuleP4H1(), PriceOnly(),
                                 LLMResearcher(RecordedProvider({key: json.dumps(answer())}))])
    res = auto.run_session(DAY, [1], lambda _: p)
    assert {r["decider"] for r in res} == {"rule_p4h1", "price_only", "llm"}
    llm = next(r for r in res if r["decider"] == "llm")
    assert llm["reason"] == "time" and llm["notional"] <= 20 and "net_pct" in llm
    assert led.verify()[0]
    kinds = [r["type"] for r in led.records]
    assert kinds.index("evidence") < kinds.index("decision") < kinds.index("risk") < kinds.index("outcome")
    rep = daily_report(led)
    assert "llm" in rep and "verified" in rep and "Safety events" in rep


def test_model_failures_give_no_trade_then_switch_the_model_off(tmp_path):
    auto, led = _auto(tmp_path, [LLMResearcher(Boom())])
    packs = [pack(), ]
    rows = list(range(5))
    auto.run_session(DAY, rows, lambda i: EvidencePack(f"ABC:{i}", "ABC", "XLK", T10, packs[0].items, packs[0].structured))
    decs = [r["payload"]["decision"] for r in led.of_type("decision")]
    assert all(d["action"] == "no_trade" for d in decs)
    assert sum(d.get("failure", False) for d in decs) == 3                          # then switched off
    assert sum("switched off" in d["rationale"] for d in decs) == 2
    kinds = [s["payload"]["kind"] for s in led.of_type("safety_event")]
    assert kinds.count("model") == 3 and "safe_state" in kinds
    assert not led.of_type("outcome")


def test_repeated_data_failures_stop_new_decisions(tmp_path):
    auto, led = _auto(tmp_path, [PriceOnly()])

    def build(i):
        if i < 3:
            raise TimeoutError("EDGAR timed out")
        return pack()
    auto.run_session(DAY, [0, 1, 2, 3, 4], build)
    assert not led.of_type("decision")
    kinds = [s["payload"]["kind"] for s in led.of_type("safety_event")]
    assert kinds.count("data") == 3 and "safe_state" in kinds


def test_the_kill_switch_stops_the_loop(tmp_path):
    auto, led = _auto(tmp_path, [PriceOnly()])
    (tmp_path / "KILL").write_text("")
    auto.run_session(DAY, [0], lambda i: pack())
    assert not led.of_type("decision") and led.of_type("safety_event")[0]["payload"]["kind"] == "kill_switch"


def test_rerunning_a_session_cannot_re_decide(tmp_path):
    auto, led = _auto(tmp_path, [PriceOnly()])
    auto.run_session(DAY, [0], lambda i: pack())
    auto2 = Autonomy(Ledger(tmp_path / "ledger.jsonl", "replay"), auto.policy, [PriceOnly()], auto.controller, FakePrices())
    with pytest.raises(DuplicateDecision):
        auto2.run_session(DAY, [0], lambda i: pack())


def test_if_the_ledger_cannot_be_written_the_run_stops(tmp_path):
    auto, led = _auto(tmp_path, [PriceOnly()])
    led.path = tmp_path / "missing-dir" / "ledger.jsonl"                             # writes now fail
    with pytest.raises(OSError):
        auto.run_session(DAY, [0], lambda i: pack())


def test_forward_daemon_backs_off_on_errors_and_stops_on_the_kill_switch(tmp_path):
    pol = OwnerPolicy.load()
    led = Ledger(tmp_path / "f.jsonl", "forward")
    auto = Autonomy(led, pol, [NoTrade()], RiskController(pol, UNIVERSE, tmp_path))

    class Obs:
        calls = 0

        def new_filings(self, seen):
            Obs.calls += 1
            if Obs.calls == 1:
                raise ConnectionError("feed down")
            (tmp_path / "KILL").write_text("")
            return []
    sleeps = []
    auto.run_forever(Obs(), poll_seconds=60, max_cycles=5, sleep=sleeps.append)
    assert Obs.calls == 2 and sleeps == [60, 60]
    kinds = [s["payload"]["kind"] for s in led.of_type("safety_event")]
    assert "data" in kinds and "kill_switch" in kinds


def test_forward_cycle_decides_only_fresh_filings_before_their_session(tmp_path):
    pol = OwnerPolicy.load()
    led = Ledger(tmp_path / "f.jsonl", "forward")
    auto = Autonomy(led, pol, [NoTrade()], RiskController(pol, UNIVERSE, tmp_path))
    fri_am = datetime(2026, 11, 6, 7, 0, tzinfo=ET)                  # already traded on Friday
    fri_pm = datetime(2026, 11, 6, 16, 5, tzinfo=ET)                 # first tradable on Monday
    now = datetime(2026, 11, 7, 8, 0, tzinfo=ET)                     # Saturday, before the real clock

    class Obs:
        def new_filings(self, seen):
            return [{"accession": "A-1", "updated": fri_am, "symbol": "ABC"},
                    {"accession": "A-2", "updated": fri_pm, "symbol": "ABC"}]

        def pack(self, f, t):
            return EvidencePack(f"ABC:{f['accession']}", "ABC", "XLK", t,
                                [Evidence("E1", "filing", "8-K", f["updated"], RELEASE)])
    assert auto.forward_cycle(Obs(), now=now) == 1
    dec = led.of_type("decision")
    assert [d["payload"]["event_id"] for d in dec] == ["ABC:A-2"]
    assert dec[0]["payload"]["decision"]["session"] == "2026-11-09"
    assert led.of_type("heartbeat")[-1]["payload"]["skipped_already_traded"] == ["A-1"]
    assert auto.forward_cycle(Obs(), now=now) == 0                     # seen filings are never re-decided
