"""The paid path of the development evaluation, tested with the real anthropic 2.7.0 SDK and an in-memory HTTP
transport (no network, no working key): the pinned request, the journal's one-generation-per-event rule, holds,
crashes at every step, concurrent runners, changed paths, corrupt journals and the budget proof."""
from __future__ import annotations

import json
import multiprocessing as mp
import sqlite3

import httpx2
import pytest

from autotrader.experiment import FROZEN
from autotrader.journal import (BudgetRefused, DamagedJournal, Journal, JournalError, JournalLocked, open_journal)
from autotrader.paid import (CriticalStop, PaidCaller, PaidPathRefused, budget_proof, build_client, check_usage,
                             guard_environment, render_request, request_params)

anthropic = pytest.importorskip("anthropic")
pytestmark = pytest.mark.skipif(anthropic.__version__ != FROZEN["sdk"]["version"],
                                reason="the paid path is pinned to anthropic 2.7.0")

ID = "e" * 64
IDENT = {"test": "identity"}
EVENTS = [f"S{i}:000{i}" for i in range(4)]
SCHEMA = {"type": "object", "properties": {"a": {"type": "string", "maxLength": 9}}, "required": ["a"]}
BOUND_TOKENS = 4500                      # the plan's per-call assumption, used ONLY to exercise the mechanics here
PER_CALL = BOUND_TOKENS * 4 + 3000 * 20  # 78,000 micro-dollars = $0.078


def _bound(nbytes):
    return BOUND_TOKENS


def ok_body(**over):
    usage = {"input_tokens": 3100, "output_tokens": 900, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
             "service_tier": "standard", "inference_geo": "global", "speed": "standard",
             "output_tokens_details": {"thinking_tokens": 400}}
    usage |= over.pop("usage", {})
    body = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "thinking", "thinking": "", "signature": "x"}, {"type": "text", "text": '{"a": "ok"}'}],
            "stop_reason": "end_turn", "stop_sequence": None, "usage": usage}
    return body | over


class Transport:
    """In-memory HTTP: records every request the SDK sends and answers from a script."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, req):
        self.requests.append(req)
        a = self.answers.pop(0) if self.answers else (200, ok_body())
        if isinstance(a, Exception):
            raise a
        code, body = a
        return httpx2.Response(code, json=body, headers={"request-id": f"req_{len(self.requests)}"})


def client(t):
    return build_client("test-not-a-key", http_client=httpx2.Client(transport=httpx2.MockTransport(t)), env={})


def journal(tmp_path, out="out", create=True, **kw):
    f = Journal.create if create else Journal.resume
    j = f(tmp_path / "state", IDENT, ID, tmp_path / out, ceiling_micro=kw.get("ceiling", 12_500_000),
          per_call_max_micro=PER_CALL, events=kw.get("events", EVENTS))
    return j


def caller(tmp_path, t, **kw):
    j = journal(tmp_path, **kw)
    j.preauthorize({e: PER_CALL for e in j.events})
    return PaidCaller(j, client(t), _bound), j


# ------------------------------------------------------------------ the pinned request
def test_the_request_is_pinned_model_schema_effort_tier_geo_and_nothing_else(tmp_path):
    t = Transport()
    c, j = caller(tmp_path, t)
    r = c.call(EVENTS[0], "sys", "user ü ✓", SCHEMA)
    assert r.state == "settled" and r.text == '{"a": "ok"}' and r.provider_request_id == "req_1"
    req = t.requests[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["anthropic-version"] == "2026-10-21"
    assert not any(h.lower().startswith("anthropic-beta") for h in req.headers)
    assert req.headers.get("x-stainless-retry-count") == "0"
    body = json.loads(req.content)
    assert set(body) == {"model", "max_tokens", "system", "messages", "output_config", "service_tier", "inference_geo"}
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 3000
    assert body["output_config"]["effort"] == "low" and body["output_config"]["format"]["type"] == "json_schema"
    assert body["service_tier"] == "standard_only" and body["inference_geo"] == "global"
    assert body["messages"] == [{"role": "user", "content": "user ü ✓"}]
    sch = body["output_config"]["format"]["schema"]
    assert sch["additionalProperties"] is False and "maxLength" not in json.dumps(sch)
    assert r.charge_micro == 3100 * 4 + 900 * 20                       # exact integer micro-dollars


def test_rendered_bytes_are_exactly_what_the_sdk_sends(tmp_path):
    t = Transport()
    c, _ = caller(tmp_path, t)
    params = request_params("s", "u" * 50, SCHEMA)
    body, headers = render_request(params)
    c.call(EVENTS[0], "s", "u" * 50, SCHEMA)
    assert t.requests[0].content == body
    assert "authorization" not in {k.lower() for k in headers} and "x-api-key" not in {k.lower() for k in headers}


@pytest.mark.parametrize("var", ["ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_PROFILE",
                                 "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_LOG"])
def test_environment_overrides_of_endpoint_headers_or_profile_are_refused(var):
    with pytest.raises(PaidPathRefused):
        guard_environment({var: "x"})
    with pytest.raises(PaidPathRefused):
        build_client("k", env={var: "x"})


def test_a_different_sdk_version_is_refused(monkeypatch):
    monkeypatch.setattr(anthropic, "__version__", "2.8.0")
    with pytest.raises(PaidPathRefused):
        build_client("k", env={})


# ------------------------------------------------------------------ one generation per event
def test_a_timeout_is_held_at_the_maximum_and_never_resent(tmp_path):
    t = Transport(httpx2.ReadTimeout("slow"))
    c, j = caller(tmp_path, t)
    r = c.call(EVENTS[0], "s", "u", SCHEMA)
    assert r.state == "held" and r.outcome == "timeout" and r.charge_micro == PER_CALL
    assert len(t.requests) == 1                                         # SDK retries are off
    again = c.call(EVENTS[0], "s", "u", SCHEMA)
    assert again.resumed and again.state == "held" and len(t.requests) == 1


@pytest.mark.parametrize("answer,outcome", [
    ((200, ok_body(stop_reason="refusal")), "refusal"),
    ((200, ok_body(stop_reason="max_tokens")), "max_tokens"),
    ((200, ok_body(model="claude-opus-5")), "model_mismatch"),
    ((200, ok_body(content=[])), "incomplete"),
    ((529, {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}), "api_error"),
    ((500, {"type": "error", "error": {"type": "api_error", "message": "x"}}), "api_error"),
    (httpx2.ConnectError("reset"), "transport_error"),
])
def test_every_non_clean_response_is_a_conservative_hold(tmp_path, answer, outcome):
    t = Transport(answer)
    c, j = caller(tmp_path, t)
    r = c.call(EVENTS[0], "s", "u", SCHEMA)
    assert (r.state, r.outcome, r.charge_micro) == ("held", outcome, PER_CALL)
    assert len(t.requests) == 1


def test_missing_usage_is_held(tmp_path):
    body = ok_body()
    body.pop("usage")
    t = Transport((200, body))
    c, _ = caller(tmp_path, t)
    r = c.call(EVENTS[0], "s", "u", SCHEMA)
    assert r.state == "held"


@pytest.mark.parametrize("usage", [{"cache_read_input_tokens": 10}, {"cache_creation_input_tokens": 5},
                                   {"service_tier": "priority"}, {"speed": "fast"}, {"inference_geo": "us"},
                                   {"output_tokens": 3001}, {"output_tokens_details": {"thinking_tokens": 5000}}])
def test_unpriced_or_contradictory_usage_stops_the_run(tmp_path, usage):
    t = Transport((200, ok_body(usage=usage)))
    c, j = caller(tmp_path, t)
    with pytest.raises(CriticalStop):
        c.call(EVENTS[0], "s", "u", SCHEMA)
    a = j.get(EVENTS[0])
    assert a.state == "held" and a.charge_micro == PER_CALL


def test_check_usage_prices_exactly_and_requires_the_standard_tier():
    assert check_usage({"input_tokens": 1, "output_tokens": 1, "service_tier": "standard"}, "m") == ("ok", 24, "")
    assert check_usage({"input_tokens": 1, "output_tokens": 1}, "m")[0] == "stop"           # tier not confirmed
    assert check_usage({"input_tokens": -1, "output_tokens": 1, "service_tier": "standard"}, "m")[0] == "hold"
    assert check_usage({"input_tokens": 1.5, "output_tokens": 1, "service_tier": "standard"}, "m")[0] == "hold"


# ------------------------------------------------------------------ crashes at every step
def test_death_before_dispatch_releases_nothing_and_sends_once_on_resume(tmp_path):
    t = Transport()
    c, j = caller(tmp_path, t)
    body, _ = render_request(request_params("s", "u", SCHEMA))
    import hashlib
    j.reserve(EVENTS[0], hashlib.sha256(body).hexdigest(), len(body), PER_CALL)   # reserved, then the process dies
    j.close()
    j2 = journal(tmp_path, create=False)
    assert j2.get(EVENTS[0]).state == "reserved" and j2.committed_micro() == PER_CALL
    r = PaidCaller(j2, client(t), _bound).call(EVENTS[0], "s", "u", SCHEMA)
    assert r.state == "settled" and len(t.requests) == 1


def test_death_after_dispatch_before_response_is_held_and_never_resent(tmp_path):
    t = Transport()
    c, j = caller(tmp_path, t)
    import hashlib
    body, _ = render_request(request_params("s", "u", SCHEMA))
    sha = hashlib.sha256(body).hexdigest()
    j.reserve(EVENTS[0], sha, len(body), PER_CALL)
    j.mark_dispatched(EVENTS[0], sha)                                    # the request is in flight; process dies
    j.close()
    j2 = journal(tmp_path, create=False)
    a = j2.get(EVENTS[0])
    assert a.state == "held" and a.outcome == "unknown_after_restart" and a.charge_micro == PER_CALL
    r = PaidCaller(j2, client(t), _bound).call(EVENTS[0], "s", "u", SCHEMA)
    assert r.resumed and r.state == "held" and t.requests == []


def test_death_after_response_before_settlement_is_held(tmp_path, monkeypatch):
    t = Transport()
    c, j = caller(tmp_path, t)
    monkeypatch.setattr(j, "settle", lambda *a, **k: (_ for _ in ()).throw(SystemExit("killed")))
    with pytest.raises(SystemExit):
        c.call(EVENTS[0], "s", "u", SCHEMA)
    j.close()
    j2 = journal(tmp_path, create=False)
    assert j2.get(EVENTS[0]).state == "held" and len(t.requests) == 1
    assert PaidCaller(j2, client(t), _bound).call(EVENTS[0], "s", "u", SCHEMA).resumed and len(t.requests) == 1


def test_death_before_decision_persistence_keeps_the_paid_answer(tmp_path):
    t = Transport()
    c, j = caller(tmp_path, t)
    first = c.call(EVENTS[0], "s", "u", SCHEMA)
    j.close()                                                            # the ledger was never written
    j2 = journal(tmp_path, create=False)
    again = PaidCaller(j2, client(t), _bound).call(EVENTS[0], "s", "u", SCHEMA)
    assert again.resumed and again.text == first.text and again.charge_micro == first.charge_micro
    assert len(t.requests) == 1


def test_a_changed_request_for_an_already_paid_event_is_refused(tmp_path):
    t = Transport()
    c, j = caller(tmp_path, t)
    c.call(EVENTS[0], "s", "u", SCHEMA)
    with pytest.raises(JournalError):
        c.call(EVENTS[0], "s", "a different prompt", SCHEMA)
    assert len(t.requests) == 1


# ------------------------------------------------------------------ budget and journal integrity
def test_the_whole_run_must_fit_before_any_send(tmp_path):
    j = journal(tmp_path, ceiling=3 * PER_CALL)                           # four events, room for three
    with pytest.raises(BudgetRefused):
        j.preauthorize({e: PER_CALL for e in EVENTS})
    with pytest.raises(PaidPathRefused):
        PaidCaller(j, client(Transport()), _bound)


def test_reservations_are_integer_validated_and_match_their_preauthorization(tmp_path):
    j = journal(tmp_path)
    j.preauthorize({e: PER_CALL for e in EVENTS})
    for bad in (1.5, -1, True, float("nan"), PER_CALL + 1):
        with pytest.raises(BudgetRefused):
            j.reserve(EVENTS[0], "x" * 64, 10, bad)
    with pytest.raises(BudgetRefused):
        j.reserve("NOT:AN-EVENT", "x" * 64, 10, PER_CALL)
    j.reserve(EVENTS[0], "x" * 64, 10, PER_CALL)
    with pytest.raises(BudgetRefused):
        j.reserve(EVENTS[0], "x" * 64, 10, PER_CALL)                    # one attempt per event
    with pytest.raises(BudgetRefused):
        j.preauthorize({e: PER_CALL - 1 for e in EVENTS})               # cannot be changed


def _hold_lock(path, q):
    try:
        j = Journal.resume(path / "state", IDENT, ID, path / "out", ceiling_micro=12_500_000,
                           per_call_max_micro=PER_CALL, events=EVENTS)
        q.put("opened")
        j.close()
    except JournalLocked:
        q.put("locked")


def test_a_second_runner_is_refused(tmp_path):
    j = journal(tmp_path)
    q = mp.get_context("fork").Queue()
    p = mp.get_context("fork").Process(target=_hold_lock, args=(tmp_path, q))
    p.start()
    p.join(20)
    assert q.get(timeout=5) == "locked"
    j.close()


def test_a_new_output_directory_cannot_reset_the_experiment(tmp_path):
    j = open_journal(tmp_path / "state", IDENT, ID, tmp_path / "out", ceiling_micro=12_500_000,
                     per_call_max_micro=PER_CALL, events=EVENTS)
    j.close()
    with pytest.raises(JournalError):
        open_journal(tmp_path / "state", IDENT, ID, tmp_path / "elsewhere", ceiling_micro=12_500_000,
                     per_call_max_micro=PER_CALL, events=EVENTS)


def test_a_missing_journal_is_refused_on_resume(tmp_path):
    j = open_journal(tmp_path / "state", IDENT, ID, tmp_path / "out", ceiling_micro=12_500_000,
                     per_call_max_micro=PER_CALL, events=EVENTS)
    j.close()
    (tmp_path / "state" / f"{ID}.sqlite3").unlink()
    with pytest.raises(JournalError):
        open_journal(tmp_path / "state", IDENT, ID, tmp_path / "out", ceiling_micro=12_500_000,
                     per_call_max_micro=PER_CALL, events=EVENTS)


def test_another_identity_ceiling_or_event_list_is_refused(tmp_path):
    journal(tmp_path).close()
    for kw in ({"ceiling": 20_000_000}, {"events": EVENTS[:3]}):
        with pytest.raises(JournalError):
            journal(tmp_path, create=False, **kw)
    with pytest.raises(JournalError):
        Journal.resume(tmp_path / "state", {"other": 1}, ID, tmp_path / "out", ceiling_micro=12_500_000,
                       per_call_max_micro=PER_CALL, events=EVENTS)


def test_an_edited_or_corrupt_journal_stops_everything(tmp_path):
    t = Transport()
    c, j = caller(tmp_path, t)
    c.call(EVENTS[0], "s", "u", SCHEMA)
    j.close()
    db = sqlite3.connect(tmp_path / "state" / f"{ID}.sqlite3")
    db.execute("UPDATE attempts SET charge_micro = 1")                   # someone lowers the charge
    db.commit()
    db.close()
    with pytest.raises(DamagedJournal):
        journal(tmp_path, create=False)
    p = tmp_path / "state" / f"{ID}.sqlite3"
    p.write_bytes(p.read_bytes()[:200] + b"garbage" * 50)
    with pytest.raises((DamagedJournal, JournalError, sqlite3.DatabaseError)):
        journal(tmp_path, create=False)


# ------------------------------------------------------------------ the budget proof
def test_the_budget_proof_does_not_hold_so_the_paid_path_is_blocked():
    body, _ = render_request(request_params("s" * 2000, "u" * 9000, SCHEMA))
    proof = budget_proof({f"E{i}": body for i in range(160)})
    assert not proof.proven and proof.totals["max_total_micro"] is None
    assert any("no demonstrated upper bound" in r for r in proof.reasons)
    assert proof.totals["theoretical_registered_max_micro"] == 12_480_000
    assert proof.totals["byte_count_illustration_micro"] > FROZEN["ceiling_micro_usd"]


def test_a_caller_without_a_demonstrated_bound_sends_nothing(tmp_path):
    t = Transport()
    j = journal(tmp_path)
    j.preauthorize({e: PER_CALL for e in EVENTS})
    from autotrader.paid import proven_input_bound
    with pytest.raises(PaidPathRefused):
        PaidCaller(j, client(t), proven_input_bound).call(EVENTS[0], "s", "u", SCHEMA)
    assert t.requests == [] and j.attempts() == []


def test_a_run_with_missing_inputs_is_never_proven_by_having_fewer_requests():
    assert not budget_proof({}).proven
    body, _ = render_request(request_params("s", "u", SCHEMA))
    few = budget_proof({f"E{i}": body for i in range(159)}, bound_fn=lambda n: 10)
    assert not few.proven and any("only 159 of the 160" in r for r in few.reasons)
    full = budget_proof({f"E{i}": body for i in range(160)}, bound_fn=lambda n: 10)
    assert full.proven                       # only with a (test) bound for every one of the 160 requests


def test_an_oversized_request_cannot_be_proven_even_with_a_bound():
    body, _ = render_request(request_params("s", "ü" * 20_000, SCHEMA))        # non-ASCII: 2 bytes per character
    assert len(body) > 40_000
    proof = budget_proof({f"E{i}": body for i in range(160)}, bound_fn=lambda n: n)
    assert not proof.proven and any("exceed the registered 4,500 input tokens" in r for r in proof.reasons)
