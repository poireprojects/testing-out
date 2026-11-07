"""The structured decision every decider returns, the JSON schema a language model must fill, and the checks that
make a model's answer usable: schema validity, then grounding (every fact quotes its evidence verbatim, every
number it states appears in that quote, every reported financial figure appears in the evidence). A response that
fails any check is recorded as invalid and becomes "no trade": the safe default."""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .evidence import EvidencePack


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fact(_Strict):
    evidence_id: str = Field(max_length=8)
    quote: str = Field(min_length=8, max_length=400)
    claim: str = Field(min_length=3, max_length=300)


class Entry(_Strict):
    max_spread_bps: float = Field(ge=0, le=100)
    min_move_pct: Optional[float] = Field(None, ge=-30, le=30)     # move since the prior close, checked at entry
    max_move_pct: Optional[float] = Field(None, ge=-30, le=30)


class Exit(_Strict):
    stop_pct: float = Field(ge=0, le=20)
    target_pct: Optional[float] = Field(None, ge=0, le=50)
    exit_time: str = Field(pattern=r"^\d{2}:\d{2}$")


class Reported(_Strict):
    eps_diluted: Optional[float] = None
    revenue_usd_millions: Optional[float] = None


class AIDecision(_Strict):
    facts: list[Fact] = Field(min_length=1, max_length=8)
    interpretations: list[str] = Field(max_length=6)
    uncertainties: list[str] = Field(max_length=6)
    priced_in: str = Field(max_length=600)
    reported: Reported
    p_up_by_close: float = Field(ge=0, le=1)
    expected_net_return_pct: float = Field(ge=-20, le=20)
    decision: Literal["long", "no_trade"]
    instrument: str = Field(max_length=10)
    entry: Entry
    exit: Exit
    invalidation: list[str] = Field(max_length=6)
    reasons_to_avoid: list[str] = Field(max_length=6)
    rationale: str = Field(min_length=10, max_length=1200)
    confidence: Literal["low", "medium", "high"]

    @model_validator(mode="after")
    def _trade_fields(self):
        """Entry and exit only matter for a trade; a no-trade answer may leave them at zero."""
        if self.exit.target_pct == 0:
            self.exit.target_pct = None
        if self.decision == "long":
            if self.entry.max_spread_bps < 1:
                raise ValueError("a trade needs entry.max_spread_bps of at least 1")
            if self.exit.stop_pct < 0.1:
                raise ValueError("a trade needs a stop (exit.stop_pct of at least 0.1)")
        return self


def _num():
    return {"anyOf": [{"type": "number"}, {"type": "null"}]}


_STR_LIST = {"type": "array", "items": {"type": "string"}}
JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "items": {"type": "object", "properties": {
            "evidence_id": {"type": "string"}, "quote": {"type": "string"}, "claim": {"type": "string"}},
            "required": ["evidence_id", "quote", "claim"]}},
        "interpretations": _STR_LIST, "uncertainties": _STR_LIST,
        "priced_in": {"type": "string"},
        "reported": {"type": "object", "properties": {"eps_diluted": _num(), "revenue_usd_millions": _num()},
                     "required": ["eps_diluted", "revenue_usd_millions"]},
        "p_up_by_close": {"type": "number"}, "expected_net_return_pct": {"type": "number"},
        "decision": {"type": "string", "enum": ["long", "no_trade"]},
        "instrument": {"type": "string"},
        "entry": {"type": "object", "properties": {"max_spread_bps": {"type": "number"}, "min_move_pct": _num(),
                                                   "max_move_pct": _num()},
                  "required": ["max_spread_bps", "min_move_pct", "max_move_pct"]},
        "exit": {"type": "object", "properties": {"stop_pct": {"type": "number"}, "target_pct": _num(),
                                                  "exit_time": {"type": "string"}},
                 "required": ["stop_pct", "target_pct", "exit_time"]},
        "invalidation": _STR_LIST, "reasons_to_avoid": _STR_LIST,
        "rationale": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["facts", "interpretations", "uncertainties", "priced_in", "reported", "p_up_by_close",
                 "expected_net_return_pct", "decision", "instrument", "entry", "exit", "invalidation",
                 "reasons_to_avoid", "rationale", "confidence"],
}

# ------------------------------------------------------------------ grounding
_TRANS = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                        " ": " "})
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.translate(_TRANS)).strip().lower()


def _numbers(s: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(s.translate(_TRANS))}


def _strip0(s: str) -> str:
    return s.rstrip("0").rstrip(".") if "." in s else s


def reported_in_text(x: float, corpus: str, millions: bool) -> bool:
    """A reported figure must appear as written: EPS as an exact number token (1.30 or 1.3), revenue next to
    'million' or 'billion' ($4,527 million, 4527 million, $4.5 billion)."""
    t = _norm(corpus)
    if not millions:
        toks = {n.replace(",", "") for n in _NUM.findall(t)}
        return bool({f"{abs(x):.2f}", _strip0(f"{abs(x):.2f}")} & toks)
    forms = {f"{x:,.0f} million", f"{x:.0f} million"}
    for d in (1, 2, 3):
        forms |= {f"{x / 1000:.{d}f} billion", _strip0(f"{x / 1000:.{d}f}") + " billion"}
    return any(re.search(r"(?<![\d.,])" + re.escape(f), t) for f in forms)


def grounding_problems(d: AIDecision, pack: EvidencePack) -> list[str]:
    probs: list[str] = []
    for i, f in enumerate(d.facts):
        e = pack.get(f.evidence_id)
        if e is None:
            probs.append(f"fact {i + 1} cites unknown evidence {f.evidence_id!r}")
            continue
        if _norm(f.quote) not in _norm(e.text):
            probs.append(f"fact {i + 1}: quote not found verbatim in {f.evidence_id}")
            continue
        extra = _numbers(f.claim) - _numbers(f.quote)
        if extra:
            probs.append(f"fact {i + 1}: claim states numbers not in its quote ({', '.join(sorted(extra))})")
    corpus = " ".join(e.text for e in pack.items if e.kind in ("filing", "vendor_headline"))
    for name, v, mil in (("eps_diluted", d.reported.eps_diluted, False),
                         ("revenue_usd_millions", d.reported.revenue_usd_millions, True)):
        if v is not None and not reported_in_text(v, corpus, mil):
            probs.append(f"reported {name}={v:g} does not appear in the evidence")
    if d.decision == "long":
        if d.instrument.upper() not in (pack.symbol.upper(), pack.sector_etf.upper()):
            probs.append(f"instrument {d.instrument!r} is not the event's stock or its sector ETF")
        if not d.reasons_to_avoid:
            probs.append("a trade must state at least one reason to avoid it")
        if not d.invalidation:
            probs.append("a trade must state what would invalidate it")
    return probs


def parse_ai_decision(text: str, pack: EvidencePack) -> tuple[Optional[AIDecision], list[str]]:
    """(decision, problems). A decision is usable only when problems is empty."""
    try:
        d = AIDecision.model_validate_json(text)
    except ValidationError as exc:
        if any(e["type"] == "json_invalid" for e in exc.errors()):
            return None, [f"not JSON: {exc.errors()[0]['msg']}"]
        return None, ["schema: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:6])]
    except ValueError as exc:
        return None, [f"not JSON: {exc}"]
    return d, grounding_problems(d, pack)


def no_trade(decider: str, reason: str, *, invalid: bool = False, problems: Optional[list[str]] = None,
             **extra) -> dict:
    return {"decider": decider, "action": "no_trade", "instrument": None, "entry": None, "exit": None,
            "p_up_by_close": None, "expected_net_return_pct": None, "rationale": reason,
            "reasons_to_avoid": [reason], "valid": not invalid, "problems": problems or []} | extra


def trade(decider: str, instrument: str, entry: dict, exit_: dict, rationale: str, reasons_to_avoid: list[str],
          **extra) -> dict:
    return {"decider": decider, "action": "long", "instrument": instrument, "entry": entry, "exit": exit_,
            "rationale": rationale, "reasons_to_avoid": reasons_to_avoid, "valid": True, "problems": []} | extra
