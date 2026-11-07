"""AI model providers.

* Ollama (default): runs a model locally on this computer — no subscription, no cost.
* Anthropic (optional): Claude models via the official `anthropic` SDK. Every call's
  token usage is converted to dollars and recorded so real spending is visible, and a
  monthly budget cap stops cloud calls once reached.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Protocol

import httpx

log = logging.getLogger(__name__)


class AIUnavailable(Exception):
    """The model could not produce an answer (offline, auth, budget, refusal, timeout)."""


@dataclass
class ProviderResult:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    provider: str
    model: str


class AIProvider(Protocol):
    name: str
    model: str
    is_paid: bool

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int = 1200) -> ProviderResult: ...

    def health(self) -> tuple[bool, str]: ...


# ----------------------------------------------------------------- Ollama

class OllamaProvider:
    name = "ollama"
    is_paid = False

    def __init__(self, base_url: str, model: str, timeout: float = 60.0, client: Optional[httpx.Client] = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        # Local server: never route through a proxy.
        self._http = client or httpx.Client(timeout=timeout, trust_env=False)

    def list_models(self) -> list[str]:
        try:
            r = self._http.get(f"{self.base_url}/api/tags", timeout=5.0)
            r.raise_for_status()
            return sorted(m.get("name", "") for m in r.json().get("models", []) if m.get("name"))
        except (httpx.HTTPError, ValueError):
            return []

    def health(self) -> tuple[bool, str]:
        try:
            r = self._http.get(f"{self.base_url}/api/tags", timeout=5.0)
            r.raise_for_status()
        except httpx.HTTPError:
            return False, f"Ollama is not running at {self.base_url}"
        names = {m.get("name", "") for m in r.json().get("models", [])}
        wanted = {self.model, f"{self.model}:latest"}
        if not names & wanted:
            return False, f"model '{self.model}' is not installed (run: ollama pull {self.model})"
        return True, f"{self.model} on this computer"

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int = 1200) -> ProviderResult:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": max_tokens},
        }
        t0 = time.monotonic()
        try:
            r = self._http.post(f"{self.base_url}/api/chat", json=body, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except httpx.TimeoutException as exc:
            raise AIUnavailable(f"local model timed out after {self.timeout:.0f}s") from exc
        except httpx.HTTPStatusError as exc:
            raise AIUnavailable(f"Ollama error {exc.response.status_code}: {exc.response.text[:200]}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise AIUnavailable(f"Ollama unreachable: {exc}") from exc
        text = (data.get("message") or {}).get("content", "")
        return ProviderResult(text, int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0), 0.0,
                              int((time.monotonic() - t0) * 1000), self.name, self.model)


# ----------------------------------------------------------------- Anthropic

# USD per million tokens (input, output). Source: Claude API model table, Oct 2026.
ANTHROPIC_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
}
ANTHROPIC_MODELS = [
    ("claude-opus-5-5", "Claude Opus 5.5 — most capable"),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5 — balanced"),
    ("claude-haiku-5-5", "Claude Haiku 5.5 — cheapest"),
]
# Models that support the server-side refusal fallback (others simply decline -> no trade).
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


def anthropic_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = ANTHROPIC_PRICES.get(model, ANTHROPIC_PRICES["claude-opus-5-5"])
    return input_tokens * pin / 1_000_000 + output_tokens * pout / 1_000_000


class AnthropicProvider:
    name = "anthropic"
    is_paid = True

    def __init__(self, api_key: str, model: str = "claude-opus-5-5", effort: str = "low", timeout: float = 60.0,
                 client=None):
        import anthropic

        self.model = model
        self.effort = effort
        self._anthropic = anthropic
        self.client = client or anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=1)

    def health(self) -> tuple[bool, str]:
        a = self._anthropic
        try:
            self.client.models.retrieve(self.model)  # free metadata call: checks key and model
            return True, f"{self.model} (cloud, billed per use)"
        except a.AuthenticationError:
            return False, "Anthropic API key was rejected"
        except a.NotFoundError:
            return False, f"model {self.model} not available to this key"
        except a.APIConnectionError:
            return False, "cannot reach api.anthropic.com"
        except a.APIStatusError as exc:
            return False, f"Anthropic error {exc.status_code}"

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int = 1200) -> ProviderResult:
        a = self._anthropic
        # Thinking is always on for current models; output_config.effort keeps it short and cheap.
        # max_tokens must leave room for thinking plus the JSON answer.
        kwargs = dict(
            model=self.model,
            max_tokens=max(4000, max_tokens * 4),
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
        )
        t0 = time.monotonic()
        try:
            if self.model in _FALLBACK_MODELS:
                resp = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                        fallbacks="default", **kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        except a.AuthenticationError as exc:
            raise AIUnavailable("Anthropic API key was rejected") from exc
        except a.RateLimitError as exc:
            raise AIUnavailable("Anthropic rate limit reached") from exc
        except a.BadRequestError as exc:
            raise AIUnavailable(f"Anthropic rejected the request: {exc.message}") from exc
        except a.APIStatusError as exc:
            raise AIUnavailable(f"Anthropic error {exc.status_code}") from exc
        except a.APITimeoutError as exc:
            raise AIUnavailable("Anthropic request timed out") from exc
        except a.APIConnectionError as exc:
            raise AIUnavailable("cannot reach api.anthropic.com") from exc
        latency = int((time.monotonic() - t0) * 1000)
        usage = getattr(resp, "usage", None)
        tin = int(getattr(usage, "input_tokens", 0) or 0) + int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        tout = int(getattr(usage, "output_tokens", 0) or 0)
        served = getattr(resp, "model", None) or self.model
        cost = anthropic_cost(served if served in ANTHROPIC_PRICES else self.model, tin, tout)
        stop = getattr(resp, "stop_reason", None)
        if stop == "refusal":
            raise AIUnavailable("the model declined to answer — no trade")
        if stop == "max_tokens":
            raise AIUnavailable("the model's answer was cut off — no trade")
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        return ProviderResult(text, tin, tout, cost, latency, self.name, served)


class DisabledProvider:
    name = "none"
    model = "—"
    is_paid = False

    def __init__(self, reason: str):
        self.reason = reason

    def health(self) -> tuple[bool, str]:
        return False, self.reason

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int = 1200) -> ProviderResult:
        raise AIUnavailable(self.reason)
