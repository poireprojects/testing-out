"""The monthly decision contract: its JSON schema, and the checks code applies before a decision can trade.

The AI owns the choices (which stocks, which weights, buy, hold, reduce, sell or 100% VOO). Code never edits a
decision: it either accepts it whole or rejects it whole, and a rejected decision keeps the existing holdings.
Checks:
* limits: at most 3 stocks after the decision; a stock target in (0, 10]%; VOO, the remainder, in [70, 100]%;
  long only; a held stock may be held unchanged only if it is at most 10% at the cutoff close;
* coverage: every held stock is addressed exactly once; a new stock must be an eligible universe member;
* reasons: a buy or increase states why it should beat VOO at the current price, the main downside, what would
  invalidate the thesis and why the weight; a hold says why the thesis still holds; a reduce or sale says what
  changed;
* evidence: a buy, increase or hold cites at least one of the company's filing passages; a buy or increase also
  cites its valuation row and at least one figure. A quote must appear verbatim in the cited item and every number
  in a figure must appear in its quote (the same grounding rule as autotrader/decision.py).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional

from ..decision import _norm, _numbers
from .pack import MonthPack
from .registration import BENCHMARK, FROZEN, dec

ACTIONS = ("buy", "increase", "hold", "reduce", "sell")
TEXT_FIELDS = ("why_outperform_voo", "main_downside", "invalidation", "weight_rationale", "what_changed",
               "thesis_still_holds")
REQUIRED_TEXT = {"buy": ("why_outperform_voo", "main_downside", "invalidation", "weight_rationale"),
                 "increase": ("why_outperform_voo", "main_downside", "invalidation", "weight_rationale"),
                 "hold": ("thesis_still_holds",), "reduce": ("what_changed",), "sell": ("what_changed",)}
TOLERANCE_PP = Decimal("0.05")


def json_schema(symbols: list[str]) -> dict:
    cite = {"type": "object", "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "quote": {"type": "string"},
                           "figures": {"type": "array", "items": {"type": "string"}}},
            "required": ["id", "quote", "figures"]}
    pos = {"type": "object", "additionalProperties": False,
           "properties": {"symbol": {"type": "string", "enum": sorted(symbols)},
                          "action": {"type": "string", "enum": list(ACTIONS)},
                          "target_weight_pct": {"type": "number"},
                          **{k: {"type": "string"} for k in TEXT_FIELDS},
                          "evidence": {"type": "array", "items": cite}},
           "required": ["symbol", "action", "target_weight_pct", *TEXT_FIELDS, "evidence"]}
    return {"type": "object", "additionalProperties": False,
            "properties": {"month": {"type": "string"}, "summary": {"type": "string"},
                           "voo_weight_pct": {"type": "number"}, "positions": {"type": "array", "items": pos}},
            "required": ["month", "summary", "voo_weight_pct", "positions"]}


INSTRUCTIONS = """You manage the AI portfolio of a 12-month paper experiment against buying and holding VOO.
Once a month you choose up to three individual stocks from the candidate list and their weights; the rest of the
portfolio is VOO. Choosing 100% VOO is always allowed and is the right answer when no stock is likely to beat VOO
at its current price after costs (the research itself is charged to this portfolio every month).

Rules that code enforces (a decision that breaks any of them is rejected whole and nothing trades):
* address every stock you hold now exactly once: hold, increase, reduce or sell it;
* buy only eligible candidates; at most 3 stocks after your decision; each bought, increased or reduced stock gets
  a target weight above 0 and at most 10 percent; hold keeps the shares unchanged and is allowed only if the stock
  is at most 10 percent now; sell has target 0; voo_weight_pct is 100 minus the stock weights (held stocks at their
  current weight);
* buy or increase: why it should outperform VOO at the current price, the main downside, what would invalidate the
  thesis, and why this weight; cite the company's filing passages and its valuation row, with figures;
* hold: why the thesis still holds, citing a filing passage; reduce or sell: what changed;
* a quote must be copied exactly from the cited item, and every number in a figure must appear in that quote.
Use only the material below. Leave a text field empty when it does not apply."""


def _pct(x) -> Optional[Decimal]:
    if isinstance(x, bool) or not isinstance(x, (int, float, str)):
        return None
    try:
        return dec(x)
    except InvalidOperation:
        return None


def _citations(p: dict, pack: MonthPack, i: int) -> tuple[list[str], list[str], int]:
    """(problems, cited ids, figures counted)."""
    probs, ids, nfig = [], [], 0
    for j, c in enumerate(p.get("evidence") or []):
        where = f"position {i + 1} ({p.get('symbol')}) evidence {j + 1}"
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not isinstance(c.get("quote"), str):
            probs.append(f"{where}: malformed citation")
            continue
        e = pack.get(c["id"])
        if e is None:
            probs.append(f"{where}: unknown item {c['id']!r} (not in this month's pack)")
            continue
        q = _norm(c["quote"])
        if len(q) < 8 or q not in _norm(e.text):
            probs.append(f"{where}: quote not found verbatim in {c['id']}")
            continue
        for fig in c.get("figures") or []:
            nums = _numbers(str(fig))
            if not nums:
                probs.append(f"{where}: figure {fig!r} has no number")
            elif nums - _numbers(c["quote"]):
                probs.append(f"{where}: figure {fig!r} states numbers not in its quote")
            else:
                nfig += 1
        ids.append(c["id"])
    return probs, ids, nfig


def validate(decision: object, pack: MonthPack) -> tuple[Optional[dict], list[str], Optional[Decimal]]:
    """(targets {symbol: {action, target_pct}}, problems, implied VOO %). Usable only when problems is empty."""
    lim = FROZEN["limits"]
    cap, vmin, vmax = dec(lim["stock_max_pct"]), dec(lim["voo_min_pct"]), dec(lim["voo_max_pct"])
    minlen = FROZEN["evidence"]["min_text_chars"]
    if not isinstance(decision, dict) or not isinstance(decision.get("positions"), list):
        return None, ["not a decision object with a positions list"], None
    probs: list[str] = []
    if decision.get("month") != pack.month:
        probs.append(f"month {decision.get('month')!r} is not {pack.month}")
    held = {h["symbol"]: dec(h["weight_pct"]) for h in pack.holdings if h["symbol"] != BENCHMARK}
    eligible = set(pack.eligible())
    seen, targets, after = set(), {}, Decimal(0)
    for i, p in enumerate(decision["positions"]):
        if not isinstance(p, dict):
            probs.append(f"position {i + 1}: not an object")
            continue
        sym, act, w = str(p.get("symbol", "")).upper(), p.get("action"), _pct(p.get("target_weight_pct"))
        tag = f"position {i + 1} ({sym})"
        if sym == BENCHMARK:
            probs.append(f"{tag}: {BENCHMARK} is the remainder, not a position")
            continue
        if sym in seen:
            probs.append(f"{tag}: listed twice")
            continue
        seen.add(sym)
        if act not in ACTIONS:
            probs.append(f"{tag}: unknown action {act!r}")
            continue
        if sym in held and act == "buy":
            probs.append(f"{tag}: already held; use increase, hold, reduce or sell")
            continue
        if sym not in held and act != "buy":
            probs.append(f"{tag}: {act} of a stock that is not held")
            continue
        if act == "buy" and sym not in eligible:
            probs.append(f"{tag}: not an eligible candidate this month")
            continue
        if w is None:
            probs.append(f"{tag}: target_weight_pct is not a number")
            continue
        if act == "sell":
            if w != 0:
                probs.append(f"{tag}: a sale has target 0")
        elif act == "hold":
            if held[sym] > cap:
                probs.append(f"{tag}: held at {held[sym]}%, above the {cap}% cap: reduce or sell it")
            after += held[sym]
        else:
            if not (0 < w <= cap):
                probs.append(f"{tag}: target {w}% is outside (0, {cap}]")
            if act == "increase" and sym in held and w <= held[sym]:
                probs.append(f"{tag}: increase to {w}% is not above the current {held[sym]}%")
            if act == "reduce" and sym in held and w >= held[sym]:
                probs.append(f"{tag}: reduce to {w}% is not below the current {held[sym]}%")
            after += w
        for k in TEXT_FIELDS:
            if not isinstance(p.get(k, ""), str):
                probs.append(f"{tag}: {k} is not text")
        for k in REQUIRED_TEXT[act]:
            if len(str(p.get(k) or "").strip()) < minlen:
                probs.append(f"{tag}: {k} is required (at least {minlen} characters)")
        cprobs, ids, nfig = _citations(p, pack, i)
        probs += cprobs
        own = [x for x in ids if x.startswith(f"{sym}/")]
        if act in ("buy", "increase", "hold") and not own:
            probs.append(f"{tag}: cites none of {sym}'s filing passages")
        if act in ("buy", "increase"):
            if f"V.{sym}" not in ids:
                probs.append(f"{tag}: does not cite its valuation row V.{sym}")
            if nfig == 0:
                probs.append(f"{tag}: states no supporting figure")
        targets[sym] = {"action": act, "target_pct": None if act == "hold" else str(w)}
    for sym in sorted(set(held) - seen):
        probs.append(f"held {sym} is not addressed (hold, increase, reduce or sell it)")
    n_after = sum(1 for t in targets.values() if t["action"] != "sell")
    if n_after > lim["max_stocks"]:
        probs.append(f"{n_after} stocks after the decision; at most {lim['max_stocks']}")
    voo = Decimal(100) - after
    if not (vmin <= voo <= vmax):
        probs.append(f"{BENCHMARK} would be {voo}%, outside [{vmin}, {vmax}]%")
    stated = _pct(decision.get("voo_weight_pct"))
    if stated is None or abs(stated - voo) > TOLERANCE_PP:
        probs.append(f"voo_weight_pct {decision.get('voo_weight_pct')!r} does not match the implied {voo}%")
    if not isinstance(decision.get("summary", ""), str):
        probs.append("summary is not text")
    return (None if probs else targets), probs, voo
