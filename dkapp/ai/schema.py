"""Structured AI responses and their programmatic validation.

The JSON schemas sent to models are intentionally simple (types, enums, required
fields) so both Ollama and Anthropic structured outputs accept them. Numeric ranges
and lengths are then enforced here with pydantic — anything that fails is discarded
and the trade is NOT taken.
"""
from __future__ import annotations

import json
import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class EntryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["buy", "skip"]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_quality: Literal["strong", "moderate", "weak", "insufficient"]
    stop_loss_pct: float = Field(gt=0.0, le=20.0)
    take_profit_pct: float = Field(gt=0.0, le=50.0)
    max_hold_minutes: int = Field(ge=1, le=390)
    reasoning: str = Field(min_length=10, max_length=1500)
    key_risks: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("key_risks")
    @classmethod
    def _short_risks(cls, v: list[str]) -> list[str]:
        return [s.strip()[:200] for s in v if s and s.strip()]


class ExitReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["hold", "sell"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=5, max_length=1000)


ENTRY_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["buy", "skip"]},
        "confidence": {"type": "number", "description": "0 to 1"},
        "evidence_quality": {"type": "string", "enum": ["strong", "moderate", "weak", "insufficient"]},
        "stop_loss_pct": {"type": "number", "description": "percent below entry, e.g. 1.2"},
        "take_profit_pct": {"type": "number", "description": "percent above entry, e.g. 2.0"},
        "max_hold_minutes": {"type": "integer"},
        "reasoning": {"type": "string", "description": "2-4 plain-English sentences a beginner can follow"},
        "key_risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["action", "confidence", "evidence_quality", "stop_loss_pct", "take_profit_pct",
                 "max_hold_minutes", "reasoning", "key_risks"],
    "additionalProperties": False,
}

EXIT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["hold", "sell"]},
        "confidence": {"type": "number", "description": "0 to 1"},
        "reasoning": {"type": "string"},
    },
    "required": ["action", "confidence", "reasoning"],
    "additionalProperties": False,
}

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    t = _FENCE.sub("", text.strip())
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    while start != -1:
        depth = 0
        for k in range(start, len(t)):
            if t[k] == "{":
                depth += 1
            elif t[k] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(t[start:k + 1])
                        return obj if isinstance(obj, dict) else None
                    except json.JSONDecodeError:
                        break
        start = t.find("{", start + 1)
    return None


def parse_entry(text: str) -> tuple[Optional[EntryDecision], Optional[str]]:
    obj = extract_json(text)
    if obj is None:
        return None, "response was not valid JSON"
    try:
        return EntryDecision.model_validate(obj), None
    except ValidationError as exc:
        return None, "schema validation failed: " + "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])


def parse_exit(text: str) -> tuple[Optional[ExitReview], Optional[str]]:
    obj = extract_json(text)
    if obj is None:
        return None, "response was not valid JSON"
    try:
        return ExitReview.model_validate(obj), None
    except ValidationError as exc:
        return None, "schema validation failed: " + "; ".join(e["msg"] for e in exc.errors()[:5])
