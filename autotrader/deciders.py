"""Deciders: the autonomous researcher (a language model) and the simpler methods it must beat.

* no_trade     never trades. After costs it is the hardest benchmark: it loses nothing.
* rule_p4h1    the phase-4 rule (EPS beat + sales beat + positive reaction by 10:00). Already REJECTED; kept as
               the "conventional indicator" baseline.
* price_only   buys any event stock that is up relative to its sector by 10:00, ignoring the news.
* llm          reads the evidence pack and decides freely: the stock, its sector ETF, or nothing. It must quote
               its evidence, separate facts from interpretation and uncertainty, say what is priced in and what
               would invalidate the thesis, and weigh reward against costs. Any invalid or ungrounded answer, and
               any model failure, is recorded and becomes "no trade".
None of these sets a position size: the risk controller does.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from .decision import (JSON_SCHEMA, json_schema_v2, no_trade, parse_ai_decision, parse_ai_decision_v2, prompt_text_v2,
                       sentences, trade)
from .evidence import EvidencePack

DEFAULT_ENTRY = {"max_spread_bps": 30.0, "min_move_pct": None, "max_move_pct": None}
DEFAULT_EXIT = {"stop_pct": 3.0, "target_pct": None, "exit_time": "15:45"}


class ModelFailure(RuntimeError):
    """The model could not be reached or did not answer (distinct from an invalid answer)."""


class NoTrade:
    name = "no_trade"
    version = "1"

    def decide(self, pack: EvidencePack) -> dict:
        return no_trade(self.name, "benchmark: never trades")


class RuleP4H1:
    name = "rule_p4h1"
    version = "1"

    def decide(self, pack: EvidencePack) -> dict:
        s = pack.structured
        sur = s.get("surprise") or {}
        if not sur:
            return no_trade(self.name, "no consensus comparison available (needs a vendor earnings flash)")
        if sur.get("eps") == "beat" and sur.get("sales") == "beat" and (s.get("react") or 0) > 0:
            return trade(self.name, pack.symbol, DEFAULT_ENTRY, DEFAULT_EXIT,
                         "EPS and sales beat, and the stock is beating its sector by 10:00",
                         ["this rule was rejected in phase 4 (no edge after costs)"])
        return no_trade(self.name, "rule conditions not met")


class PriceOnly:
    name = "price_only"
    version = "1"

    def decide(self, pack: EvidencePack) -> dict:
        r = pack.structured.get("react")
        if r is None:
            return no_trade(self.name, "no price reaction available")
        if r > 0:
            return trade(self.name, pack.symbol, DEFAULT_ENTRY, DEFAULT_EXIT,
                         "the stock is beating its sector by 10:00 (price only, news ignored)",
                         ["price-only continuation was no better than chance in phase 4"])
        return no_trade(self.name, "not beating its sector by 10:00")


SYSTEM = """You are an autonomous, disciplined stock trader for a small account. You investigate one market event at a time and decide whether to place a long trade today or do nothing. Doing nothing is often correct: most events offer no edge after trading costs.

Use ONLY the evidence provided. Never invent news, numbers, prices or results.
1. facts: each fact must copy an exact phrase from one evidence item (field "quote", copied word for word) and give that item's id (e.g. "E2"). The "claim" may only restate numbers that appear in its quote.
2. Keep facts, interpretations and uncertainties separate.
3. Work through: what happened; why the price might not yet reflect it; what the market has already priced in (the price move so far is evidence of that); what would invalidate the idea; whether the expected gain beats the round-trip cost and the risk of the stop.
4. decision: "long" (buy the event's stock, or its sector ETF, as "instrument") or "no_trade". Shorting is not allowed.
5. You do not choose position size or risk limits; an independent controller does and may reject your trade.
6. exit.stop_pct is the stop distance in percent (the controller allows 0.4 to 3). exit.exit_time must be 15:45 or earlier, as HH:MM. entry.max_spread_bps is the widest bid-ask spread you will accept at entry.
7. p_up_by_close: your probability that the stock is higher at 15:45 than at your entry. expected_net_return_pct: your expected return after costs if you trade.
8. reported: the GAAP diluted EPS and revenue (in millions of dollars) stated in the company's own release, or null if not stated.
9. If you trade, give at least one reason to avoid the trade and at least one invalidation condition.
10. Be concise: at most 4 facts, each quote under 30 words; at most 3 items in each list; rationale under 80 words.
Answer with JSON only."""


class LLMResearcher:
    name = "llm"
    version = "1"

    def __init__(self, provider, max_tokens: int = 1600):
        self.provider = provider
        self.max_tokens = max_tokens

    @property
    def model_id(self) -> str:
        return f"{getattr(self.provider, 'name', '?')}:{getattr(self.provider, 'model', '?')}"

    def decide(self, pack: EvidencePack) -> dict:
        user = pack.prompt_text() + "\n\nInvestigate this event and decide now. JSON only."
        meta = {"model": self.model_id, "prompt_sha256": hashlib.sha256((SYSTEM + user).encode()).hexdigest()}
        try:
            res = self.provider.complete_json(SYSTEM, user, JSON_SCHEMA, max_tokens=self.max_tokens)
        except Exception as exc:                                  # unreachable, timeout, budget, refusal ...
            raise ModelFailure(f"{type(exc).__name__}: {exc}") from exc
        meta |= {"latency_ms": res.latency_ms, "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
                 "cost_usd": res.cost_usd, "raw_sha256": hashlib.sha256(res.text.encode()).hexdigest(),
                 "raw": res.text[:6000]}
        d, problems = parse_ai_decision(res.text, pack)
        if d is None or problems:
            return no_trade(self.name, "invalid or ungrounded model answer: treated as no trade", invalid=True,
                            problems=problems, model=meta,
                            p_up_by_close=d.p_up_by_close if d else None)
        thesis = {"facts": [f.model_dump() for f in d.facts], "interpretations": d.interpretations,
                  "uncertainties": d.uncertainties, "priced_in": d.priced_in, "invalidation": d.invalidation,
                  "confidence": d.confidence, "reported": d.reported.model_dump()}
        common = {"thesis": thesis, "model": meta, "p_up_by_close": d.p_up_by_close,
                  "expected_net_return_pct": d.expected_net_return_pct}
        if d.decision == "no_trade":
            return no_trade(self.name, d.rationale, **common) | {"reasons_to_avoid": d.reasons_to_avoid or [d.rationale]}
        return trade(self.name, d.instrument.upper(), d.entry.model_dump(), d.exit.model_dump(), d.rationale,
                     d.reasons_to_avoid, **common)


SYSTEM_V2 = """You are an autonomous, disciplined stock trader for a small account. You investigate one market event at a time and decide whether to place a long trade today or do nothing. Doing nothing is often correct: most events offer no edge after trading costs.

The evidence is given as numbered sentences such as [E1.S3]. Use ONLY that evidence. Never invent news, numbers, prices or results.
1. facts: each fact cites 1 to 3 sentence ids in "refs" and states a short "claim". Every number in a claim must be written exactly as it appears in the cited sentences. Do not compute new numbers in facts; put calculations in interpretations.
2. Keep facts, interpretations and uncertainties separate.
3. Work through: what happened; why the price might not yet reflect it; what the market has already priced in (the price move so far is evidence of that); what would invalidate the idea; whether the expected gain beats the round-trip cost and the risk of the stop.
4. decision: "long" or "no_trade". Shorting is not allowed. For "long", fill "trade" (instrument: the event's stock or its sector ETF); for "no_trade", set "trade" to null.
5. You do not choose position size or risk limits; an independent controller does and may reject your trade.
6. reported: only figures stated in the company's own release (E-ids of kind filing). Cite the sentence, copy the value exactly as written there, and give its unit (usd_per_share for EPS; usd_millions or usd_billions for revenue as written). Leave the list empty if the release states none.
7. p_up_pct: your probability (0-100) that the stock is higher at 15:45 than at entry. expected_net_return_bps: expected return after costs in basis points if you trade.
8. Be brief. Answer with JSON only."""


class LLMResearcherV2(LLMResearcher):
    """Contract v2: facts cite numbered sentences instead of copying text; trade fields are fixed choices."""
    version = "2"

    def decide(self, pack: EvidencePack) -> dict:
        sents = sentences(pack)
        user = prompt_text_v2(pack, sents) + "\n\nInvestigate this event and decide now. JSON only."
        meta = {"model": self.model_id, "contract": "2",
                "prompt_sha256": hashlib.sha256((SYSTEM_V2 + user).encode()).hexdigest()}
        try:
            res = self.provider.complete_json(SYSTEM_V2, user, json_schema_v2(pack, sents), max_tokens=self.max_tokens)
        except Exception as exc:
            raise ModelFailure(f"{type(exc).__name__}: {exc}") from exc
        meta |= {"latency_ms": res.latency_ms, "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
                 "cost_usd": res.cost_usd, "raw_sha256": hashlib.sha256(res.text.encode()).hexdigest(),
                 "raw": res.text[:6000]}
        d, problems, sents = parse_ai_decision_v2(res.text, pack, sents)
        if d is None or problems:
            return no_trade(self.name, "invalid or ungrounded model answer: treated as no trade", invalid=True,
                            problems=problems, model=meta, p_up_by_close=(d.p_up_pct / 100 if d else None))
        facts = [{"refs": f.refs, "claim": f.claim, "evidence": {r: sents[r] for r in f.refs}} for f in d.facts]
        rep = {"eps_diluted": None, "revenue_usd_millions": None}
        for r in d.reported:
            if r.metric == "gaap_diluted_eps":
                rep["eps_diluted"] = r.value
            else:
                rep["revenue_usd_millions"] = r.value * (1000 if r.unit == "usd_billions" else 1)
        thesis = {"facts": facts, "interpretations": d.interpretations, "uncertainties": d.uncertainties,
                  "priced_in": d.priced_in, "invalidation": d.invalidation, "confidence": d.confidence,
                  "reported": rep, "reported_refs": [r.model_dump() for r in d.reported]}
        common = {"thesis": thesis, "model": meta, "p_up_by_close": d.p_up_pct / 100,
                  "expected_net_return_pct": d.expected_net_return_bps / 100}
        if d.decision == "no_trade":
            return no_trade(self.name, d.rationale, **common) | {"reasons_to_avoid": d.reasons_to_avoid or [d.rationale]}
        t = d.trade
        return trade(self.name, t.instrument.upper(),
                     {"max_spread_bps": float(t.max_spread_bps), "min_move_pct": t.min_move_pct, "max_move_pct": t.max_move_pct},
                     {"stop_pct": t.stop_pct, "target_pct": t.target_pct, "exit_time": t.exit_time},
                     d.rationale, d.reasons_to_avoid, **common)


class RecordedProvider:
    """Replays recorded model answers by prompt hash: makes an LLM run reproducible and tests deterministic."""
    name = "recorded"
    is_paid = False

    def __init__(self, answers: dict[str, str], model: str = "recorded"):
        self.answers, self.model = answers, model

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int = 1200):
        from stockpilot.ai.providers import ProviderResult
        key = hashlib.sha256((system + user).encode()).hexdigest()
        if key not in self.answers:
            raise ModelFailure("no recorded answer for this prompt")
        return ProviderResult(self.answers[key], 0, 0, 0.0, 0, self.name, self.model)


def baselines() -> list:
    return [NoTrade(), RuleP4H1(), PriceOnly()]
