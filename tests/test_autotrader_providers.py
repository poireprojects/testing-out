"""Paid-model safeguards for the phase-5 evaluation (no network: fake clients only)."""
from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from autotrader.__main__ import _provider
from autotrader.decision import json_schema_v2, sentences
from autotrader.providers import (AnthropicJSONProvider, Budget, BudgetedProvider, BudgetExceeded, api_schema,
                                  budgeted_anthropic, worst_case_usd)
from stockpilot.ai.providers import AIUnavailable, ProviderResult
from tests.test_autotrader_v2 import _pack


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_api_schema_follows_the_structured_output_rules_without_losing_choices():
    p = _pack()
    raw = json_schema_v2(p, sentences(p))
    s = api_schema(raw)
    for n in _walk(s):
        assert not {"minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum"} & set(n)
        if n.get("type") == "object":
            assert n["additionalProperties"] is False and set(n["required"]) == set(n["properties"])
    trade = s["properties"]["trade"]["anyOf"][1]
    assert trade["properties"]["stop_pct"]["enum"] == [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    assert "maxItems" in raw["properties"]["facts"]                       # the original is not modified


class Fake:
    name, model, is_paid = "fake", "m", True

    def __init__(self, cost=0.03, fail=None):
        self.cost, self.fail, self.calls = cost, fail, 0

    def complete_json(self, *a, **k):
        self.calls += 1
        if self.fail:
            raise self.fail
        return ProviderResult("{}", 100, 100, self.cost, 1, "fake", "m")


def test_the_budget_reserves_the_worst_case_and_never_passes_the_ceiling(tmp_path):
    b = Budget(tmp_path / "b.json", 0.20)
    prov = BudgetedProvider(Fake(cost=0.03), b, max_input_tokens=4500, max_output_tokens=3000,
                            price_in_per_m=4.0, price_out_per_m=20.0)       # worst $0.078 per call
    for _ in range(5):                                                     # spent 0.15 after five calls
        prov.complete_json("s", "u", {})
    with pytest.raises(BudgetExceeded):
        prov.complete_json("s", "u", {})                                   # 0.15 + 0.078 > 0.20: refused
    assert prov.inner.calls == 5
    assert b.spent <= 0.20


def test_the_budget_survives_restarts_and_its_ceiling_cannot_be_raised_by_the_program(tmp_path):
    b = Budget(tmp_path / "b.json", 1.0)
    b.settle(0.4)
    assert Budget(tmp_path / "b.json", 1.0).spent == pytest.approx(0.4)
    with pytest.raises(ValueError):
        Budget(tmp_path / "b.json", 5.0)


def test_a_failed_paid_call_is_charged_at_its_worst_case_unless_its_cost_is_known(tmp_path):
    b = Budget(tmp_path / "b.json", 1.0)
    prov = BudgetedProvider(Fake(fail=AIUnavailable("timeout")), b, 4500, 3000, 4.0, 20.0)
    with pytest.raises(AIUnavailable):
        prov.complete_json("s", "u", {})
    assert b.spent == pytest.approx(0.078)
    e = AIUnavailable("refused")
    e.cost_usd = 0.01
    prov2 = BudgetedProvider(Fake(fail=e), b, 4500, 3000, 4.0, 20.0)
    with pytest.raises(AIUnavailable):
        prov2.complete_json("s", "u", {})
    assert b.spent == pytest.approx(0.088)


class FakeClient:
    def __init__(self, stop="end_turn", model="claude-opus-5-5", text='{"ok": true}'):
        self.kw, self.stop, self.model, self.text = None, stop, model, text
        self.messages = self

    def create(self, **kw):
        self.kw = kw
        return SimpleNamespace(model=self.model, stop_reason=self.stop,
                               usage=SimpleNamespace(input_tokens=3000, output_tokens=800, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0),
                               content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=self.text)])


def test_the_anthropic_provider_uses_one_model_structured_output_and_no_fallback():
    c = FakeClient()
    p = AnthropicJSONProvider("claude-opus-5-5", client=c)
    res = p.complete_json("sys", "user", {"type": "object", "properties": {"a": {"type": "string", "maxLength": 5}}})
    assert res.text == '{"ok": true}' and res.cost_usd == pytest.approx(3000 * 4 / 1e6 + 800 * 20 / 1e6)
    assert c.kw["model"] == "claude-opus-5-5" and "fallbacks" not in c.kw and "betas" not in c.kw
    fmt = c.kw["output_config"]
    assert fmt["effort"] == "low" and fmt["format"]["type"] == "json_schema"
    assert fmt["format"]["schema"]["additionalProperties"] is False
    assert "maxLength" not in json.dumps(fmt["format"]["schema"])


@pytest.mark.parametrize("client,needle", [
    (FakeClient(stop="refusal"), "declined"),
    (FakeClient(stop="max_tokens"), "cut off"),
    (FakeClient(model="claude-opus-4-8"), "not claude-opus-5-5"),
])
def test_refusals_truncation_and_substitute_models_are_failures_with_their_cost(client, needle):
    with pytest.raises(AIUnavailable) as e:
        AnthropicJSONProvider("claude-opus-5-5", client=client).complete_json("s", "u", {"type": "object", "properties": {}})
    assert needle in str(e.value) and e.value.cost_usd > 0


def test_the_command_line_refuses_a_run_whose_worst_case_exceeds_the_ceiling(tmp_path):
    a = argparse.Namespace(llm="anthropic", model="claude-opus-5-5", budget_usd=12.50)
    assert worst_case_usd("claude-opus-5-5", 160) == pytest.approx(12.48)
    with pytest.raises(SystemExit):
        _provider(a, 161, tmp_path)
    p = budgeted_anthropic("claude-opus-5-5", tmp_path / "b.json", 12.50, client=FakeClient())
    assert p.worst == pytest.approx(0.078) and p.model == "claude-opus-5-5"
