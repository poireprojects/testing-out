"""The proposed budget calculation, from a maximum-size request rendered offline. Nothing is sent or priced live.

Three kinds of number are kept apart:
* measured: the exact bytes of the request this code would send for a pack filled to every registered cap
  (12 candidates x 4 passages x 1,200 characters, 12 valuation rows at their longest wording, three holdings with
  recorded theses), and of a maximum-length answer under the contract's caps;
* estimated: token counts from bytes, as a range (2.5 to 4 bytes per token). The tokenizer is not public and the
  structured-output system prompt is billed but of undocumented size, so these are estimates, never bounds;
* documented bounds: `max_tokens` (16,000) is a strict limit on output tokens including thinking, so output cost per
  request is bounded at 16,000 x $20 per million = $0.32. Nothing documents a bound on billable input tokens.

Budget rules for a later paid client (not implemented): at most 12 requests (one per month; no retries; a failed or
lost request counts and is never resent); before each request, reserve its estimated maximum in the durable request
journal and keep the reservation until the provider's charge is reconciled; the sum of reservations and reconciled
charges must stay within the $12.50 overall ceiling. Because the input side has no demonstrated bound and no
provider limit is documented as non-overshooting, paid dispatch stays BLOCKED.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from decimal import Decimal

from ..evidence import Evidence
from .contract import (INSTRUCTIONS, MAX_CITES, MAX_CLAIM_TEXT, MAX_CLAIMS, MAX_INPUTS, MAX_NOTE, MAX_QUOTE,
                       MAX_REASONING, MAX_TEXT, json_schema)
from .engine import ModelDecider
from .pack import MonthPack, _valuation
from .registration import FROZEN, canonical_json, schedule

D = Decimal
CEILING = D("12.50")
REQUESTS_MAX = FROZEN["months"]
BYTES_PER_TOKEN = (D("2.5"), D("4"))


def _filler(n: int, seed: str) -> str:
    """Filing-like text of exactly n characters with many figures (dense numbers tokenize worse than prose)."""
    unit = (f"In fiscal {seed}, revenue was $12,345.6 million, up 7.8% year over year; operating margin was 23.4%, "
            f"diluted EPS was $4.56 and free cash flow reached $3,210 million, while backlog rose 11% to $8,901 million. ")
    return (unit * (n // len(unit) + 1))[:n]


def longest_valuation(sym: str, cutoff) -> str:
    """The longest valuation row `pack._valuation` can write for `sym`: every form of each part (large figures,
    several share classes, unresolved inputs) is rendered and the longest row kept, so the budget's request stays a
    maximum when the row's wording changes."""
    f = {"form": "10-K/A", "accession": "0000000000-31-000000", "accepted_at": "2031-01-20T16:05:00-05:00"}
    big = {"shares_outstanding": "123456789012", "revenue_ttm_musd": "-1234567.89", "net_income_ttm_musd": "-123456.78",
           "eps_diluted_ttm": "-1234.56"}
    unresolved = {"status": "unresolved", "reason": "budget"}
    rows = []
    for classes in ("1", "99"):
        for gone in ((), ("shares_outstanding",), tuple(big)):
            facts = {k: v for k, v in big.items() if k not in gone} | ({"share_classes": classes} if
                                                                         "shares_outstanding" not in gone else {})
            rec = dict(f, facts=facts, facts_status={k: unresolved for k in gone})
            rows.append(_valuation(sym, D("123456.78"), rec, cutoff)[0])
    return max(rows, key=lambda t: (len(t.encode()), t))


def max_size_pack() -> MonthPack:
    slot = schedule("2031-01")[1]
    cutoff = slot.cutoff.close
    lim = FROZEN["evidence"]
    syms = [f"SYM{i:02d}X" for i in range(1, 13)]
    items = []
    for s in syms:
        for k in range(lim["max_passages_per_candidate"]):
            items.append(Evidence(f"{s}/0000000000-31-{k:06d}/P{k + 1}", "filing",
                                  f"SEC 10-Q 0000000000-31-{k:06d} (A Company With A Long Registered Name Incorporated), "
                                  "MD&A: figures", cutoff - timedelta(days=10), _filler(lim["max_passage_chars"], s)))
        items.append(Evidence(f"V.{s}", "valuation", f"computed from the {slot.cutoff.day} close and 0000000000-31-000000",
                              cutoff, longest_valuation(s, cutoff)))
    holdings = [{"symbol": s, "qty": "0.123456789", "close": "1234.56", "value": "9.99", "weight_pct": "9.99"}
                for s in syms[:3]] + [{"symbol": "VOO", "qty": "0.123456789", "close": "567.89", "value": "70.01",
                                       "weight_pct": "70.01"}]
    theses = [{"symbol": s, "month": "2030-12", "mispricing": "x" * MAX_TEXT, "invalidation": "y" * 120}
              for s in syms[:3]]
    cands = [{"rank": i + 1, "symbol": s, "name": "A Company With A Long Registered Name Incorporated",
              "eligible": True, "why": "", "passages": 4} for i, s in enumerate(syms)]
    cov = [{"symbol": s, "status": "complete", "reason": "", "required": ["0000000000-31-000000"],
            "index_retrieved_at": "2031-02-01T00:00:00+00:00", "index_source": "x"} for s in syms]
    return MonthPack(slot.index, slot.month, cutoff, slot.deadline, cutoff + timedelta(minutes=20), False, "0.00",
                     "100.00", "567.89", holdings, cands, theses, items, cov, {"data_manifest_sha256": "0" * 64})


def max_answer(symbols: list[str]) -> dict:
    """An answer at every cap of the contract (three kept stocks, all claims and inputs at their limits)."""
    t = "z" * MAX_TEXT
    cite = {"id": "SYM01X/0000000000-31-000000/P1", "quote": "q" * MAX_QUOTE, "figures": ["12,345.6", "7.8%"]}
    claim = {"type": "calculation", "text": "c" * MAX_CLAIM_TEXT, "evidence": [cite] * MAX_CITES,
             "inputs": [{"name": f"input_{i}", "value": 1234.5678, "source_id": "V.SYM01X"} for i in range(MAX_INPUTS)],
             "expression": " + ".join(f"input_{i}" for i in range(MAX_INPUTS)), "result": 7407.4068}
    pos = {"symbol": symbols[0], "action": "hold", "target_weight_pct": 10.0, **{k: t for k in (
        "mispricing", "valuation_link", "contrary_evidence", "downside", "weight_rationale", "what_changed",
        "thesis_still_holds")},
        "invalidation": {"metric": t[:120], "comparator": "<", "threshold": 1.5, "unit": "percent",
                         "check_by": "2031-06-30"},
        "scenarios": {k: {"return_pct_3m": 1.0, "reasoning": "r" * MAX_REASONING} for k in ("base", "upside", "downside")},
        "claims": [claim] * MAX_CLAIMS}
    return {"month": "2031-02", "summary": t, "voo_weight_pct": 70.0, "prefer_voo_reason": "",
            "assessments": [{"symbol": s, "rank": i + 1, "expected_excess_pct_3m": -12.34, "p_positive_excess": 0.4567,
                             "note": "n" * MAX_NOTE} for i, s in enumerate(symbols)],
            "positions": [dict(pos, symbol=s) for s in symbols[:3]]}


def _tokens(b: int) -> dict:
    lo, hi = (D(b) / BYTES_PER_TOKEN[1]).quantize(D(1)), (D(b) / BYTES_PER_TOKEN[0]).quantize(D(1))
    return {"low": int(lo), "high": int(hi)}


def max_size_budget() -> dict:
    pack = max_size_pack()
    syms = [c["symbol"] for c in pack.candidates]
    params = ModelDecider(syms).request(pack)
    body = canonical_json(params)
    schema_b = len(canonical_json(json_schema(syms)))
    answer_b = len(json.dumps(max_answer(syms), separators=(",", ":")))
    price_in, price_out = D(FROZEN["model"]["price_sheet"]["input_usd_per_mtok"]), D(FROZEN["model"]["price_sheet"]["output_usd_per_mtok"])
    max_out = FROZEN["model"]["max_tokens"]
    tin = _tokens(len(body))
    out_bound = (D(max_out) * price_out / 1_000_000).quantize(D("0.0001"))
    per_req = {k: (D(v) * price_in / 1_000_000 + out_bound).quantize(D("0.0001")) for k, v in tin.items()}
    total = {k: (v * REQUESTS_MAX).quantize(D("0.01")) for k, v in per_req.items()}
    ans_tok = _tokens(answer_b)
    return {
        "model": FROZEN["model"]["id"], "effort": FROZEN["model"]["effort"], "max_tokens": max_out,
        "request_bytes": len(body), "request_sha256": hashlib.sha256(body).hexdigest(),
        "measured_bytes": {"request_total": len(body), "system_instructions": len(INSTRUCTIONS.encode()),
                           "schema": schema_b, "pack_prompt": len(pack.prompt_text().encode()),
                           "max_length_answer": answer_b},
        "estimated_tokens": {"input": tin, "max_length_answer": ans_tok,
                             "note": "estimates from bytes (2.5-4 bytes per token); the tokenizer is not public; the "
                                     "structured-output system prompt is billed and its size is undocumented, so it "
                                     "is NOT included"},
        "output_room": {"max_tokens_includes_thinking": True,
                        "visible_answer_at_caps_high_estimate": ans_tok["high"],
                        "left_for_thinking_at_that_estimate": max_out - ans_tok["high"],
                        "risk": "an answer at every cap plus thinking can exceed max_tokens: it would stop at "
                                "max_tokens and count as a missed answer (charged)"},
        "documented_bounds": {"output_per_request_usd": str(out_bound),
                              "source": "max_tokens is a strict limit that includes thinking; output price "
                                        f"${price_out}/MTok (pricing page)",
                              "input_per_request": "none documented"},
        "estimates_usd": {"per_request": {k: str(v) for k, v in per_req.items()},
                          "twelve_requests": {k: str(v) for k, v in total.items()}},
        "plan": {"requests_max": REQUESTS_MAX, "retries": 0,
                 "failed_or_lost_requests": "count as one of the 12, are charged, and are never resent",
                 "reservation": "before each request, reserve its estimated maximum (high) in the durable request "
                                "journal; release only against the reconciled provider charge",
                 "ceiling_usd": str(CEILING), "ceiling_status": "the project's overall ceiling; no pilot spend approved"},
        "paid_dispatch": "BLOCKED",
        "missing_guarantee": "a provider-documented upper bound on billable input tokens for this exact request "
                             "(including the injected structured-output system prompt), or a provider-enforced spend "
                             "limit documented not to overshoot when requests are in flight, set at or below the "
                             "remaining ceiling; the rate-limits page documents neither"}
