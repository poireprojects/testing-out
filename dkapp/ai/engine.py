"""AI decision engine.

The quantitative scanner and strategies do the cheap, continuous work. The model is
only consulted for a shortlisted entry (and periodic reviews of open positions).
Calls run on a background worker so price monitoring and stops never wait for a slow
model. Any failure — offline, timeout, invalid JSON, schema violation, budget cap —
results in NO trade.
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .. import credentials
from ..clock import ET
from ..config import AppSettings
from ..models import Signal
from ..storage.db import Database, to_iso
from ..strategies import ALL_STRATEGIES
from .providers import AIProvider, AIUnavailable, AnthropicProvider, DisabledProvider, OllamaProvider
from .schema import (ENTRY_SCHEMA, EXIT_SCHEMA, EntryDecision, ExitReview, SelectDecision, parse_entry, parse_exit,
                     parse_select, select_schema)

log = logging.getLogger(__name__)

from .consistency import Facts, check_explanation, derived_facts  # noqa: E402

ENTRY_SYSTEM = """You are the trade-review step inside a personal, automated day-trading app.
A rules-based strategy has flagged a possible LONG (buy) day trade. Decide whether the evidence justifies taking it.

Rules you must follow:
- Long only. The position will be closed before today's market close.
- Most flagged setups should be skipped. Choose "buy" only when several independent pieces of evidence agree,
  the stop is sensible, and the potential gain clearly outweighs the risk after costs.
- If data is missing, stale, conflicting, or the move already looks exhausted, choose "skip".
- Use only the numbers provided. Do not invent news, earnings, or prices.
- You do not control position size or risk limits; a separate deterministic risk engine enforces them and may
  still block the trade.
- stop_loss_pct and take_profit_pct are percentages from the entry price (for example 1.2 means 1.2%).
- "computed_by_app" holds numbers the app calculated (stop and target distance, reward/risk after costs, costs,
  RSI and what its level means). Quote those exactly; never recalculate them. An explanation that contradicts
  them is rejected and the trade is skipped.
- Write "reasoning" for a beginner: 2-4 short sentences, no jargon without a brief explanation.
Return only the JSON object described by the schema."""

SELECT_SYSTEM = """You are the decision step inside a personal, automated day-trading app.
You are shown a short, ranked list of possible LONG (buy) day trades that already passed the app's rule checks,
plus the current market conditions. Pick the single best one, or "none".

Rules you must follow:
- "none" is often the right answer. Pick a trade only if it clearly beats doing nothing after costs.
- Compare the options against each other: strength of evidence, risk vs. reward after costs, liquidity, and how well
  the setup suits today's market conditions.
- Positions are closed before today's market close. You do not control size or risk limits; a separate
  deterministic risk engine enforces them and may still block the trade.
- Use only the numbers provided. Do not invent news, earnings, or prices.
- Write "reasoning" for a beginner: 2-4 short sentences that say why the pick beats the others (or why none do).
Return only the JSON object described by the schema."""

EXIT_SYSTEM = """You review an open intraday LONG position inside an automated day-trading app.
Decide whether to keep holding or sell now. Selling only reduces risk. Prefer "hold" unless the evidence has
clearly turned against the position (trend broken, momentum gone, price losing key levels).
Use only the numbers provided. Return only the JSON object described by the schema."""


@dataclass
class AIResult:
    kind: str
    symbol: str
    ok: bool
    decision: Optional[EntryDecision] = None
    review: Optional[ExitReview] = None
    selection: Optional[SelectDecision] = None
    error: Optional[str] = None
    provider: str = ""
    model: str = ""
    cost_usd: float = 0.0
    latency_ms: int = 0
    decision_id: Optional[int] = None
    signal: Optional[Signal] = None
    requested_at: Optional[datetime] = None
    approved: bool = False
    verdict_text: str = ""
    extra: dict = field(default_factory=dict)


def build_provider(settings: AppSettings) -> AIProvider:
    ai = settings.ai
    if ai.provider == "anthropic":
        key = credentials.load_anthropic_key()
        if not key:
            return DisabledProvider("no Anthropic API key saved")
        return AnthropicProvider(key, ai.anthropic_model, ai.anthropic_effort, ai.timeout_seconds)
    return OllamaProvider(ai.ollama_url, ai.ollama_model, ai.timeout_seconds)


class AIEngine:
    def __init__(self, settings: AppSettings, db: Database, provider: Optional[AIProvider] = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.settings = settings
        self.db = db
        self._now = now
        self.provider: AIProvider = provider or build_provider(settings)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dk-ai")
        self._lock = threading.Lock()
        self._last_asked: dict[tuple[str, str], datetime] = {}
        self.healthy: Optional[bool] = None
        self.health_msg = "not checked yet"
        self.last_health_check: Optional[datetime] = None
        self.busy = False

    # ------------------------------------------------------------ status
    def set_provider(self, provider: AIProvider) -> None:
        self.provider = provider
        self.healthy, self.health_msg, self.last_health_check = None, "not checked yet", None

    def check_health(self) -> tuple[bool, str]:
        try:
            ok, msg = self.provider.health()
        except Exception as exc:  # pragma: no cover - defensive
            ok, msg = False, f"health check failed: {exc}"
        self.healthy, self.health_msg, self.last_health_check = ok, msg, self._now()
        return ok, msg

    def month_cost(self) -> float:
        now = self._now().astimezone(ET)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        return float(self.db.scalar("SELECT SUM(cost_usd) FROM ai_decisions WHERE time >= ?", (to_iso(start),), 0.0))

    def calls_last_hour(self) -> int:
        since = to_iso(self._now() - timedelta(hours=1))
        return int(self.db.scalar("SELECT COUNT(*) FROM ai_decisions WHERE time >= ? AND provider != 'none'", (since,), 0))

    def can_call(self) -> tuple[bool, str]:
        ai = self.settings.ai
        if ai.decision_mode == "rules":
            return False, "rules-only mode (AI not consulted)"
        if self.healthy is False:
            return False, self.health_msg
        if getattr(self.provider, "is_paid", False) and self.month_cost() >= ai.monthly_budget_usd:
            return False, f"monthly cloud AI budget of ${ai.monthly_budget_usd:.2f} reached"
        if self.calls_last_hour() >= ai.max_calls_per_hour:
            return False, f"AI call limit ({ai.max_calls_per_hour}/hour) reached"
        return True, ""

    def recently_asked(self, symbol: str, strategy: str) -> bool:
        t = self._last_asked.get((symbol, strategy))
        return t is not None and self._now() - t < timedelta(minutes=self.settings.ai.recheck_cooldown_minutes)

    # ------------------------------------------------------------ entries
    def submit_entry(self, signal: Signal, context: dict) -> Future:
        self._last_asked[(signal.symbol, signal.strategy)] = self._now()
        return self._pool.submit(self.evaluate_entry, signal, context)

    def evaluate_entry(self, signal: Signal, context: dict) -> AIResult:
        requested = self._now()
        user = json.dumps(build_entry_prompt(signal, context), default=str)
        res = AIResult("entry", signal.symbol, ok=False, signal=signal, requested_at=requested,
                       provider=self.provider.name, model=self.provider.model)
        raw, tin, tout = "", 0, 0
        self.busy = True
        try:
            pr = self.provider.complete_json(ENTRY_SYSTEM, user, ENTRY_SCHEMA)
            raw, tin, tout = pr.text, pr.input_tokens, pr.output_tokens
            res.cost_usd, res.latency_ms, res.model = pr.cost_usd, pr.latency_ms, pr.model
            decision, err = parse_entry(pr.text)
            if decision is None:
                res.error = err
            else:
                self.healthy, self.health_msg = True, f"{pr.model} responding"
                chk = check_explanation(decision.reasoning, Facts.from_signal(signal, context.get("round_trip_cost_pct")))
                if chk.ok:
                    res.ok, res.decision = True, decision
                else:  # numbers the app computed are facts; an answer that contradicts them is unusable
                    res.decision = None
                    res.error = "explanation contradicts the app's numbers: " + "; ".join(chk.problems)
                    res.extra["rejected_reasoning"] = decision.reasoning
        except AIUnavailable as exc:
            res.error = str(exc)
            if "budget" not in str(exc):
                self.healthy, self.health_msg = False, str(exc)
        except Exception as exc:  # never let a provider bug crash trading
            log.exception("AI provider crashed")
            res.error = f"unexpected AI error: {exc}"
        finally:
            self.busy = False

        ai = self.settings.ai
        if res.ok and res.decision is not None:
            d = res.decision
            strong_enough = d.evidence_quality in ("strong", "moderate")
            res.approved = d.action == "buy" and d.confidence >= ai.min_confidence and strong_enough
            if d.action == "buy" and not res.approved:
                res.verdict_text = (f"AI leaned buy but confidence {d.confidence:.0%} / evidence '{d.evidence_quality}' "
                                    f"is below the bar — skipped")
            else:
                res.verdict_text = "AI approved" if res.approved else "AI skipped"
        else:
            res.verdict_text = f"AI unavailable — no trade ({res.error})"
        res.decision_id = self.db.insert(
            "ai_decisions", time=to_iso(requested), kind="entry", symbol=signal.symbol, strategy=signal.strategy,
            provider=res.provider, model=res.model,
            action=(res.decision.action if res.decision else "error"),
            confidence=(res.decision.confidence if res.decision else None),
            evidence_quality=(res.decision.evidence_quality if res.decision else None),
            reasoning=(res.decision.reasoning if res.decision else res.error),
            risks=json.dumps(res.decision.key_risks if res.decision else []),
            valid=int(res.ok), error=res.error, raw=raw[:4000], input_tokens=tin, output_tokens=tout,
            cost_usd=res.cost_usd, latency_ms=res.latency_ms, signal_id=context.get("signal_id"))
        return res

    # ------------------------------------------------------------ choose among candidates
    def submit_selection(self, options: list[dict], context: dict) -> Future:
        for o in options:
            self._last_asked[(o["symbol"], o["strategyId"])] = self._now()
        return self._pool.submit(self.evaluate_selection, options, context)

    def evaluate_selection(self, options: list[dict], context: dict) -> AIResult:
        requested = self._now()
        symbols = [o["symbol"] for o in options]
        res = AIResult("select", ",".join(symbols), ok=False, requested_at=requested, provider=self.provider.name,
                       model=self.provider.model, extra={"options": symbols})
        raw, tin, tout = "", 0, 0
        self.busy = True
        try:
            pr = self.provider.complete_json(SELECT_SYSTEM, json.dumps({"options": options, **context}, default=str),
                                             select_schema(symbols))
            raw, tin, tout = pr.text, pr.input_tokens, pr.output_tokens
            res.cost_usd, res.latency_ms, res.model = pr.cost_usd, pr.latency_ms, pr.model
            sel, err = parse_select(pr.text, symbols)
            res.ok, res.selection, res.error = sel is not None, sel, err
            if sel is not None:
                self.healthy, self.health_msg = True, f"{pr.model} responding"
        except AIUnavailable as exc:
            res.error = str(exc)
            if "budget" not in str(exc):
                self.healthy, self.health_msg = False, str(exc)
        except Exception as exc:
            log.exception("AI provider crashed during selection")
            res.error = f"unexpected AI error: {exc}"
        finally:
            self.busy = False
        sel = res.selection
        res.approved = bool(res.ok and sel and sel.choice != "none")
        res.verdict_text = (f"AI picked {sel.choice}" if res.approved else "AI chose not to trade") if res.ok \
            else f"AI unavailable — no trade ({res.error})"
        if sel is not None:
            res.extra["reasoning"] = sel.reasoning
        res.decision_id = self.db.insert(
            "ai_decisions", time=to_iso(requested), kind="select", symbol=(sel.choice if sel else ",".join(symbols)),
            strategy=None, provider=res.provider, model=res.model,
            action=("buy" if res.approved else ("skip" if res.ok else "error")),
            confidence=(sel.confidence if sel else None), reasoning=(sel.reasoning if sel else res.error),
            risks=json.dumps(sel.key_risks if sel else []), valid=int(res.ok), error=res.error, raw=raw[:4000],
            input_tokens=tin, output_tokens=tout, cost_usd=res.cost_usd, latency_ms=res.latency_ms)
        return res

    # ------------------------------------------------------------ exits
    def submit_exit_review(self, symbol: str, context: dict) -> Future:
        return self._pool.submit(self.evaluate_exit, symbol, context)

    def evaluate_exit(self, symbol: str, context: dict) -> AIResult:
        requested = self._now()
        res = AIResult("exit_review", symbol, ok=False, requested_at=requested, provider=self.provider.name,
                       model=self.provider.model, extra={"trade_id": context.get("trade_id")})
        raw, tin, tout = "", 0, 0
        try:
            pr = self.provider.complete_json(EXIT_SYSTEM, json.dumps(context, default=str), EXIT_SCHEMA, 400)
            raw, tin, tout = pr.text, pr.input_tokens, pr.output_tokens
            res.cost_usd, res.latency_ms, res.model = pr.cost_usd, pr.latency_ms, pr.model
            review, err = parse_exit(pr.text)
            res.ok, res.review, res.error = review is not None, review, err
        except AIUnavailable as exc:
            res.error = str(exc)
        except Exception as exc:
            log.exception("AI provider crashed during exit review")
            res.error = f"unexpected AI error: {exc}"
        res.decision_id = self.db.insert(
            "ai_decisions", time=to_iso(requested), kind="exit_review", symbol=symbol, strategy=context.get("strategy"),
            provider=res.provider, model=res.model, action=(res.review.action if res.review else "error"),
            confidence=(res.review.confidence if res.review else None),
            reasoning=(res.review.reasoning if res.review else res.error), risks="[]", valid=int(res.ok),
            error=res.error, raw=raw[:2000], input_tokens=tin, output_tokens=tout, cost_usd=res.cost_usd,
            latency_ms=res.latency_ms)
        return res

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def build_entry_prompt(signal: Signal, context: dict) -> dict:
    strat = ALL_STRATEGIES.get(signal.strategy)
    return {
        "symbol": signal.symbol,
        "time_et": signal.time.astimezone(ET).strftime("%Y-%m-%d %H:%M"),
        "strategy": {
            "name": strat.name if strat else signal.strategy,
            "how_it_works": strat.summary if strat else "",
            "why_it_triggered": signal.reasons,
            "signal_strength_0_to_1": round(signal.score, 2),
        },
        "proposed_trade": {
            "entry_price": round(signal.price, 4),
            "stop_price": round(signal.stop_price, 4),
            "stop_loss_pct": round(signal.stop_pct, 2),
            "target_price": round(signal.target_price, 4),
            "take_profit_pct": round(signal.target_pct, 2),
            "max_hold_minutes": signal.max_hold_minutes,
            "estimated_round_trip_cost_pct": context.get("round_trip_cost_pct"),
        },
        "computed_by_app": derived_facts(signal, context.get("round_trip_cost_pct")),
        "indicators": signal.features,
        "recent_1min_bars": context.get("recent_bars", []),
        "market": context.get("market", {}),
        "account": context.get("account", {}),
        "data_note": context.get("data_note", ""),
    }
