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


# ================================================================== contract v2: sentence references
# Audit of v1 (research/runs/2026-11-07-phase5/audit/): of 30 invalid answers from the 3B model, 2 stated a false
# fact; the rest were copying slips, citations of the wrong passage, units, field ranges, lengths and truncation.
# v2 removes copying altogether: the evidence is shown as numbered sentences and a fact cites sentence ids; the
# system inserts the exact text. Verification is unchanged or stricter:
#   * every number in a claim must appear in the sentences it cites (no derived or outside numbers);
#   * a reported figure must cite a sentence of the company's own release, contain the value in the declared
#     unit, and (when XBRL history exists) be within a factor of 3 of the latest reported quarter;
#   * trade fields are small fixed choices; "no trade" carries no trade fields.
CONTRACT_V2 = "2"
STOPS_V2 = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0)
SPREADS_V2 = (10, 20, 30)
TARGETS_V2 = (1, 2, 3, 4, 5)
EXIT_TIMES_V2 = ("11:00", "12:00", "13:00", "14:00", "15:00", "15:45")
_SPLIT = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9$(\[\"'])")
RELEASE_KINDS = ("filing", "vendor_headline")


def sentences(pack: EvidencePack, max_len: int = 280) -> dict[str, str]:
    """Deterministic numbered sentences per evidence item: E1.S1, E1.S2, ... (long table runs are cut at spaces)."""
    out: dict[str, str] = {}
    for e in pack.items:
        chunks: list[str] = []
        for part in _SPLIT.split(e.text.strip()):
            part = part.strip()
            while len(part) > max_len:
                cut = part.rfind(" ", 0, max_len)
                cut = cut if cut > max_len // 2 else max_len
                chunks.append(part[:cut].strip())
                part = part[cut:].strip()
            if part:
                chunks.append(part)
        for i, c in enumerate(chunks, 1):
            out[f"{e.id}.S{i}"] = c
    return out


def release_refs(pack: EvidencePack, sents: dict[str, str]) -> list[str]:
    ids = {e.id for e in pack.items if e.kind in RELEASE_KINDS}
    return [r for r in sents if r.split(".")[0] in ids]


def prompt_text_v2(pack: EvidencePack, sents: dict[str, str]) -> str:
    out = [f"EVENT {pack.event_id}: {pack.symbol} (sector ETF {pack.sector_etf}). Decision time "
           f"{pack.decision_time.strftime('%Y-%m-%d %H:%M %Z')}. Everything below was available by then."]
    for e in pack.items:
        out.append(f"\n{e.id}: {e.kind} | {e.source} | available {e.available_at.strftime('%Y-%m-%d %H:%M %Z')}")
        out += [f"[{r}] {t}" for r, t in sents.items() if r.split(".")[0] == e.id]
    return "\n".join(out)


def json_schema_v2(pack: EvidencePack, sents: dict[str, str]) -> dict:
    refs = list(sents)
    rel = release_refs(pack, sents) or refs[:1]
    short = {"type": "string", "maxLength": 200}
    lst = {"type": "array", "items": short, "maxItems": 3}
    trade = {"type": "object", "properties": {
        "instrument": {"enum": [pack.symbol, pack.sector_etf]},
        "max_spread_bps": {"enum": list(SPREADS_V2)},
        "min_move_pct": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "max_move_pct": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "stop_pct": {"enum": list(STOPS_V2)},
        "target_pct": {"anyOf": [{"enum": list(TARGETS_V2)}, {"type": "null"}]},
        "exit_time": {"enum": list(EXIT_TIMES_V2)}},
        "required": ["instrument", "max_spread_bps", "min_move_pct", "max_move_pct", "stop_pct", "target_pct", "exit_time"]}
    return {"type": "object", "properties": {
        "facts": {"type": "array", "minItems": 1, "maxItems": 4, "items": {"type": "object", "properties": {
            "refs": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"enum": refs}},
            "claim": short}, "required": ["refs", "claim"]}},
        "interpretations": lst, "uncertainties": lst,
        "priced_in": short,
        "reported": {"type": "array", "maxItems": 2, "items": {"type": "object", "properties": {
            "metric": {"enum": ["gaap_diluted_eps", "revenue"]}, "ref": {"enum": rel}, "value": {"type": "number"},
            "unit": {"enum": ["usd_per_share", "usd_millions", "usd_billions"]}},
            "required": ["metric", "ref", "value", "unit"]}},
        "p_up_pct": {"type": "integer"}, "expected_net_return_bps": {"type": "integer"},
        "decision": {"enum": ["long", "no_trade"]},
        "trade": {"anyOf": [{"type": "null"}, trade]},
        "invalidation": lst, "reasons_to_avoid": lst,
        "rationale": {"type": "string", "maxLength": 600},
        "confidence": {"enum": ["low", "medium", "high"]}},
        "required": ["facts", "interpretations", "uncertainties", "priced_in", "reported", "p_up_pct",
                     "expected_net_return_bps", "decision", "trade", "invalidation", "reasons_to_avoid",
                     "rationale", "confidence"]}


class FactV2(_Strict):
    refs: list[str] = Field(min_length=1, max_length=3)
    claim: str = Field(min_length=2, max_length=300)


class ReportedV2(_Strict):
    metric: Literal["gaap_diluted_eps", "revenue"]
    ref: str
    value: float
    unit: Literal["usd_per_share", "usd_millions", "usd_billions"]


class TradeV2(_Strict):
    instrument: str
    max_spread_bps: int
    min_move_pct: Optional[float] = Field(None, ge=-30, le=30)
    max_move_pct: Optional[float] = Field(None, ge=-30, le=30)
    stop_pct: float
    target_pct: Optional[float] = None
    exit_time: str

    @model_validator(mode="after")
    def _choices(self):
        if self.max_spread_bps not in SPREADS_V2:
            raise ValueError(f"max_spread_bps must be one of {SPREADS_V2}")
        if self.stop_pct not in STOPS_V2:
            raise ValueError(f"stop_pct must be one of {STOPS_V2}")
        if self.target_pct is not None and self.target_pct not in TARGETS_V2:
            raise ValueError(f"target_pct must be null or one of {TARGETS_V2}")
        if self.exit_time not in EXIT_TIMES_V2:
            raise ValueError(f"exit_time must be one of {EXIT_TIMES_V2}")
        return self


class AIDecisionV2(_Strict):
    facts: list[FactV2] = Field(min_length=1, max_length=4)
    interpretations: list[str] = Field(max_length=3)
    uncertainties: list[str] = Field(max_length=3)
    priced_in: str = Field(max_length=800)
    reported: list[ReportedV2] = Field(max_length=2)
    p_up_pct: int = Field(ge=0, le=100)
    expected_net_return_bps: int = Field(ge=-2000, le=2000)
    decision: Literal["long", "no_trade"]
    trade: Optional[TradeV2] = None
    invalidation: list[str] = Field(max_length=3)
    reasons_to_avoid: list[str] = Field(max_length=3)
    rationale: str = Field(min_length=5, max_length=1500)
    confidence: Literal["low", "medium", "high"]

    @model_validator(mode="after")
    def _trade_matches_decision(self):
        if self.decision == "long" and self.trade is None:
            raise ValueError("decision 'long' needs a trade")
        if self.decision == "no_trade":
            self.trade = None
        return self


def _sent_nums(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(text.translate(_TRANS))} | \
        {n.replace(",", "").rstrip(".").rstrip("0").rstrip(".") for n in _NUM.findall(text.translate(_TRANS)) if "." in n}


def _latest_quarter_revenue(pack: EvidencePack) -> Optional[float]:
    for e in pack.items:
        if e.kind == "financials":
            vals = re.findall(r"revenue \$([\d,]+) million", e.text)
            if vals:
                return float(vals[-1].replace(",", ""))
    return None


def grounding_problems_v2(d: AIDecisionV2, pack: EvidencePack, sents: dict[str, str]) -> list[str]:
    probs: list[str] = []
    for i, f in enumerate(d.facts):
        bad = [r for r in f.refs if r not in sents]
        if bad:
            probs.append(f"fact {i + 1} cites unknown sentence(s) {', '.join(bad)}")
            continue
        allowed = set().union(*(_sent_nums(sents[r]) for r in f.refs))
        extra = {n for n in _numbers(f.claim) if n not in allowed and (n.rstrip("0").rstrip(".") if "." in n else n) not in allowed}
        if extra:
            probs.append(f"fact {i + 1}: claim states numbers not in its cited sentences ({', '.join(sorted(extra))})")
    rel = set(release_refs(pack, sents))
    latest = _latest_quarter_revenue(pack)
    for i, rp in enumerate(d.reported):
        if rp.ref not in rel:
            probs.append(f"reported {rp.metric}: {rp.ref} is not a sentence of the company's release")
            continue
        forms = {f"{abs(rp.value):g}", f"{abs(rp.value):.2f}"} | ({f"{abs(rp.value):.0f}"} if abs(rp.value) >= 100 else set())
        if not forms & _sent_nums(sents[rp.ref]):
            probs.append(f"reported {rp.metric}={rp.value:g} does not appear in {rp.ref}")
            continue
        if rp.metric == "revenue":
            if rp.unit == "usd_per_share":
                probs.append("reported revenue cannot be per share")
            elif latest:
                m = rp.value * (1000 if rp.unit == "usd_billions" else 1)
                if not (latest / 3 <= m <= latest * 3):
                    probs.append(f"reported revenue {m:,.0f} million is not within 3x of the latest filed quarter "
                                 f"({latest:,.0f} million): wrong unit or period")
        elif rp.unit != "usd_per_share":
            probs.append("reported EPS must be in usd_per_share")
    if d.decision == "long" and d.trade is not None:
        if d.trade.instrument.upper() not in (pack.symbol.upper(), pack.sector_etf.upper()):
            probs.append(f"instrument {d.trade.instrument!r} is not the event's stock or its sector ETF")
        if not d.reasons_to_avoid:
            probs.append("a trade must state at least one reason to avoid it")
        if not d.invalidation:
            probs.append("a trade must state what would invalidate it")
    return probs


def parse_ai_decision_v2(text: str, pack: EvidencePack, sents: Optional[dict[str, str]] = None):
    """(decision, problems, sentences). Usable only when problems is empty."""
    sents = sents if sents is not None else sentences(pack)
    try:
        d = AIDecisionV2.model_validate_json(text)
    except ValidationError as exc:
        if any(e["type"] == "json_invalid" for e in exc.errors()):
            return None, [f"not JSON: {exc.errors()[0]['msg']}"], sents
        return None, ["schema: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:6])], sents
    return d, grounding_problems_v2(d, pack, sents), sents
