"""Blinded review of the model's financial claims (frozen rubric). Automatic validity is not factual accuracy.

The contract-v2 validator checks that every number in a claim appears in the sentences it cites. It cannot check
meaning: it accepted "EPS fell from 1.30 to 1.02" when the cited sentence said EPS rose from 1.02 to 1.30. So every
answer's claims and reported figures are exported for a person to judge, blinded to the decision, the forecast,
any trade and every market outcome, under the rubric below. Original raw answers are kept unchanged in the
ledger and the journal; adjudications are a separate file.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

RUBRIC_VERSION = "1"
RUBRIC = {
    "subject": "the claim is about the same company, segment and metric as the cited sentence",
    "direction": "rose/fell, beat/missed, raised/lowered match the cited sentence",
    "period": "the same fiscal period (quarter, year, year-to-date) as the cited sentence",
    "basis": "GAAP versus adjusted (non-GAAP) matches the cited sentence",
    "sign": "a loss is a loss and a gain a gain (negative values keep their sign)",
    "unit": "per share, thousands, millions, billions or percent as in the cited sentence",
}
VERDICTS = ("supported", "unsupported", "cannot_judge")
FORBIDDEN_IN_PACKET = {"decision", "action", "trade", "instrument", "p_up_by_close", "expected_net_return_pct",
                       "outcome", "pnl_usd", "up", "market_outcome", "confidence", "rationale", "entry", "exit"}


def review_id(seed: str, event_id: str) -> str:
    return hashlib.sha256(f"{seed}|{event_id}".encode()).hexdigest()[:12]


def packet(decision_records: list[dict], seed: str) -> list[dict]:
    """One blinded item per model answer that has a thesis (valid or not): claims, cited sentences, reported figures."""
    out = []
    for r in decision_records:
        p = r["payload"]
        if p["decider"] != "llm":
            continue
        th = p["decision"].get("thesis") or {}
        facts = th.get("facts") or []
        reported = th.get("reported_refs") or []
        if not facts and not reported:
            continue
        out.append({"review_id": review_id(seed, p["event_id"]), "event_id": p["event_id"],
                    "claims": [{"index": i, "claim": f.get("claim"), "cited": f.get("evidence", {})}
                               for i, f in enumerate(facts)],
                    "reported": [{"metric": x.get("metric"), "value": x.get("value"), "unit": x.get("unit"),
                                  "cited_ref": x.get("ref")} for x in reported],
                    "automatically_valid": bool(p["decision"].get("valid")) and not p["decision"].get("problems")})
    out.sort(key=lambda x: x["review_id"])
    return out


def check_blind(items: list[dict]) -> None:
    def walk(x):
        if isinstance(x, dict):
            bad = FORBIDDEN_IN_PACKET & set(x)
            if bad:
                raise ValueError(f"review packet is not blinded: {sorted(bad)}")
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(items)


def write_packet(path: Path, decision_records: list[dict], seed: str) -> int:
    items = packet(decision_records, seed)
    check_blind(items)
    Path(path).write_text(json.dumps({"rubric_version": RUBRIC_VERSION, "rubric": RUBRIC, "verdicts": VERDICTS,
                                      "instructions": "Judge each claim only against its cited sentences. Do not look "
                                      "up prices or what happened next.", "items": items}, indent=1))
    return len(items)


def score(items: list[dict], adjudications: list[dict]) -> dict:
    """Per-answer: automatically valid vs verified accurate (every claim and figure judged 'supported')."""
    by = {}
    for a in adjudications:
        if a.get("verdict") not in VERDICTS:
            raise ValueError(f"unknown verdict {a.get('verdict')!r}")
        for k in RUBRIC:
            if a.get(k) not in (True, False, None):
                raise ValueError(f"rubric field {k} must be true, false or null")
        by.setdefault(a["review_id"], []).append(a)
    rows = []
    for it in items:
        adj = by.get(it["review_id"], [])
        n_needed = len(it["claims"]) + len(it["reported"])
        judged = len(adj)
        unsupported = sum(a["verdict"] == "unsupported" for a in adj)
        accurate: Optional[bool] = None
        if judged >= n_needed and n_needed:
            accurate = all(a["verdict"] == "supported" for a in adj)
        rows.append({"review_id": it["review_id"], "event_id": it["event_id"],
                     "automatically_valid": it["automatically_valid"], "judged": judged, "needed": n_needed,
                     "unsupported": unsupported, "verified_accurate": accurate})
    done = [r for r in rows if r["verified_accurate"] is not None]
    return {"rubric_version": RUBRIC_VERSION, "answers": len(rows), "fully_reviewed": len(done),
            "verified_accurate": sum(r["verified_accurate"] for r in done),
            "automatically_valid_but_unsupported": sum(r["automatically_valid"] and r["unsupported"] > 0 for r in rows),
            "rows": rows}
