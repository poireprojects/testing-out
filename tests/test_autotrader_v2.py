"""Phase 5, evaluation protocol v2 and output contract v2:
* predictions are scored on the decision's recorded target session (weekend, after-hours, pre-open cases);
* unattended forward scoring: as-of baselines from data that ended by 10:00, market outcomes, execution of the
  recorded decisions, idempotence, retry when prices fail, and no way for the AI to see later prices;
* contract v2: facts cite numbered sentences, reported figures cite the release with a unit, trade fields are
  fixed choices; verification is no weaker than v1."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from autotrader.decision import json_schema_v2, parse_ai_decision_v2, prompt_text_v2, sentences
from autotrader.deciders import SYSTEM, SYSTEM_V2, LLMResearcher, LLMResearcherV2, NoTrade, PriceOnly, RecordedProvider
from autotrader.evaluate import evaluate, target_session
from autotrader.evidence import Evidence, EvidencePack
from autotrader.ledger import Ledger, LedgerError
from autotrader.policy import OwnerPolicy
from autotrader.risk import Health, Quote, RiskController, SimPortfolio
from autotrader.runner import Autonomy
from autotrader.scoring import ForwardScorer
from dkapp.clock import ET

FRI_PM = datetime(2026, 11, 6, 16, 5, tzinfo=ET)          # after the close on Friday
SAT_AM = datetime(2026, 11, 7, 8, 0, tzinfo=ET)           # Saturday morning
MON = date(2026, 11, 9)
MON_PRE = datetime(2026, 11, 9, 8, 0, tzinfo=ET)          # Monday before the open
UNIVERSE = {"ABC", "XLK", "SPY"}
RELEASE = ("Acme Corp reports third quarter results. Revenue of $4,527 million increased 12%. "
           "GAAP diluted EPS of $1.30 compared with $1.02 a year ago. The company raised its full-year outlook.")
HISTORY = "Quarter ended 2026-06-30: diluted EPS 1.10; revenue $4,300 million (form 10-Q)."


def _pack(t=SAT_AM, accepted=FRI_PM, extra=None):
    items = [Evidence("E1", "filing", "SEC EDGAR 8-K 0000000000-26-000001, EX-99.1", accepted, RELEASE),
             Evidence("E2", "financials", "SEC XBRL company facts", accepted - timedelta(days=60), HISTORY)]
    return EvidencePack("ABC:A-1", "ABC", "XLK", t, items + (extra or []))


class DayPrices:
    """Quotes at 10:00:30 and 15:45, 15-minute bars and the prior close, only for the given sessions."""

    def __init__(self, days=(MON,), entry=50.6, exit_=51.0, prev=50.0, closes=None, fail=False):
        self.days, self.e, self.x, self.prev, self.fail = set(days), entry, exit_, prev, fail
        self.closes = closes or {}
        self.calls = 0

    def _ok(self, d):
        self.calls += 1
        if self.fail:
            raise ConnectionError("market data unavailable")
        return d in self.days

    def quote(self, s, t):
        if not self._ok(t.date()):
            return None
        mid = {(10, 0): self.e, (15, 45): self.x}.get((t.hour, t.minute))
        if mid is None:
            return None
        h = mid * 5 / 1e4
        return Quote(s, mid - h, mid + h, t, self.prev)

    def prev_close(self, s, d):
        return self.prev if self._ok(d) else None

    def bars15(self, s, d):
        if not self._ok(d):
            return pd.DataFrame()
        idx = pd.date_range(datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET), periods=26, freq="15min")
        close = [self.closes.get((s, i), self.prev * 1.01 if s == "ABC" else self.prev) for i in range(26)]
        return pd.DataFrame({"open": close, "high": [c * 1.002 for c in close], "low": [c * 0.998 for c in close],
                             "close": close, "volume": [1e5] * 26}, index=idx)


def _v1_answer(**over):
    a = {"facts": [{"evidence_id": "E1", "quote": "GAAP diluted EPS of $1.30 compared with $1.02 a year ago",
                    "claim": "EPS rose from 1.02 to 1.30"}],
         "interpretations": [], "uncertainties": [], "priced_in": "partly",
         "reported": {"eps_diluted": 1.30, "revenue_usd_millions": 4527}, "p_up_by_close": 0.7,
         "expected_net_return_pct": 0.1, "decision": "long", "instrument": "ABC",
         "entry": {"max_spread_bps": 30, "min_move_pct": 0.0, "max_move_pct": 4.0},
         "exit": {"stop_pct": 2.0, "target_pct": None, "exit_time": "15:45"},
         "invalidation": ["below the prior close"], "reasons_to_avoid": ["costs"], "rationale": "raised outlook",
         "confidence": "low"}
    a.update(over)
    return a


class Counting(RecordedProvider):
    calls = 0

    def complete_json(self, *a, **k):
        Counting.calls += 1
        return super().complete_json(*a, **k)


def _forward(tmp_path, answer=None, written=SAT_AM):
    """A forward ledger whose LLM decision was written on Saturday for Monday's session."""
    pol = OwnerPolicy.load()
    p = _pack(t=written)
    user = p.prompt_text() + "\n\nInvestigate this event and decide now. JSON only."
    key = hashlib.sha256((SYSTEM + user).encode()).hexdigest()
    Counting.calls = 0
    prov = Counting({key: json.dumps(answer or _v1_answer())})
    led = Ledger(tmp_path / "f.jsonl", "forward")
    auto = Autonomy(led, pol, [NoTrade(), LLMResearcher(prov)], RiskController(pol, UNIVERSE, tmp_path),
                    clock=lambda: written.astimezone(timezone.utc))

    class Obs:
        def new_filings(self, seen):
            return [] if "A-1" in seen else [{"accession": "A-1", "updated": FRI_PM, "symbol": "ABC"}]

        def pack(self, f, t):
            return _pack(t=t)
    assert auto.forward_cycle(Obs(), now=written) == 1
    return auto, led


# ------------------------------------------------------------------ 1. target session
@pytest.mark.parametrize("written,accepted,expected", [
    (SAT_AM, FRI_PM, MON),                                                       # weekend
    (datetime(2026, 11, 6, 18, 0, tzinfo=ET), FRI_PM, MON),                      # after hours on Friday
    (MON_PRE, datetime(2026, 11, 9, 7, 0, tzinfo=ET), MON),                      # before the open, same day
    (datetime(2026, 11, 26, 9, 0, tzinfo=ET), datetime(2026, 11, 25, 17, 0, tzinfo=ET), date(2026, 11, 27)),  # holiday
])
def test_forward_decisions_record_and_are_scored_on_their_target_session(tmp_path, written, accepted, expected):
    pol = OwnerPolicy.load()
    led = Ledger(tmp_path / "f.jsonl", "forward")
    auto = Autonomy(led, pol, [NoTrade()], RiskController(pol, UNIVERSE, tmp_path),
                    clock=lambda: written.astimezone(timezone.utc))

    class Obs:
        def new_filings(self, seen):
            return [{"accession": "A-1", "updated": accepted, "symbol": "ABC"}]

        def pack(self, f, t):
            return _pack(t=t, accepted=accepted)
    auto.forward_cycle(Obs(), now=written)
    rec = led.of_type("decision")[0]
    assert target_session(rec, "forward") == expected
    assert target_session(rec, "forward") != written.date() or written.date() == expected


def test_q2_scores_a_saturday_decision_on_monday_not_on_saturday(tmp_path):
    auto, led = _forward(tmp_path)
    prices = DayPrices(days={MON})
    assert prices.quote("ABC", datetime(2026, 11, 7, 10, 0, 30, tzinfo=ET)) is None   # v1 would have found nothing
    q2 = evaluate(led, prices=prices)["Q2_prediction"]
    assert q2["n"] == 1 and q2["upRate"] == 1.0 and q2["noTargetSession"] == 0


def test_q2_prefers_the_ledgers_market_outcome_for_the_target_session(tmp_path):
    auto, led = _forward(tmp_path)
    ForwardScorer(auto, DayPrices(days={MON}, entry=50.6, exit_=50.0)).run(now=datetime(2026, 11, 9, 17, 0, tzinfo=ET))
    q2 = evaluate(led)["Q2_prediction"]                                            # no prices needed any more
    assert q2["n"] == 1 and q2["upRate"] == 0.0


def test_a_forward_decision_without_a_target_session_is_not_scored(tmp_path):
    led = Ledger(tmp_path / "f.jsonl", "forward")
    led.record_decision("ABC:A-1", "llm", "h", SAT_AM, {"action": "no_trade", "p_up_by_close": 0.6},
                        now=SAT_AM.astimezone(timezone.utc))
    q2 = evaluate(led, prices=DayPrices(days={SAT_AM.date(), MON}))["Q2_prediction"]
    assert q2["n"] == 0 and q2["noTargetSession"] == 1


def test_replay_decisions_without_a_session_use_their_own_day():
    rec = {"payload": {"decision": {}, "decision_time": "2026-10-30T10:00:00-04:00"}}
    assert target_session(rec, "replay") == date(2026, 10, 30)
    assert target_session(rec, "forward") is None


# ------------------------------------------------------------------ 2. unattended forward scoring
def test_scoring_waits_until_the_session_is_over(tmp_path):
    auto, led = _forward(tmp_path)
    sc = ForwardScorer(auto, DayPrices())
    assert sc.pending(datetime(2026, 11, 9, 15, 0, tzinfo=ET)) == []
    assert sc.pending(datetime(2026, 11, 9, 16, 30, tzinfo=ET)) == [MON]


def test_a_full_session_is_scored_once_with_as_of_baselines(tmp_path):
    auto, led = _forward(tmp_path)
    before = json.dumps([r for r in led.records if r["type"] == "decision"], sort_keys=True)
    sc = ForwardScorer(auto, DayPrices())
    out = sc.run(now=datetime(2026, 11, 9, 17, 0, tzinfo=ET))
    assert out[0]["events"] == 1 and out[0]["eventsWithMarketOutcome"] == 1
    decs = {(r["payload"]["decider"], bool(r["payload"].get("asof"))) for r in led.of_type("decision")}
    assert decs == {("no_trade", False), ("llm", False), ("price_only", True), ("rule_p4h1", True)}
    asof = [r["payload"] for r in led.of_type("decision") if r["payload"].get("asof")]
    assert all(datetime.fromisoformat(i["ended"]) <= datetime(2026, 11, 9, 10, 0, tzinfo=ET)
               for p in asof for i in p["inputs"])
    rule = next(p for p in asof if p["decider"] == "rule_p4h1")
    assert rule["decision"]["action"] == "no_trade"                              # no consensus data in forward mode
    outs = {o["payload"]["decider"] for o in led.of_type("outcome")}
    assert outs == {"llm", "price_only"}
    after = json.dumps([r for r in led.records if r["type"] == "decision" and not r["payload"].get("asof")], sort_keys=True)
    assert before == after                                                         # recorded decisions untouched
    assert Counting.calls == 1                                                     # the model was never asked again
    n = len(led.records)
    assert sc.run(now=datetime(2026, 11, 10, 17, 0, tzinfo=ET)) == [] and len(led.records) == n   # idempotent
    assert led.verify()[0]


def test_as_of_baselines_ignore_prices_after_10_00(tmp_path):
    auto, led = _forward(tmp_path)
    crash = {("ABC", i): 30.0 for i in range(2, 26)}                              # collapse from the 10:00 bar on
    ForwardScorer(auto, DayPrices(closes=crash)).run(now=datetime(2026, 11, 9, 17, 0, tzinfo=ET))
    po = next(r["payload"] for r in led.of_type("decision") if r["payload"]["decider"] == "price_only")
    assert po["decision"]["action"] == "long"                                      # it only saw +1% by 10:00


def test_the_ledger_refuses_an_as_of_input_that_ended_after_the_as_of_time(tmp_path):
    led = Ledger(tmp_path / "f.jsonl", "forward")
    asof = datetime(2026, 11, 9, 10, 0, tzinfo=ET)
    with pytest.raises(LedgerError):
        led.record_asof_decision("ABC:A-1", "price_only", "h", asof, {"action": "long"},
                                 [{"source": "ABC 15-minute bars", "ended": "2026-11-09T10:15:00-05:00"}])


def test_the_ais_entry_conditions_are_checked_on_the_10_00_30_quote(tmp_path):
    auto, led = _forward(tmp_path)
    ForwardScorer(auto, DayPrices(entry=53.5)).run(now=datetime(2026, 11, 9, 17, 0, tzinfo=ET))   # +7% > 4% max
    risk = next(r["payload"] for r in led.of_type("risk") if r["payload"]["decider"] == "llm")
    assert not risk["approved"] and any("entry condition: move" in x for x in risk["reasons"])


def test_price_failures_leave_the_session_pending_then_unscorable(tmp_path):
    auto, led = _forward(tmp_path)
    bad = ForwardScorer(auto, DayPrices(fail=True))
    assert bad.run(now=datetime(2026, 11, 9, 17, 0, tzinfo=ET)) == []
    assert not led.of_type("market_outcome") and not led.of_type("outcome")       # nothing half-written
    assert led.of_type("safety_event")[-1]["payload"]["kind"] == "data"
    assert bad.pending(datetime(2026, 11, 10, 17, 0, tzinfo=ET)) == [MON]          # retried later
    good = ForwardScorer(auto, DayPrices())
    assert good.run(now=datetime(2026, 11, 10, 17, 0, tzinfo=ET))[0]["day"] == "2026-11-09"
    auto2, led2 = _forward(tmp_path / "b")
    ForwardScorer(auto2, DayPrices(fail=True), give_up_days=2).run(now=datetime(2026, 11, 13, 17, 0, tzinfo=ET))
    assert led2.of_type("session_unscorable")[0]["payload"]["day"] == "2026-11-09"


def test_the_scorer_accepts_no_model(tmp_path):
    auto, led = _forward(tmp_path)
    with pytest.raises(TypeError):
        ForwardScorer(auto, DayPrices(), asof_baselines=(LLMResearcher(RecordedProvider({})),))


def test_a_forward_outcome_cannot_use_prices_from_before_the_decision_was_written(tmp_path):
    late = datetime(2026, 11, 9, 11, 0, tzinfo=ET)                               # written after Monday's entry
    pol = OwnerPolicy.load()
    led = Ledger(tmp_path / "f.jsonl", "forward")
    led.record_decision("ABC:A-1", "llm", "h", SAT_AM, {"action": "long", "session": "2026-11-09"},
                        now=late.astimezone(timezone.utc))
    with pytest.raises(LedgerError):
        led.record_market_outcome("ABC:A-1", "2026-11-09", "ABC", 50.6, 51.0, datetime(2026, 11, 9, 10, 0, 30, tzinfo=ET))
    del pol


def test_daemon_scores_finished_sessions_unattended(tmp_path):
    auto, led = _forward(tmp_path)
    auto.clock = lambda: datetime(2026, 11, 9, 22, 0, tzinfo=timezone.utc)
    sc = ForwardScorer(auto, DayPrices())

    class Quiet:
        def new_filings(self, seen):
            return []
    auto.run_forever(Quiet(), poll_seconds=60, max_cycles=1, sleep=lambda s: None, scorer=sc)
    assert led.of_type("session_scored")


# ------------------------------------------------------------------ 3. contract v2
def _v2(**over):
    a = {"facts": [{"refs": ["E1.S3"], "claim": "EPS rose from 1.02 to 1.30"}],
         "interpretations": [], "uncertainties": [], "priced_in": "partly",
         "reported": [{"metric": "gaap_diluted_eps", "ref": "E1.S3", "value": 1.30, "unit": "usd_per_share"},
                      {"metric": "revenue", "ref": "E1.S2", "value": 4527, "unit": "usd_millions"}],
         "p_up_pct": 60, "expected_net_return_bps": 10, "decision": "long",
         "trade": {"instrument": "ABC", "max_spread_bps": 30, "min_move_pct": 0.0, "max_move_pct": 4.0,
                   "stop_pct": 2.0, "target_pct": None, "exit_time": "15:45"},
         "invalidation": ["below the prior close"], "reasons_to_avoid": ["costs"], "rationale": "raised outlook",
         "confidence": "low"}
    a.update(over)
    return json.dumps(a)


def test_v2_sentences_are_deterministic_and_cover_every_item():
    p = _pack()
    s = sentences(p)
    assert s == sentences(p) and s["E1.S3"].startswith("GAAP diluted EPS of $1.30")
    assert {k.split(".")[0] for k in s} == {"E1", "E2"}
    assert "[E1.S3] GAAP diluted EPS" in prompt_text_v2(p, s)
    sch = json_schema_v2(p, s)
    assert sch["properties"]["trade"]["anyOf"][1]["properties"]["instrument"]["enum"] == ["ABC", "XLK"]
    assert sch["properties"]["reported"]["items"]["properties"]["ref"]["enum"] == [k for k in s if k.startswith("E1.")]


def test_v2_grounded_answer_is_accepted():
    d, probs, _ = parse_ai_decision_v2(_v2(), _pack())
    assert d is not None and probs == []


@pytest.mark.parametrize("over,needle", [
    ({"facts": [{"refs": ["E9.S1"], "claim": "a fact"}]}, "unknown sentence"),
    ({"facts": [{"refs": ["E1.S2"], "claim": "EPS 1.30"}]}, "numbers not in its cited sentences"),     # wrong sentence
    ({"facts": [{"refs": ["E1.S3"], "claim": "EPS up 27.5%"}]}, "numbers not in its cited sentences"),  # derived number
    ({"reported": [{"metric": "gaap_diluted_eps", "ref": "E1.S3", "value": 1.29, "unit": "usd_per_share"}]}, "does not appear"),
    ({"reported": [{"metric": "revenue", "ref": "E2.S1", "value": 4300, "unit": "usd_millions"}]}, "not a sentence of the company's release"),
    ({"reported": [{"metric": "revenue", "ref": "E1.S2", "value": 4527, "unit": "usd_billions"}]}, "within 3x"),
    ({"reported": [{"metric": "gaap_diluted_eps", "ref": "E1.S3", "value": 1.30, "unit": "usd_millions"}]}, "usd_per_share"),
    ({"reasons_to_avoid": []}, "reason to avoid"),
])
def test_v2_verification_is_no_weaker_than_v1(over, needle):
    d, probs, _ = parse_ai_decision_v2(_v2(**over), _pack())
    assert any(needle in p for p in probs), probs


@pytest.mark.parametrize("over,needle", [
    ({"decision": "long", "trade": None}, "needs a trade"),
    ({"trade": {"instrument": "ABC", "max_spread_bps": 30, "min_move_pct": None, "max_move_pct": None,
                "stop_pct": 5.0, "target_pct": None, "exit_time": "15:45"}}, "stop_pct must be one of"),
    ({"trade": {"instrument": "ABC", "max_spread_bps": 30, "min_move_pct": None, "max_move_pct": None,
                "stop_pct": 2.0, "target_pct": None, "exit_time": "16:00"}}, "exit_time must be one of"),
    ({"p_up_pct": 140}, "less than or equal to 100"),
    ({"position_usd": 1000}, "Extra inputs"),
])
def test_v2_schema_rejects_out_of_contract_answers(over, needle):
    d, probs, _ = parse_ai_decision_v2(_v2(**over), _pack())
    assert d is None and needle in probs[0], probs


def test_v2_billions_are_accepted_when_written_so_and_normalised():
    rel = RELEASE.replace("$4,527 million", "$4.527 billion")
    p = EvidencePack("ABC:A-1", "ABC", "XLK", SAT_AM,
                     [Evidence("E1", "filing", "8-K", FRI_PM, rel), Evidence("E2", "financials", "xbrl", FRI_PM, HISTORY)])
    rep = [{"metric": "revenue", "ref": "E1.S2", "value": 4.527, "unit": "usd_billions"}]
    d, probs, _ = parse_ai_decision_v2(_v2(reported=rep), p)
    assert probs == []


def test_v2_no_trade_drops_trade_fields_and_researcher_output_is_usable(tmp_path):
    d, probs, _ = parse_ai_decision_v2(_v2(decision="no_trade"), _pack())
    assert d.trade is None and probs == []
    p = _pack(t=datetime(2026, 11, 9, 10, 0, tzinfo=ET), accepted=datetime(2026, 11, 9, 7, 0, tzinfo=ET))
    s = sentences(p)
    user = prompt_text_v2(p, s) + "\n\nInvestigate this event and decide now. JSON only."
    key = hashlib.sha256((SYSTEM_V2 + user).encode()).hexdigest()
    out = LLMResearcherV2(RecordedProvider({key: _v2()})).decide(p)
    assert out["action"] == "long" and out["valid"] and out["p_up_by_close"] == 0.6
    assert out["thesis"]["facts"][0]["evidence"]["E1.S3"].startswith("GAAP diluted EPS")      # system-inserted text
    assert out["thesis"]["reported"] == {"eps_diluted": 1.30, "revenue_usd_millions": 4527}
    v = RiskController(OwnerPolicy.load(), UNIVERSE, tmp_path).check(
        out, p, Quote("ABC", 50.58, 50.62, datetime(2026, 11, 9, 10, 0, 30, tzinfo=ET), 50.0), SimPortfolio(), Health(),
        datetime(2026, 11, 9, 10, 0, 30, tzinfo=ET))
    assert v["approved"], v["reasons"]
