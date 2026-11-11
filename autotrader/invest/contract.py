"""The monthly decision contract (version 2, proposed): its JSON schema, the instructions, and the checks code applies
before a decision can trade.

The AI owns the judgment: which stocks, which weights, buy, hold, reduce, sell or 100% VOO, and its assessment of
every candidate. Code never edits a decision: it accepts it whole or rejects it whole, and a rejected decision keeps
the existing holdings. What code checks:

* Limits (unchanged): at most 3 stocks after the decision; a target in (0, 10]%; VOO, the remainder, in [70, 100]%;
  long only; a held stock may be held unchanged only if it is at most 10% at the cutoff close; every held stock is
  addressed exactly once; a new stock must be an eligible universe member.
* Assessments: every eligible candidate and every held stock exactly once, rejected names included, with an integer
  rank (1 = most attractive; ties allowed; at most the number assessed), the expected total-return excess over VOO
  for the next three monthly holding periods in percentage points within [-100, 100], and the probability that the
  excess is positive as a fraction within [0, 1]. A percentage given as a probability (say 55) is rejected.
* Positions kept after the decision (buy, increase, hold, reduce): the perceived mispricing, how the current price
  and valuation relate to the thesis, the strongest contrary evidence, the downside, a measurable invalidation
  (metric, comparator, number, unit, date), why the weight, and base/upside/downside three-month returns with
  downside <= base <= upside. A hold also says why the thesis still holds; a reduce or sale says what changed.
* Claims are typed and kept apart: `fact` (sourced: at least one verbatim citation whose figures appear in its
  quote), `calculation` (inputs each sourced from a pack item whose text contains the value, or labelled as an
  assumption; an arithmetic expression over the inputs; a stated result that code recomputes), `assumption` and
  `forecast` (labelled, never carrying citations), `inferred_expectation` (what the price seems to imply, labelled as
  the model's inference; it may cite only valuation rows and may never claim analyst estimates or a consensus).
  Every kept position cites at least one of the company's filing passages; a buy or increase also uses its valuation
  row in a calculation.
* Abstention: when no stock remains, a reason to prefer VOO is required.
* Size: text fields and lists are capped so one answer fits the single request's output allowance.

These checks verify form and quotation mechanics only. A claim can pass every check and still be wrong in meaning
(direction, units, period, valuation); that is what the blinded claim review (`claims.py`) is for, and its verdicts
never edit a frozen decision.
"""
from __future__ import annotations

import ast
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from ..decision import _norm, _numbers
from .pack import MonthPack
from .registration import BENCHMARK, FROZEN, dec

ACTIONS = ("buy", "increase", "hold", "reduce", "sell")
KEPT = ("buy", "increase", "hold", "reduce")
CLAIM_TYPES = ("fact", "calculation", "assumption", "forecast", "inferred_expectation")
COMPARATORS = ("<", "<=", ">", ">=")
TEXT_FIELDS = ("mispricing", "valuation_link", "contrary_evidence", "downside", "weight_rationale", "what_changed",
               "thesis_still_holds")
REQUIRED_TEXT = {"buy": ("mispricing", "valuation_link", "contrary_evidence", "downside", "weight_rationale"),
                 "increase": ("mispricing", "valuation_link", "contrary_evidence", "downside", "weight_rationale"),
                 "hold": ("mispricing", "valuation_link", "contrary_evidence", "downside", "weight_rationale",
                          "thesis_still_holds"),
                 "reduce": ("mispricing", "valuation_link", "contrary_evidence", "downside", "weight_rationale",
                            "what_changed"),
                 "sell": ("what_changed",)}
TOLERANCE_PP = Decimal("0.05")
EXCESS_BOUNDS = (Decimal(-100), Decimal(100))         # percentage points of total return versus VOO, three months
# Caps sized so an answer at every cap stays well inside max_tokens (16,000, thinking included); see budget.py.
MAX_TEXT, MAX_NOTE, MAX_REASONING, MAX_CLAIM_TEXT, MAX_QUOTE = 240, 100, 160, 160, 200
MAX_CLAIMS, MAX_INPUTS, MAX_CITES = 4, 4, 2
NO_CONSENSUS = re.compile(r"(?i)\b(consensus|analysts?|street estimates?|sell-side)\b")
NAME = re.compile(r"^[a-z][a-z0-9_]{0,19}$")


def json_schema(symbols: list[str]) -> dict:
    s, n = {"type": "string"}, {"type": "number"}
    cite = {"type": "object", "additionalProperties": False,
            "properties": {"id": s, "quote": s, "figures": {"type": "array", "items": s}},
            "required": ["id", "quote", "figures"]}
    inp = {"type": "object", "additionalProperties": False, "properties": {"name": s, "value": n, "source_id": s},
           "required": ["name", "value", "source_id"]}
    claim = {"type": "object", "additionalProperties": False,
             "properties": {"type": {"type": "string", "enum": list(CLAIM_TYPES)}, "text": s,
                            "evidence": {"type": "array", "items": cite}, "inputs": {"type": "array", "items": inp},
                            "expression": s, "result": n},
             "required": ["type", "text", "evidence", "inputs", "expression", "result"]}
    scen = {"type": "object", "additionalProperties": False, "properties": {"return_pct_3m": n, "reasoning": s},
            "required": ["return_pct_3m", "reasoning"]}
    inval = {"type": "object", "additionalProperties": False,
             "properties": {"metric": s, "comparator": {"type": "string", "enum": list(COMPARATORS)}, "threshold": n,
                            "unit": s, "check_by": s},
             "required": ["metric", "comparator", "threshold", "unit", "check_by"]}
    pos = {"type": "object", "additionalProperties": False,
           "properties": {"symbol": {"type": "string", "enum": sorted(symbols)},
                          "action": {"type": "string", "enum": list(ACTIONS)}, "target_weight_pct": n,
                          **{k: s for k in TEXT_FIELDS}, "invalidation": inval,
                          "scenarios": {"type": "object", "additionalProperties": False,
                                        "properties": {"base": scen, "upside": scen, "downside": scen},
                                        "required": ["base", "upside", "downside"]},
                          "claims": {"type": "array", "items": claim}},
           "required": ["symbol", "action", "target_weight_pct", *TEXT_FIELDS, "invalidation", "scenarios", "claims"]}
    assess = {"type": "object", "additionalProperties": False,
              "properties": {"symbol": {"type": "string", "enum": sorted(symbols)}, "rank": {"type": "integer"},
                             "expected_excess_pct_3m": n, "p_positive_excess": n, "note": s},
              "required": ["symbol", "rank", "expected_excess_pct_3m", "p_positive_excess", "note"]}
    return {"type": "object", "additionalProperties": False,
            "properties": {"month": s, "summary": s, "voo_weight_pct": n, "prefer_voo_reason": s,
                           "assessments": {"type": "array", "items": assess}, "positions": {"type": "array", "items": pos}},
            "required": ["month", "summary", "voo_weight_pct", "prefer_voo_reason", "assessments", "positions"]}


INSTRUCTIONS = """You manage the AI portfolio of a 12-month paper experiment against buying and holding VOO.
Once a month you may hold up to three stocks from the candidate list; the rest is VOO. The question is whether you
can find business changes whose implications the current price does not yet reflect, and size positions by upside,
downside and uncertainty. Choosing 100% VOO is always allowed and is right when no candidate clears your own
assessment after costs (the research itself is charged to this portfolio every month).

Answer in the required JSON. Rules that code enforces (any breach rejects the whole decision and nothing trades):
* assessments: every eligible candidate and every stock you hold, exactly once, rejected names included: rank
  (integer, 1 = most attractive, ties allowed), expected_excess_pct_3m (your expected total return minus VOO's over
  the next three monthly holding periods, in percentage points, between -100 and 100), p_positive_excess (the
  probability that excess is positive, a fraction between 0 and 1, not a percentage), note (one short line);
* positions: address every stock you hold exactly once (hold, increase, reduce or sell); buy only eligible
  candidates; at most 3 stocks after your decision; a bought, increased or reduced stock gets a target above 0 and at
  most 10 percent; hold keeps the shares and is allowed only if the stock is at most 10 percent now; sell has target
  0; voo_weight_pct is 100 minus the stock weights (held stocks at their current weight);
* every stock you keep: mispricing (what you think the price misses), valuation_link (how the current price and
  valuation relate to that), contrary_evidence (the strongest evidence against you), downside, invalidation (a
  measurable metric, comparator, number, unit and a check_by date), weight_rationale, and scenarios (base, upside,
  downside three-month returns in percent with downside <= base <= upside); hold adds thesis_still_holds; reduce or
  sell adds what_changed;
* claims keep kinds apart: fact (an observed statement, with exact quotes from the cited item; every number in a
  figure must appear in its quote), calculation (inputs with values taken from a cited item, or source_id
  "assumption"; an expression over the input names using + - * / and parentheses; the result), assumption and
  forecast (your own premises and predictions: no citations), inferred_expectation (what the price appears to imply:
  your inference; it may cite only valuation rows and must not mention analysts or a consensus, which are not in the
  material). Each kept stock cites at least one of its filing passages; a buy or increase uses its valuation row in a
  calculation;
* if you hold no stock after the decision, prefer_voo_reason says why no candidate clears your assessment;
* stay compact: text fields at most 240 characters, scenario reasoning and claim text 160, quotes 200, notes 100;
  at most 4 claims per stock, 4 inputs per calculation and 2 citations per claim.
Use only the material below. Say "unknown" rather than guessing. Leave a text field empty when it does not apply."""


def _num(x) -> Optional[Decimal]:
    if isinstance(x, bool) or not isinstance(x, (int, float, str)):
        return None
    try:
        return dec(x)
    except InvalidOperation:
        return None


_pct = _num


def _cite(c, pack: MonthPack, where: str) -> tuple[list[str], Optional[str], int]:
    """(problems, cited id, figures counted) for one citation."""
    if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not isinstance(c.get("quote"), str):
        return [f"{where}: malformed citation"], None, 0
    e = pack.get(c["id"])
    if e is None:
        return [f"{where}: unknown item {c['id']!r} (not in this month's pack)"], None, 0
    q = _norm(c["quote"])
    if len(q) < 8 or q not in _norm(e.text):
        return [f"{where}: quote not found verbatim in {c['id']}"], None, 0
    probs, nfig = [], 0
    for fig in c.get("figures") or []:
        nums = _numbers(str(fig))
        if not nums:
            probs.append(f"{where}: figure {fig!r} has no number")
        elif nums - _numbers(c["quote"]):
            probs.append(f"{where}: figure {fig!r} states numbers not in its quote")
        else:
            nfig += 1
    return probs, c["id"], nfig


def _eval(expr: str, env: dict[str, Decimal]) -> Decimal:
    """Evaluate + - * / and parentheses over named inputs and numbers, in Decimal; anything else is refused."""
    tree = ast.parse(expr, mode="eval")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = ev(n.left), ev(n.right)
            return a + b if isinstance(n.op, ast.Add) else a - b if isinstance(n.op, ast.Sub) else \
                a * b if isinstance(n.op, ast.Mult) else a / b
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        if isinstance(n, ast.Name) and n.id in env:
            return env[n.id]
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return Decimal(str(n.value))
        raise ValueError(f"not allowed in an expression: {ast.dump(n)[:60]}")
    return ev(tree)


def _value_in(v: Decimal, text: str) -> bool:
    """A calculation input is sourced only if its value appears in the cited item (as written, commas ignored)."""
    nums = {Decimal(x) for x in _numbers(text) if x}
    return v in nums


def _claims(p: dict, pack: MonthPack, tag: str, sym: str) -> tuple[list[str], dict]:
    probs, seen = [], {"own_passage": False, "valuation_calc": False, "types": set()}
    cl = p.get("claims")
    if not isinstance(cl, list):
        return [f"{tag}: claims is not a list"], seen
    if len(cl) > MAX_CLAIMS:
        probs.append(f"{tag}: {len(cl)} claims, at most {MAX_CLAIMS}")
    for j, c in enumerate(cl):
        where = f"{tag} claim {j + 1}"
        if not isinstance(c, dict) or c.get("type") not in CLAIM_TYPES:
            probs.append(f"{where}: unknown claim type {c.get('type') if isinstance(c, dict) else c!r}")
            continue
        t, text, ev = c["type"], str(c.get("text") or ""), c.get("evidence") or []
        seen["types"].add(t)
        if not text.strip() or len(text) > MAX_CLAIM_TEXT:
            probs.append(f"{where}: text must be 1-{MAX_CLAIM_TEXT} characters")
        if len(ev) > MAX_CITES:
            probs.append(f"{where}: at most {MAX_CITES} citations")
        if any(len(str((x or {}).get("quote", ""))) > MAX_QUOTE for x in ev):
            probs.append(f"{where}: a quote is longer than {MAX_QUOTE} characters")
        if t in ("assumption", "forecast") and ev:
            probs.append(f"{where}: a {t} is the model's own and carries no citation")
        if t == "inferred_expectation":
            if NO_CONSENSUS.search(text):
                probs.append(f"{where}: an inferred expectation must not be presented as analysts' or a consensus view")
            if any(not str((x or {}).get("id", "")).startswith("V.") for x in ev):
                probs.append(f"{where}: an inferred expectation may cite only valuation rows")
        if t == "fact":
            if not ev:
                probs.append(f"{where}: a fact needs at least one citation")
            if NO_CONSENSUS.search(text) and not any(NO_CONSENSUS.search(str((x or {}).get("quote", ""))) for x in ev):
                probs.append(f"{where}: mentions analysts or a consensus that no cited quote contains")
        for k, x in enumerate(ev):
            pr, cid, _ = _cite(x, pack, f"{where} citation {k + 1}")
            probs += pr
            if cid and t == "fact" and cid.startswith(f"{sym}/"):
                seen["own_passage"] = True
        if t == "calculation":
            inputs, env = c.get("inputs") or [], {}
            if not inputs or len(inputs) > MAX_INPUTS:
                probs.append(f"{where}: a calculation needs 1-{MAX_INPUTS} inputs")
            for x in inputs:
                nm, v, src = (x or {}).get("name"), _num((x or {}).get("value")), str((x or {}).get("source_id", ""))
                if not isinstance(nm, str) or not NAME.match(nm) or v is None:
                    probs.append(f"{where}: malformed input {x!r}")
                    continue
                env[nm] = v
                if src == "assumption":
                    continue
                item = pack.get(src)
                if item is None:
                    probs.append(f"{where}: input {nm} cites unknown item {src!r}")
                elif not _value_in(v, item.text):
                    probs.append(f"{where}: input {nm}={v} does not appear in {src}")
                elif src == f"V.{sym}":
                    seen["valuation_calc"] = True
            res = _num(c.get("result"))
            try:
                got = _eval(str(c.get("expression") or ""), env)
            except (ValueError, SyntaxError, ArithmeticError, InvalidOperation) as exc:
                probs.append(f"{where}: expression cannot be evaluated ({exc})")
                continue
            if res is None or abs(got - res) > max(Decimal("0.01"), abs(got) * Decimal("0.005")):
                probs.append(f"{where}: stated result {c.get('result')!r} differs from the recomputed {got:.4f}")
    return probs, seen


def _assessments(decision: dict, pack: MonthPack, held: set[str]) -> list[str]:
    probs, rows = [], decision.get("assessments")
    need = set(pack.eligible()) | held
    if not isinstance(rows, list):
        return ["assessments is not a list"]
    got = []
    for i, a in enumerate(rows):
        tag = f"assessment {i + 1}"
        if not isinstance(a, dict):
            probs.append(f"{tag}: not an object")
            continue
        sym = str(a.get("symbol", "")).upper()
        got.append(sym)
        r = a.get("rank")
        if isinstance(r, bool) or not isinstance(r, int) or not 1 <= r <= len(need):
            probs.append(f"{tag} ({sym}): rank {r!r} must be an integer from 1 to {len(need)} (ties allowed)")
        x = _num(a.get("expected_excess_pct_3m"))
        if x is None or not EXCESS_BOUNDS[0] <= x <= EXCESS_BOUNDS[1]:
            probs.append(f"{tag} ({sym}): expected_excess_pct_3m {a.get('expected_excess_pct_3m')!r} must be "
                         f"percentage points within [{EXCESS_BOUNDS[0]}, {EXCESS_BOUNDS[1]}]")
        p = _num(a.get("p_positive_excess"))
        if p is None or not 0 <= p <= 1:
            hint = " (a percentage was given; use a fraction)" if p is not None and 1 < p <= 100 else ""
            probs.append(f"{tag} ({sym}): p_positive_excess {a.get('p_positive_excess')!r} must be a fraction in "
                         f"[0, 1]{hint}")
        if len(str(a.get("note") or "")) > MAX_NOTE:
            probs.append(f"{tag} ({sym}): note longer than {MAX_NOTE} characters")
    dup = sorted({s for s in got if got.count(s) > 1})
    if dup:
        probs.append("assessed more than once: " + ", ".join(dup))
    if set(got) - need:
        probs.append("assessed names that are neither eligible nor held: " + ", ".join(sorted(set(got) - need)))
    if need - set(got):
        probs.append("not assessed: " + ", ".join(sorted(need - set(got))))
    return probs


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
    probs += _assessments(decision, pack, set(held))
    cutoff_day = pack.cutoff.date()
    seen, targets, after = set(), {}, Decimal(0)
    for i, p in enumerate(decision["positions"]):
        if not isinstance(p, dict):
            probs.append(f"position {i + 1}: not an object")
            continue
        sym, act, w = str(p.get("symbol", "")).upper(), p.get("action"), _num(p.get("target_weight_pct"))
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
            v = p.get(k, "")
            if not isinstance(v, str) or len(v) > MAX_TEXT:
                probs.append(f"{tag}: {k} must be text of at most {MAX_TEXT} characters")
        for k in REQUIRED_TEXT[act]:
            if len(str(p.get(k) or "").strip()) < minlen:
                probs.append(f"{tag}: {k} is required (at least {minlen} characters)")
        cprobs, cs = _claims(p, pack, tag, sym)
        probs += cprobs
        if act in KEPT:
            if not cs["own_passage"]:
                probs.append(f"{tag}: no fact cites one of {sym}'s filing passages")
            inv = p.get("invalidation") or {}
            th, by = _num(inv.get("threshold")), str(inv.get("check_by") or "")
            try:
                by_ok = cutoff_day < date.fromisoformat(by) <= cutoff_day + timedelta(days=400)
            except ValueError:
                by_ok = False
            if not str(inv.get("metric") or "").strip() or inv.get("comparator") not in COMPARATORS or th is None \
                    or not str(inv.get("unit") or "").strip() or not by_ok:
                probs.append(f"{tag}: invalidation must give a metric, a comparator, a number, a unit and a check_by "
                             "date after the cutoff and within 400 days")
            sc = p.get("scenarios") or {}
            r = {k: _num((sc.get(k) or {}).get("return_pct_3m")) for k in ("downside", "base", "upside")}
            if any(v is None or not -100 <= v <= 300 for v in r.values()):
                probs.append(f"{tag}: scenarios need numeric return_pct_3m values (percent, -100 to 300)")
            elif not r["downside"] <= r["base"] <= r["upside"]:
                probs.append(f"{tag}: scenarios must satisfy downside <= base <= upside")
            for k in ("downside", "base", "upside"):
                why = str((sc.get(k) or {}).get("reasoning") or "").strip()
                if len(why) < minlen or len(why) > MAX_REASONING:
                    probs.append(f"{tag}: {k} scenario reasoning is required ({minlen}-{MAX_REASONING} characters)")
        if act in ("buy", "increase") and not cs["valuation_calc"]:
            probs.append(f"{tag}: no calculation uses its valuation row V.{sym}")
        targets[sym] = {"action": act, "target_pct": None if act == "hold" else str(w)}
    for sym in sorted(set(held) - seen):
        probs.append(f"held {sym} is not addressed (hold, increase, reduce or sell it)")
    n_after = sum(1 for t in targets.values() if t["action"] != "sell")
    if n_after > lim["max_stocks"]:
        probs.append(f"{n_after} stocks after the decision; at most {lim['max_stocks']}")
    if n_after == 0 and len(str(decision.get("prefer_voo_reason") or "").strip()) < minlen:
        probs.append("no stock remains: prefer_voo_reason is required")
    if len(str(decision.get("prefer_voo_reason") or "")) > MAX_TEXT:
        probs.append(f"prefer_voo_reason longer than {MAX_TEXT} characters")
    voo = Decimal(100) - after
    if not (vmin <= voo <= vmax):
        probs.append(f"{BENCHMARK} would be {voo}%, outside [{vmin}, {vmax}]%")
    stated = _num(decision.get("voo_weight_pct"))
    if stated is None or abs(stated - voo) > TOLERANCE_PP:
        probs.append(f"voo_weight_pct {decision.get('voo_weight_pct')!r} does not match the implied {voo}%")
    if not isinstance(decision.get("summary", ""), str) or len(decision.get("summary") or "") > MAX_TEXT:
        probs.append(f"summary must be text of at most {MAX_TEXT} characters")
    return (None if probs else targets), probs, voo


def consistency_notes(decision: dict) -> list[str]:
    """Signals, not rules: a kept stock the model's own assessment does not favour. Recorded, never enforced."""
    a = {str(x.get("symbol", "")).upper(): x for x in decision.get("assessments") or [] if isinstance(x, dict)}
    out = []
    for p in decision.get("positions") or []:
        if p.get("action") in KEPT:
            x = a.get(str(p.get("symbol", "")).upper()) or {}
            e, pr = _num(x.get("expected_excess_pct_3m")), _num(x.get("p_positive_excess"))
            if (e is not None and e <= 0) or (pr is not None and pr < Decimal("0.5")):
                out.append(f"{p.get('symbol')}: kept with expected excess {e} and probability {pr}")
    return out
