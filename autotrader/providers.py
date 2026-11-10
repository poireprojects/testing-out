"""Model access for evaluations: one fixed model, a hard spending ceiling, and the API's schema rules.

Nothing here is called unless the owner supplies a key and runs a paid evaluation on purpose.

**Superseded for the development evaluation (dev-eval amendment 1).** The audit of 2026-11-10 found that `Budget`
compares floats in a JSON file next to the output, forgets a reservation on restart, lets a new output directory
start a fresh budget, and that `max_retries=1` plus an unknown timeout could send a generation twice under one
settled call; `chars / 3` does not bound tokenization. No command uses this path any more: the paid path is
`autotrader/paid.py` with the transactional journal in `autotrader/journal.py`. `api_schema` is still used.

* `Budget` keeps a running total in a JSON file (it survives restarts). Before every call it reserves the
  call's **worst-case** cost (maximum input tokens at the input price + max_tokens at the output price); if
  spent + reserved would exceed the ceiling, the call is refused. After the call the reservation is replaced
  by the actual cost. Spending can therefore never pass the ceiling, whatever the model does.
* `api_schema` adapts a decision schema to the structured-output rules of the Claude API: every object gets
  `additionalProperties: false`, and the keywords the API does not support (minItems/maxItems, minLength/
  maxLength, minimum/maximum, multipleOf) are removed. The same limits are still enforced, after the answer,
  by the pydantic models in decision.py, so nothing is weakened.
* `AnthropicJSONProvider` calls one model with structured outputs and a fixed effort. It deliberately does
  **not** use refusal fallbacks: a fallback would answer some events with a different model, and an
  evaluation must keep one model. A refusal, a cut-off answer or a different serving model is a model failure,
  which the runner turns into "no trade" (safe state).
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from stockpilot.ai.providers import ANTHROPIC_PRICES, AIUnavailable, ProviderResult, anthropic_cost

UNSUPPORTED = ("minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum", "multipleOf")


class BudgetExceeded(AIUnavailable):
    pass


def api_schema(schema: dict) -> dict:
    def fix(node):
        if isinstance(node, dict):
            for k in UNSUPPORTED:
                node.pop(k, None)
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                node.setdefault("required", list(node.get("properties", {})))
            for v in list(node.values()):
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)
        return node
    return fix(copy.deepcopy(schema))


class Budget:
    def __init__(self, path: Path, ceiling_usd: float):
        self.path, self.ceiling = Path(path), float(ceiling_usd)
        d = json.loads(self.path.read_text()) if self.path.exists() else {}
        if d and abs(d.get("ceiling_usd", self.ceiling) - self.ceiling) > 1e-9:
            raise ValueError("the ceiling of an existing budget file cannot be changed by the program")
        self.spent = float(d.get("spent_usd", 0.0))
        self.calls = int(d.get("calls", 0))

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"ceiling_usd": self.ceiling, "spent_usd": round(self.spent, 6),
                                         "calls": self.calls}, indent=1))

    def reserve(self, worst_usd: float) -> None:
        if self.spent + worst_usd > self.ceiling + 1e-12:
            raise BudgetExceeded(f"spending ceiling ${self.ceiling:.2f} reached (spent ${self.spent:.4f}; the next "
                                 f"call could cost up to ${worst_usd:.4f})")

    def settle(self, actual_usd: float) -> None:
        self.spent += actual_usd
        self.calls += 1
        self._save()


class BudgetedProvider:
    """Wraps any provider: refuses a call whose worst case would cross the ceiling, records the actual cost."""

    def __init__(self, inner, budget: Budget, max_input_tokens: int, max_output_tokens: int,
                 price_in_per_m: float, price_out_per_m: float):
        self.inner, self.budget = inner, budget
        self.name, self.model, self.is_paid = inner.name, inner.model, True
        self.worst = max_input_tokens * price_in_per_m / 1e6 + max_output_tokens * price_out_per_m / 1e6
        self.max_input_tokens = max_input_tokens

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int = 1200) -> ProviderResult:
        approx_in = (len(system) + len(user) + len(json.dumps(schema))) / 3.0     # generous chars-per-token bound
        if approx_in > self.max_input_tokens:
            raise AIUnavailable(f"prompt too long for the budgeted input bound ({approx_in:.0f} > {self.max_input_tokens})")
        self.budget.reserve(self.worst)
        try:
            res = self.inner.complete_json(system, user, schema, max_tokens)
        except BudgetExceeded:
            raise
        except AIUnavailable as exc:
            cost = getattr(exc, "cost_usd", None)
            self.budget.settle(self.worst if cost is None else cost)   # unknown cost of a failed call: count the worst
            raise
        self.budget.settle(res.cost_usd)
        return res

    def health(self):
        return self.inner.health()


class AnthropicJSONProvider:
    name = "anthropic"
    is_paid = True

    def __init__(self, model: str, effort: str = "low", max_tokens: int = 3000, client=None, timeout: float = 120.0):
        if model not in ANTHROPIC_PRICES:
            raise ValueError(f"no price on file for {model}")
        self.model, self.effort, self.max_tokens = model, effort, max_tokens
        if client is None:
            import anthropic
            client = anthropic.Anthropic(timeout=timeout, max_retries=1)       # key from ANTHROPIC_API_KEY or a profile
        self.client = client

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int = 1200) -> ProviderResult:
        t0 = time.monotonic()
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": api_schema(schema)}})
        except Exception as exc:                                    # auth, rate limit, bad request, timeout, network
            raise AIUnavailable(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
        usage = getattr(resp, "usage", None)
        tin = int(getattr(usage, "input_tokens", 0) or 0) + int(getattr(usage, "cache_read_input_tokens", 0) or 0) \
            + int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        tout = int(getattr(usage, "output_tokens", 0) or 0)
        cost = anthropic_cost(self.model, tin, tout)
        served = getattr(resp, "model", self.model)

        def fail(msg):
            e = AIUnavailable(msg)
            e.cost_usd = cost
            return e
        if served != self.model:
            raise fail(f"answered by {served}, not {self.model}: excluded to keep one model")
        if getattr(resp, "stop_reason", None) == "refusal":
            raise fail("the model declined to answer")
        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise fail("the answer was cut off at max_tokens")
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        return ProviderResult(text, tin, tout, cost, int((time.monotonic() - t0) * 1000), self.name, served)

    def health(self):
        return True, self.model


def budgeted_anthropic(model: str, budget_file: Path, ceiling_usd: float, effort: str = "low", max_tokens: int = 3000,
                       max_input_tokens: int = 4500, client=None) -> BudgetedProvider:
    pin, pout = ANTHROPIC_PRICES[model]
    inner = AnthropicJSONProvider(model, effort, max_tokens, client)
    return BudgetedProvider(inner, Budget(budget_file, ceiling_usd), max_input_tokens, max_tokens, pin, pout)


def worst_case_usd(model: str, calls: int, max_input_tokens: int = 4500, max_tokens: int = 3000) -> float:
    pin, pout = ANTHROPIC_PRICES[model]
    return calls * (max_input_tokens * pin + max_tokens * pout) / 1e6

