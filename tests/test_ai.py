import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from dkapp.ai.engine import AIEngine, build_entry_prompt
from dkapp.ai.providers import AIUnavailable, AnthropicProvider, OllamaProvider, anthropic_cost
from dkapp.ai.schema import ENTRY_SCHEMA, parse_entry, parse_exit
from dkapp.config import AppSettings
from dkapp.models import Signal
from dkapp.storage.db import Database
from tests.harness import ScriptedAI

GOOD = {"action": "buy", "confidence": 0.8, "evidence_quality": "strong", "stop_loss_pct": 1.0,
        "take_profit_pct": 2.0, "max_hold_minutes": 45, "reasoning": "trend and volume agree here.",
        "key_risks": ["could reverse"]}


def sig():
    return Signal("AAPL", "orb", datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc), 200.0, 198.0, 204.0, 0.7,
                  ["broke out"], 90, {"rsi14": 61.0})


# ---------------------------------------------------------------- validation
def test_valid_json_parses():
    d, err = parse_entry(json.dumps(GOOD))
    assert err is None and d.action == "buy" and d.confidence == 0.8


def test_json_in_code_fence_or_with_chatter_parses():
    d, _ = parse_entry("```json\n" + json.dumps(GOOD) + "\n```")
    assert d is not None
    d, _ = parse_entry("Sure! Here you go: " + json.dumps(GOOD) + " hope that helps")
    assert d is not None


@pytest.mark.parametrize("patch", [
    {"confidence": 1.5}, {"confidence": -0.1}, {"action": "short"}, {"action": "buy_more"},
    {"stop_loss_pct": 0}, {"stop_loss_pct": 45}, {"max_hold_minutes": 0}, {"evidence_quality": "great"},
    {"reasoning": "ok"}, {"surprise": "extra field"},
])
def test_schema_violations_are_rejected(patch):
    bad = dict(GOOD, **patch)
    d, err = parse_entry(json.dumps(bad))
    assert d is None and "validation" in err


def test_missing_fields_and_garbage_are_rejected():
    assert parse_entry(json.dumps({"action": "buy"}))[0] is None
    assert parse_entry("I think you should buy.")[0] is None
    assert parse_entry("")[0] is None
    assert parse_exit(json.dumps({"action": "hold", "confidence": 0.5, "reasoning": "trend intact"}))[0] is not None


def test_entry_schema_is_strict():
    assert ENTRY_SCHEMA["additionalProperties"] is False
    assert set(ENTRY_SCHEMA["required"]) == set(GOOD)


# ---------------------------------------------------------------- engine decisions
@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "ai.db", "paper")


def test_approval_requires_confidence_and_evidence(db):
    s = AppSettings()
    for conf, quality, approved in [(0.8, "strong", True), (0.5, "strong", False), (0.9, "weak", False),
                                     (0.9, "insufficient", False), (0.65, "moderate", True)]:
        eng = AIEngine(s, db, ScriptedAI(confidence=conf, quality=quality))
        res = eng.evaluate_entry(sig(), {})
        assert res.ok and res.approved is approved, (conf, quality)


def test_skip_is_never_approved(db):
    res = AIEngine(AppSettings(), db, ScriptedAI(action="skip", confidence=0.99)).evaluate_entry(sig(), {})
    assert res.ok and not res.approved


def test_unavailable_or_invalid_ai_means_no_trade(db):
    for provider in (ScriptedAI(fail="connection refused"), ScriptedAI(raw="not json at all"),
                     ScriptedAI(raw=json.dumps(dict(GOOD, confidence=7)))):
        res = AIEngine(AppSettings(), db, provider).evaluate_entry(sig(), {})
        assert not res.ok and not res.approved
    rows = db.query("SELECT valid, error FROM ai_decisions")
    assert len(rows) == 3 and all(r["valid"] == 0 and r["error"] for r in rows)


def test_provider_crash_is_contained(db):
    class Boom(ScriptedAI):
        def complete_json(self, *a, **k):
            raise ZeroDivisionError("bug")
    res = AIEngine(AppSettings(), db, Boom()).evaluate_entry(sig(), {})
    assert not res.ok and "unexpected" in res.error


def test_rules_mode_and_budget_block_calls(db):
    s = AppSettings()
    s.ai.decision_mode = "rules"
    assert AIEngine(s, db, ScriptedAI()).can_call()[0] is False
    s.ai.decision_mode = "ai_confirm"
    s.ai.monthly_budget_usd = 0.01
    paid = ScriptedAI()
    paid.is_paid = True
    eng = AIEngine(s, db, paid)
    db.insert("ai_decisions", time=datetime.now(timezone.utc).isoformat(), kind="entry", provider="anthropic",
              cost_usd=0.02, valid=1)
    ok, why = eng.can_call()
    assert not ok and "budget" in why


def test_hourly_call_cap(db):
    s = AppSettings()
    s.ai.max_calls_per_hour = 2
    eng = AIEngine(s, db, ScriptedAI())
    for _ in range(2):
        eng.evaluate_entry(sig(), {})
    assert eng.can_call()[0] is False


def test_prompt_contains_evidence_not_secrets():
    p = build_entry_prompt(sig(), {"recent_bars": [{"c": 1}], "market": {"spy_above_vwap": True}})
    text = json.dumps(p)
    assert "AAPL" in text and "opening-range" in text and "spy_above_vwap" in text
    assert "key" not in p and "secret" not in text.lower()


# ---------------------------------------------------------------- Ollama provider (HTTP mocked)
def test_ollama_provider_round_trip():
    seen = {}

    def handler(request: httpx.Request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})
        body = json.loads(request.content)
        seen.update(body)
        return httpx.Response(200, json={"message": {"content": json.dumps(GOOD)}, "prompt_eval_count": 321,
                                         "eval_count": 54})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://localhost:11434")
    p = OllamaProvider("http://localhost:11434", "llama3.1:8b", client=client)
    assert p.health()[0]
    r = p.complete_json("sys", "user", ENTRY_SCHEMA)
    assert seen["format"] == ENTRY_SCHEMA and seen["stream"] is False
    assert r.cost_usd == 0.0 and r.input_tokens == 321 and parse_entry(r.text)[0] is not None
    assert p.list_models() == ["llama3.1:8b"]


def test_ollama_missing_model_and_offline():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"models": []})))
    ok, msg = OllamaProvider("http://x", "llama3.1:8b", client=client).health()
    assert not ok and "ollama pull" in msg

    def down(request):
        raise httpx.ConnectError("refused")
    p = OllamaProvider("http://x", "m", client=httpx.Client(transport=httpx.MockTransport(down)))
    assert p.health()[0] is False
    with pytest.raises(AIUnavailable):
        p.complete_json("s", "u", ENTRY_SCHEMA)


# ---------------------------------------------------------------- Anthropic provider (client faked)
class FakeAnthropicClient:
    def __init__(self, stop="end_turn", text=json.dumps(GOOD), model="claude-opus-5-5"):
        resp = SimpleNamespace(stop_reason=stop, model=model,
                               usage=SimpleNamespace(input_tokens=1500, output_tokens=600, cache_read_input_tokens=0),
                               content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)])
        self.calls = []
        outer = self

        class _M:
            def create(self_inner, **kw):
                outer.calls.append(("messages", kw))
                return resp
        class _B:
            messages = _M()
        self.messages = _M()
        self.beta = _B()
        self.models = SimpleNamespace(retrieve=lambda m: SimpleNamespace(id=m))


def test_anthropic_structured_output_and_cost():
    fake = FakeAnthropicClient()
    p = AnthropicProvider("sk-test", "claude-opus-5-5", "low", client=fake)
    r = p.complete_json("sys", "user", ENTRY_SCHEMA)
    _, kw = fake.calls[0]
    assert kw["output_config"]["format"]["schema"] == ENTRY_SCHEMA and kw["output_config"]["effort"] == "low"
    assert kw["fallbacks"] == "default"
    assert r.cost_usd == pytest.approx(anthropic_cost("claude-opus-5-5", 1500, 600))
    assert r.cost_usd == pytest.approx(1500 * 4 / 1e6 + 600 * 20 / 1e6)
    assert parse_entry(r.text)[0] is not None


def test_anthropic_haiku_uses_plain_endpoint_and_cheap_pricing():
    fake = FakeAnthropicClient(model="claude-haiku-5-5")
    r = AnthropicProvider("k", "claude-haiku-5-5", client=fake).complete_json("s", "u", ENTRY_SCHEMA)
    assert "fallbacks" not in fake.calls[0][1]
    assert r.cost_usd == pytest.approx(1500 * 0.10 / 1e6 + 600 * 0.50 / 1e6)


def test_anthropic_refusal_and_truncation_mean_no_trade():
    for stop in ("refusal", "max_tokens"):
        with pytest.raises(AIUnavailable):
            AnthropicProvider("k", "claude-opus-5-5", client=FakeAnthropicClient(stop=stop)).complete_json("s", "u", ENTRY_SCHEMA)
