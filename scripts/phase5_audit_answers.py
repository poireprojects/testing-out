"""Audit of the language model's invalid answers (phase 5, contract v1): genuine fabrication versus avoidable
formatting and schema problems.

    python scripts/phase5_audit_answers.py LEDGER [LEDGER ...] > OUT.json

Every answer is re-read leniently (plain json.loads, no schema), so answers that failed the schema are still
checked for fabricated content. Each problem gets one class:

GENUINE FACTUAL ERROR (the answer states something the evidence does not support)
  quote_content_absent     the quote's numbers, or most of its words, appear in no evidence item
  claim_number_absent      a claim states a number found nowhere in the evidence and not derivable from its quote
  reported_value_absent    a reported EPS/revenue figure appears nowhere in the evidence in any unit (invented or
                           misread, e.g. 1.87 when the release says 1.88)
  reported_wrong_period    a reported figure is real but belongs to an earlier quarter in the XBRL history
MISATTRIBUTED (real information, wrong label; no false statement of fact)
  quote_wrong_item         the quote is verbatim in a different evidence item than the one cited
  claim_number_elsewhere   the claim's number is real but comes from another passage
  reported_wrong_unit      a figure stated in billions entered in the millions field (6.5 for $6.5 billion)
  reported_other_source    the right figure for the right quarter, taken from the XBRL history, not the release
AVOIDABLE FORMAT (no information problem)
  quote_near_verbatim      small copying slips, reordered words or a literal \u2019 escape; all numbers real
  claim_number_derived     a claim number computed from the quote (sum, difference, rescaling, rounding, a date)
  quote_too_long / list_too_long / text_too_short_or_long / out_of_range_field
  truncated_output         the answer ran out of output tokens (not parseable)
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from collections import Counter

TRANS = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").translate(TRANS)).strip().lower()


def nums(s: str) -> set[str]:
    out = set()
    for n in NUM.findall((s or "").translate(TRANS)):
        n = n.replace(",", "").rstrip(".")
        out.add(n)
        if "." in n:
            out.add(n.rstrip("0").rstrip("."))
    return out


def unescape(s: str) -> str:
    """The model sometimes writes a literal \\u2019 instead of the character: decode such escapes."""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s or "")


def bag_coverage(quote: str, text: str) -> float:
    q = re.findall(r"[a-z0-9.%$-]+", norm(quote))
    t = set(re.findall(r"[a-z0-9.%$-]+", norm(text)))
    return sum(w in t for w in q) / len(q) if q else 0.0


def coverage(quote: str, text: str) -> float:
    """Share of the quote's words found, in order, in the text (difflib matching blocks)."""
    q, t = norm(quote).split(), norm(text).split()
    if not q:
        return 0.0
    sm = difflib.SequenceMatcher(None, q, t, autojunk=False)
    return sum(b.size for b in sm.get_matching_blocks()) / len(q)


def derivable(n: str, quote_nums: set[str], quote: str = "") -> bool:
    """A claim number computed from the quote: sum, difference, ratio, percent change, rescaling (millions <->
    billions), rounding, or part of a date written out in the quote."""
    try:
        x = float(n)
        vals = [float(v) for v in quote_nums]
    except ValueError:
        return False
    months = "january february march april may june july august september october november december".split()
    if any(m in norm(quote) for m in months) and (1 <= x <= 31 or 2000 <= x <= 2100):
        return True
    for a in vals:
        for c in (a / 1000, a * 1000, a / 1e6):
            for d in (0, 1, 2, 3):
                if abs(round(c, d) - x) < 1e-9 or abs(round(a, d) - x) < 1e-9:
                    return True
    for a in vals:
        for b in vals:
            if a == b:
                continue
            for c in (a + b, a - b, b - a, (a / b - 1) * 100 if b else None, a / b if b else None):
                if c is not None and abs(abs(c) - abs(x)) <= max(0.051, abs(x) * 0.01):
                    return True
    return False


def audit_answer(raw: str, items: dict[str, str], release_ids: set[str]) -> list[str]:
    try:
        a = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ["truncated_output"]
    out = []
    corpus = " ".join(items.values())
    all_nums = nums(corpus)
    for f in a.get("facts") or []:
        q, eid, claim = unescape(f.get("quote", "")), f.get("evidence_id", ""), f.get("claim", "")
        if len(q) > 400:
            out.append("quote_too_long")
        if norm(q) and norm(q) not in norm(items.get(eid, "")):
            other = [k for k, v in items.items() if k != eid and norm(q) in norm(v)]
            if other:
                out.append("quote_wrong_item")
            else:
                cov = max((max(coverage(q, v), bag_coverage(q, v)) for v in items.values()), default=0.0)
                missing = nums(q) - all_nums
                if missing or cov < 0.8:
                    out.append("quote_content_absent")
                else:
                    out.append("quote_near_verbatim")
        extra = nums(claim) - nums(q)
        for n in extra:
            if n in all_nums:
                out.append("claim_number_elsewhere")
            elif derivable(n, nums(q), q):
                out.append("claim_number_derived")
            else:
                out.append("claim_number_absent")
        if len(claim) < 3 or len(claim) > 300:
            out.append("text_too_short_or_long")
    rel = " ".join(v for k, v in items.items() if k in release_ids).translate(TRANS).replace(",", "")
    rel_nums = nums(rel)
    hist = " ".join(v for k, v in items.items() if k not in release_ids and "Quarter ended" in v)
    quarters = re.findall(r"Quarter ended (\d{4}-\d{2}-\d{2}): ([^.]*(?:\.\d+[^.]*)*)\.", hist)
    latest = max((q for q, _ in quarters), default=None)
    rep = a.get("reported") or {}
    for key in ("eps_diluted", "revenue_usd_millions"):
        v = rep.get(key)
        if v is None:
            continue
        if key == "eps_diluted":
            forms = {f"{v:g}", f"{v:.2f}"}
        else:                                            # a bare "4" must not match a 3.839 billion figure
            forms = {f"{v:g}", f"{v:.0f}"} if v >= 100 else {f"{v:g}"}
        hist_forms = forms | ({f"{v * 1000:.0f}"} if key == "revenue_usd_millions" and v < 100 else set())
        if key == "revenue_usd_millions" and v < 100 and re.search(r"(?<![\d.])" + re.escape(f"{v:g}") + r"\s*billion", rel.lower()):
            out.append("reported_wrong_unit")
        elif forms & rel_nums:
            continue                                     # present in the release as written
        elif key == "revenue_usd_millions" and ({f"{v / 1000:g}", f"{v / 1000:.1f}", f"{v / 1000:.2f}"} & rel_nums):
            continue                                     # the release states it in billions: a correct figure
        else:
            hits = [qe for qe, line in quarters if hist_forms & nums(line.replace(",", ""))]
            if hits and latest in hits:
                out.append("reported_other_source")
            elif hits:
                out.append("reported_wrong_period")
            else:
                out.append("reported_value_absent")
    for k in ("interpretations", "uncertainties", "invalidation", "reasons_to_avoid"):
        if len(a.get(k) or []) > 6:
            out.append("list_too_long")
    ent = a.get("entry") or {}
    if isinstance(ent.get("max_spread_bps"), (int, float)) and not (0 <= ent["max_spread_bps"] <= 100):
        out.append("out_of_range_field")
    return out


def audit_answer_v2(raw: str, items: dict[str, str], kinds: dict[str, str]) -> list[str]:
    """Contract v2: facts cite sentence ids; the same classes as v1 where they apply."""
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parents[1]))
    from datetime import datetime as _dt, timezone as _tz
    from autotrader.decision import sentences
    from autotrader.evidence import Evidence, EvidencePack
    t0 = _dt(2000, 1, 1, tzinfo=_tz.utc)
    pack = EvidencePack("x", "X", "Y", t0, [Evidence(k, kinds[k], "", t0, v) for k, v in items.items()])
    sents = sentences(pack)
    try:
        a = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ["truncated_output"]
    out = []
    all_nums = nums(" ".join(items.values()))
    for f in a.get("facts") or []:
        refs = [r for r in f.get("refs", []) if r in sents]
        if len(refs) < len(f.get("refs", [])):
            out.append("unknown_sentence")
        cited = nums(" ".join(sents[r] for r in refs))
        for n in nums(f.get("claim", "")) - cited:
            if n in all_nums:
                out.append("claim_number_elsewhere")
            elif derivable(n, cited, " ".join(sents[r] for r in refs)):
                out.append("claim_number_derived")
            else:
                out.append("claim_number_absent")
    rel_ids = {k for k, v in kinds.items() if v in ("filing", "vendor_headline")}
    rel_nums = nums(" ".join(items[k] for k in rel_ids))
    hist = " ".join(v for k, v in items.items() if kinds[k] == "financials")
    for r in a.get("reported") or []:
        v, ref = r.get("value"), r.get("ref", "")
        if v is None:
            continue
        forms = {f"{abs(v):g}", f"{abs(v):.2f}"} | ({f"{abs(v):.0f}"} if abs(v) >= 100 else set())
        if ref.split(".")[0] not in rel_ids:
            out.append("reported_other_source")
        elif forms & nums(sents.get(ref, "")):
            if r.get("metric") == "revenue" and r.get("unit") == "usd_millions" and abs(v) < 100:
                out.append("reported_wrong_unit")
        elif any({f"{abs(v) / k:g}", f"{abs(v) / k:.2f}", f"{abs(v) / k:.3f}"} & nums(sents.get(ref, ""))
                 for k in (1e9, 1e6, 1e3, 1e-3)):
            out.append("reported_wrong_unit")             # e.g. 2.155e9 written for "$2.155 billion"
        elif forms & rel_nums:
            out.append("reported_wrong_sentence")
        elif forms & nums(hist.replace(",", "")):
            out.append("reported_wrong_period")
        else:
            out.append("reported_value_absent")
    return out


FAB = {"quote_content_absent", "claim_number_absent", "reported_value_absent", "reported_wrong_period"}
MIS = {"quote_wrong_item", "reported_wrong_unit", "claim_number_elsewhere", "reported_other_source",
       "reported_wrong_sentence"}


def main(paths: list[str]) -> int:
    rows = []
    for p in paths:
        recs = [json.loads(line) for line in open(p)]
        ev = {r["payload"]["event_id"]: r["payload"] for r in recs if r["type"] == "evidence"}
        for r in recs:
            if r["type"] != "decision" or r["payload"]["decider"] != "llm":
                continue
            d = r["payload"]["decision"]
            raw = (d.get("model") or {}).get("raw")
            if raw is None:
                continue                                         # model unavailable: infrastructure, not an answer
            pack = ev[r["payload"]["event_id"]]
            items = {i["id"]: i["text"] for i in pack["items"]}
            rel = {i["id"] for i in pack["items"] if i["kind"] in ("filing", "vendor_headline")}
            if (d.get("model") or {}).get("contract") == "2":
                found = audit_answer_v2(raw, items, {i["id"]: i["kind"] for i in pack["items"]})
            else:
                found = audit_answer(raw, items, rel)
            cls = "factual_error" if FAB & set(found) else "misattributed" if MIS & set(found) else \
                "format_only" if found else "clean"
            rows.append({"ledger": p, "event": r["payload"]["event_id"].split(":")[0], "validV1": d["valid"],
                         "contract": (d.get("model") or {}).get("contract", "1"), "v1Problems": d.get("problems", []), "auditFindings": sorted(Counter(found).items()),
                         "class": cls})
    summary = {"answers": len(rows), "validV1": sum(r["validV1"] for r in rows),
               "byClass": dict(Counter(r["class"] for r in rows)),
               "invalidV1ByClass": dict(Counter(r["class"] for r in rows if not r["validV1"])),
               "findings": dict(Counter(f for r in rows for f, n in r["auditFindings"] for _ in range(n)))}
    json.dump({"summary": summary, "answers": rows}, sys.stdout, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
