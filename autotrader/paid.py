"""The paid path for the development evaluation: one pinned request shape, one attempt per event, and a budget
proof that must hold before anything is sent. Nothing here runs unless the owner approves the spend, and the
budget proof currently cannot be met (see `budget_proof`), so the paid path is blocked.

Request (pinned, tested against anthropic 2.7.0 with an in-memory transport, never with a working key):
* endpoint https://api.anthropic.com/v1/messages, API version 2026-10-21, no environment or profile overrides;
* model claude-opus-5-5, max_tokens 3000, output_config {effort: low, format: json_schema};
* service_tier "standard_only" and inference_geo "global" sent explicitly (a workspace default of US-only
  inference would otherwise apply a 1.1x price); no thinking parameter (adaptive thinking is always on for this
  model; its tokens are output tokens and count toward max_tokens), no speed, no caching, no tools, no betas,
  no fallbacks, no metadata;
* SDK retries off (max_retries=0) and no middleware: a request is sent once or not at all.

Response: settled only if the served model is the requested one, stop_reason is end_turn, a text block exists and
the usage has exactly the priced categories (input, output) within the limits. Anything else is held at the
maximum charge. A usage report with an unpriced category (cache, fast mode, US-only, priority or batch tier,
server tools) or that contradicts the limits stops the whole run.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .experiment import FROZEN
from .journal import Journal, JournalError
from .providers import api_schema

PINNED_SDK = FROZEN["sdk"]["version"]
PINNED_API_VERSION = FROZEN["sdk"]["api_version"]
ENDPOINT = "https://api.anthropic.com"
# Environment variables that the SDK would honour and that could change the endpoint, the headers (for example a
# beta header), the credential source or what is logged. Their presence refuses the run.
FORBIDDEN_ENV = ("ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE",
                 "ANTHROPIC_CONFIG_DIR", "ANTHROPIC_IDENTITY_TOKEN", "ANTHROPIC_IDENTITY_TOKEN_FILE",
                 "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID", "ANTHROPIC_SERVICE_ACCOUNT_ID",
                 "ANTHROPIC_WORKSPACE_ID", "ANTHROPIC_LOG", "ANTHROPIC_WEBHOOK_SIGNING_KEY")
ALLOWED_PARAMS = {"model", "max_tokens", "system", "messages", "output_config", "service_tier", "inference_geo"}


class PaidPathRefused(RuntimeError):
    """The paid path cannot run safely as configured: nothing was sent."""


class CriticalStop(RuntimeError):
    """A response showed something the price sheet cannot account for: stop the whole run."""


def guard_environment(env: Optional[dict] = None) -> None:
    env = os.environ if env is None else env
    bad = [k for k in FORBIDDEN_ENV if env.get(k)]
    if bad:
        raise PaidPathRefused("refusing: these environment variables could change the endpoint, headers or "
                              f"credentials of the paid client: {', '.join(bad)}")


def check_sdk():
    import anthropic
    from anthropic._constants import ANTHROPIC_API_VERSION
    if anthropic.__version__ != PINNED_SDK:
        raise PaidPathRefused(f"anthropic {anthropic.__version__} is installed; this run is pinned to {PINNED_SDK}")
    if ANTHROPIC_API_VERSION != PINNED_API_VERSION:
        raise PaidPathRefused(f"the SDK sends API version {ANTHROPIC_API_VERSION}, not {PINNED_API_VERSION}")
    return anthropic


def build_client(api_key: str, http_client=None, env: Optional[dict] = None, timeout_s: float = 120.0):
    """The only way the paid client is made. `http_client` is for tests (an in-memory transport)."""
    guard_environment(env)
    anthropic = check_sdk()
    if not api_key:
        raise PaidPathRefused("no API key (ANTHROPIC_API_KEY is read only by the run command, never stored)")
    c = anthropic.Anthropic(api_key=api_key, base_url=ENDPOINT, max_retries=0,
                            timeout=anthropic.Timeout(timeout_s, connect=10.0), http_client=http_client)
    if str(c.base_url).rstrip("/") != ENDPOINT or c.max_retries != 0 or c.middleware:
        raise PaidPathRefused("the client is not pinned (endpoint, retries or middleware differ)")
    return c


def request_params(system: str, user: str, schema: dict) -> dict:
    return {"model": FROZEN["model"], "max_tokens": FROZEN["max_output_tokens"], "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": FROZEN["effort"],
                              "format": {"type": "json_schema", "schema": api_schema(schema)}},
            "service_tier": "standard_only", "inference_geo": "global"}


class _Rendered(Exception):
    pass


def render_request(params: dict) -> tuple[bytes, dict]:
    """The exact bytes and headers (credentials removed) that the pinned SDK would send for these parameters,
    obtained through the SDK itself with an in-memory transport that never touches the network."""
    import httpx2
    if set(params) - ALLOWED_PARAMS:
        raise PaidPathRefused(f"unexpected request parameters: {sorted(set(params) - ALLOWED_PARAMS)}")
    seen: dict = {}

    def capture(req):
        seen["body"], seen["url"] = req.content, str(req.url)
        seen["headers"] = {k: v for k, v in req.headers.items()
                           if k.lower() not in ("authorization", "x-api-key")}
        raise _Rendered()
    client = build_client("render-only-not-a-key", http_client=httpx2.Client(transport=httpx2.MockTransport(capture)),
                          env={})
    try:
        client.messages.create(**params)
    except Exception:
        pass
    if "body" not in seen:
        raise PaidPathRefused("the SDK did not produce a request body")
    if seen["url"] != ENDPOINT + "/v1/messages":
        raise PaidPathRefused(f"the request would go to {seen['url']}")
    return seen["body"], seen["headers"]


# ------------------------------------------------------------------ budget proof
@dataclass
class BudgetProof:
    proven: bool
    per_event: dict[str, dict]
    totals: dict
    reasons: list[str] = field(default_factory=list)

    def record(self) -> dict:
        return {"proven": self.proven, "reasons": self.reasons, "totals": self.totals, "per_event": self.per_event}


def proven_input_bound(request_bytes: int) -> Optional[int]:
    """A demonstrated upper bound on the billable input tokens of one request, or None.

    None, because no bound can be demonstrated offline:
    * structured outputs add a system prompt that is billed as input and whose size is not documented
      (platform.claude.com/docs/en/build-with-claude/structured-outputs: "Claude automatically receives an
      additional system prompt ... The injected prompt costs you tokens like any other system prompt");
    * the tokenizer is not public, and the token-counting endpoint is documented as an estimate that "might differ
      by a small amount" (build-with-claude/token-counting);
    * the earlier guard (characters / 3) is not a bound: this model's tokenizer produces about 30% more tokens
      than earlier ones for the same text, and short tokens are possible.
    """
    return None


def budget_proof(requests: dict[str, bytes], bound_fn: Callable[[int], Optional[int]] = proven_input_bound,
                 expected_events: int = FROZEN["sample"]["events"]) -> BudgetProof:
    """Holds only if every one of the fixed run's requests exists and has a demonstrated maximum, and together they
    fit the ceiling. A run with missing inputs never "fits" by having fewer requests."""
    ps = FROZEN["price_sheet"]
    pin, pout = ps["input_micro_usd_per_token"], ps["output_micro_usd_per_token"]
    out_max = FROZEN["max_output_tokens"] * pout        # max_tokens is a strict limit that includes all thinking
    per, reasons = {}, []
    byte_total = 0
    for eid, body in requests.items():
        n = len(body)
        b = bound_fn(n)
        byte_total += n * pin + out_max
        per[eid] = {"request_sha256": hashlib.sha256(body).hexdigest(), "request_bytes": n,
                    "input_bound_tokens": b, "max_micro": None if b is None else b * pin + out_max,
                    "over_4500_bytes": n > FROZEN["max_input_tokens"]}
    if len(per) != expected_events:
        reasons.append(f"only {len(per)} of the {expected_events} requests could be built and rendered (missing "
                       "inputs, or an SDK other than the pinned one): the fixed run cannot be priced, so nothing is sent")
    unbounded = [e for e, p in per.items() if p["input_bound_tokens"] is None]
    total = None if unbounded else sum(p["max_micro"] for p in per.values())
    cap = FROZEN["ceiling_micro_usd"]
    if unbounded:
        reasons.append(f"{len(unbounded)} of {len(per)} requests have no demonstrated upper bound on billable input "
                       "tokens (structured-output system prompt of undocumented size; tokenizer not public; token "
                       "counts are estimates)")
    over = [e for e, p in per.items() if p["input_bound_tokens"] is not None
            and p["input_bound_tokens"] > FROZEN["max_input_tokens"]]
    if over:
        reasons.append(f"{len(over)} requests could exceed the registered 4,500 input tokens")
    if total is not None and total > cap:
        reasons.append(f"the whole fixed run could cost up to ${total / 1e6:.2f}, above ${cap / 1e6:.2f}")
    if total is None:
        reasons.append("no separate, verifiable, non-overshooting provider limit at or below $12.50 has been "
                       "demonstrated (a prepaid balance or a workspace limit cannot be read or tested without a key, "
                       "and the plan's $15 balance is above the ceiling)")
    totals = {"events": len(per), "ceiling_micro": cap, "max_total_micro": total,
              "output_max_micro_per_event": out_max,
              "byte_count_illustration_micro": byte_total,
              "byte_count_illustration_note": "if every billable input token were at least one UTF-8 byte of the "
              "request body (an unverified property of a private tokenizer) and the structured-output prompt were "
              "free, the run could still cost up to this much",
              "theoretical_registered_max_micro": len(per) * (FROZEN["max_input_tokens"] * pin + out_max)}
    proven = total is not None and total <= cap and not over and len(per) == expected_events
    return BudgetProof(proven, per, totals, reasons if not proven else [])


# ------------------------------------------------------------------ one generation per event
@dataclass
class CallResult:
    event_id: str
    state: str                 # settled | held
    outcome: str               # ok | refusal | max_tokens | model_mismatch | timeout | ...
    text: Optional[str]
    charge_micro: int
    latency_ms: Optional[int]
    provider_request_id: Optional[str]
    usage: Optional[dict]
    resumed: bool = False


def usage_dict(u) -> Optional[dict]:
    if u is None:
        return None
    d = u.model_dump() if hasattr(u, "model_dump") else dict(u)
    return json.loads(json.dumps(d, default=str))


def check_usage(u: Optional[dict], served: str) -> tuple[str, Optional[int], str]:
    """('ok', charge, '') | ('hold', None, why) | ('stop', None, why)."""
    if u is None:
        return "hold", None, "missing usage"
    tin, tout = u.get("input_tokens"), u.get("output_tokens")
    for name, v in (("input_tokens", tin), ("output_tokens", tout)):
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            return "hold", None, f"usage {name}={v!r} is not a non-negative integer"
    unpriced = []
    for k in ("cache_creation_input_tokens", "cache_read_input_tokens"):
        if u.get(k):
            unpriced.append(f"{k}={u[k]}")
    cc = u.get("cache_creation") or {}
    if any(v for v in cc.values()):
        unpriced.append(f"cache_creation={cc}")
    stu = u.get("server_tool_use") or {}
    if any(v for v in stu.values()):
        unpriced.append(f"server_tool_use={stu}")
    if u.get("service_tier") != "standard":
        unpriced.append(f"service_tier={u.get('service_tier')!r}")
    if u.get("speed") not in (None, "standard"):
        unpriced.append(f"speed={u.get('speed')!r}")
    if u.get("inference_geo") not in (None, "global"):
        unpriced.append(f"inference_geo={u.get('inference_geo')!r}")
    if unpriced:
        return "stop", None, "unpriced charge categories: " + ", ".join(unpriced)
    det = u.get("output_tokens_details") or {}
    if tout > FROZEN["max_output_tokens"] or (det.get("thinking_tokens") or 0) > tout:
        return "stop", None, f"contradictory usage: output {tout}, thinking {det.get('thinking_tokens')}"
    ps = FROZEN["price_sheet"]
    return "ok", tin * ps["input_micro_usd_per_token"] + tout * ps["output_micro_usd_per_token"], ""


class PaidCaller:
    """Sends at most one generation per event through the journal. The caller never retries and never resends."""

    def __init__(self, journal: Journal, client, bound_fn: Callable[[int], Optional[int]],
                 clock: Callable[[], float] = time.monotonic):
        if journal.plan() == {}:
            raise PaidPathRefused("the run is not preauthorized in the journal")
        self.j, self.client, self.bound_fn, self.clock = journal, client, bound_fn, clock

    def _from_journal(self, a) -> CallResult:
        return CallResult(a.event_id, a.state, a.outcome or "", a.answer_text, a.committed_micro, a.latency_ms,
                          a.provider_request_id, json.loads(a.usage_json) if a.usage_json else None, resumed=True)

    def call(self, event_id: str, system: str, user: str, schema: dict) -> CallResult:
        params = request_params(system, user, schema)
        body, _ = render_request(params)
        sha = hashlib.sha256(body).hexdigest()
        a = self.j.get(event_id)
        if a is not None and a.state in ("settled", "held"):
            if a.request_sha256 != sha:
                raise JournalError(f"{event_id}: the rebuilt request differs from the one already paid for")
            return self._from_journal(a)                       # resume: never resend
        bound_tokens = self.bound_fn(len(body))
        if bound_tokens is None:
            raise PaidPathRefused("no demonstrated upper bound on this request's input tokens: not sent")
        ps = FROZEN["price_sheet"]
        bound = bound_tokens * ps["input_micro_usd_per_token"] + \
            FROZEN["max_output_tokens"] * ps["output_micro_usd_per_token"]
        if a is None:
            self.j.reserve(event_id, sha, len(body), bound)
        elif a.request_sha256 != sha:
            raise JournalError(f"{event_id}: the rebuilt request differs from the one reserved")
        self.j.mark_dispatched(event_id, sha)
        t0 = self.clock()
        try:
            raw = self.client.messages.with_raw_response.create(**params)
            msg = raw.parse()
            rid = raw.headers.get("request-id") or getattr(msg, "_request_id", None)
        except Exception as exc:                                # the server may have accepted the call: hold
            ms = int((self.clock() - t0) * 1000)
            rid = getattr(exc, "request_id", None)
            kind = type(exc).__name__
            outcome = "timeout" if "Timeout" in kind else ("api_error" if hasattr(exc, "status_code") else "transport_error")
            self.j.hold(event_id, outcome, f"{kind}: {str(exc)[:300]}", latency_ms=ms, provider_request_id=rid)
            return _fresh(self._from_journal(self.j.get(event_id)))
        ms = int((self.clock() - t0) * 1000)
        served, stop = getattr(msg, "model", None), getattr(msg, "stop_reason", None)
        u = usage_dict(getattr(msg, "usage", None))
        text = next((b.text for b in (msg.content or []) if getattr(b, "type", "") == "text"), None)
        verdict, charge, why = check_usage(u, served)
        common = dict(latency_ms=ms, provider_request_id=rid, served_model=served, stop_reason=stop, usage=u,
                      answer_text=text)
        if verdict == "stop":
            self.j.hold(event_id, "unpriced_or_contradictory_usage", why, **common)
            raise CriticalStop(f"{event_id}: {why}")
        if verdict == "hold":
            self.j.hold(event_id, "missing_or_invalid_usage", why, **common)
        elif served != FROZEN["model"]:
            self.j.hold(event_id, "model_mismatch", f"served by {served}", **common)
        elif stop == "refusal":
            self.j.hold(event_id, "refusal", "the model declined", **common)
        elif stop == "max_tokens":
            self.j.hold(event_id, "max_tokens", "the answer was cut off at max_tokens", **common)
        elif stop != "end_turn" or text is None:
            self.j.hold(event_id, "incomplete", f"stop_reason {stop!r}, text {'present' if text else 'absent'}", **common)
        elif charge > bound:
            self.j.hold(event_id, "contradictory_usage", f"charge {charge} above the reserved maximum {bound}", **common)
            raise CriticalStop(f"{event_id}: usage charge above the reserved maximum")
        else:
            self.j.settle(event_id, charge, latency_ms=ms, provider_request_id=rid, served_model=served,
                          stop_reason=stop, usage=u, answer_text=text)
        return _fresh(self._from_journal(self.j.get(event_id)))


def _fresh(r: CallResult) -> CallResult:
    r.resumed = False
    return r
