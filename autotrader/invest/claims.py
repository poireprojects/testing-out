"""Blinded claim review for genuine decisions (defined now, used only in a future genuine run).

The contract's checks (`contract.validate`) verify quotation mechanics: a quote appears verbatim in the cited item
and every number in a figure appears in that quote. They do not verify meaning. A thesis can quote "revenue rose
5%" and then argue from a decline, mix quarterly and annual figures, confuse millions with billions, or misstate
its own valuation row. So every claim cited by a valid genuine decision gets a human review:

* reviewed claims are the typed `fact`, `calculation` and `inferred_expectation` claims of contract v2 (forecasts
  and assumptions are the model's own and are not factual claims);
* the packet (`packet`) shows the reviewer the claim, the cited text, the quote, the figures and a calculation's
  inputs, and nothing about the action, the weights, any later price or any outcome (`check_blind` from
  autotrader/review.py);
* the rubric is autotrader/review.py's (subject, direction, period, GAAP vs adjusted, sign, unit) plus valuation;
* each verdict is its own append-only ledger record (`claim_review|<id>`). A review never edits the frozen decision,
  never changes its trades and never removes its trading outcome; unsupported claims stay listed in the report;
* the report counts a genuine record as complete only when every such claim has a verdict.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from ..review import RUBRIC, VERDICTS, check_blind
from .ledger import InvestLedger, read_records

RUBRIC_VERSION = "invest-1"
CRITERIA = dict(RUBRIC, valuation="price, market value, P/E and price/sales statements, and comparisons with VOO, "
                                  "match the cited valuation row")
REVIEWED_TYPES = ("fact", "calculation", "inferred_expectation")


def claims(records: list[dict], genuine_only: bool = True) -> list[dict]:
    """One review item per fact, calculation or inferred expectation of each valid decision."""
    seed = next(r for r in records if r["key"] == "open")["hash"]
    out = []
    for r in records:
        p = r["payload"]
        if r["type"] != "decision" or p["status"] != "valid" or (genuine_only and not p["genuine"]):
            continue
        for pos in p["decision"]["positions"]:
            for j, c in enumerate(pos.get("claims") or []):
                if c.get("type") not in REVIEWED_TYPES:
                    continue
                rid = hashlib.sha256(f"{seed}|{p['slot']}|{pos['symbol']}|{j}".encode()).hexdigest()[:12]
                out.append({"review_id": rid, "slot": p["slot"], "month": p["month"], "symbol": pos["symbol"],
                            "type": c["type"], "text": c["text"], "citations": c.get("evidence") or [],
                            "inputs": c.get("inputs") or [], "expression": c.get("expression") or "",
                            "result": c.get("result"), "genuine": p["genuine"], "pack_file": p.get("pack_file")})
    return out


def packet(state_dir: Path, genuine_only: bool = True) -> dict:
    """What a reviewer sees: no action, weight, later price or outcome. `genuine_only=False` includes fixture claims
    (for testing the review mechanics; fixture claims are never part of a result)."""
    import json
    state_dir = Path(state_dir)
    items = []
    for c in claims(read_records(state_dir / "ledger.jsonl"), genuine_only):
        pack = json.loads((state_dir / c["pack_file"]).read_text())
        text = {i["id"]: i["text"] for i in pack["items"]}
        items.append({"review_id": c["review_id"], "month": c["month"], "company": c["symbol"], "kind": c["type"],
                      "claim": c["text"],
                      "citations": [{"cited_text": text.get(x["id"]), "quote": x["quote"], "figures": x["figures"]}
                                    for x in c["citations"]],
                      "inputs": [{"name": x["name"], "value": x["value"],
                                  "cited_text": "assumption" if x["source_id"] == "assumption" else text.get(x["source_id"])}
                                 for x in c["inputs"]],
                      "expression": c["expression"], "result": c["result"]})
    check_blind(items)
    return {"rubric_version": RUBRIC_VERSION, "criteria": CRITERIA, "verdicts": VERDICTS,
            "instructions": "Judge each statement and figure only against the cited text. Do not look up prices, "
                            "news or what happened next.", "items": items}


def record_review(state_dir: Path, review_id: str, verdict: str, failed: list[str], reviewer: str, note: str,
                  at: datetime) -> dict:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    bad = set(failed) - set(CRITERIA)
    if bad:
        raise ValueError(f"unknown criteria {sorted(bad)}")
    if verdict == "supported" and failed:
        raise ValueError("a supported claim fails no criterion")
    path = Path(state_dir) / "ledger.jsonl"
    if review_id not in {c["review_id"] for c in claims(read_records(path), genuine_only=False)}:
        raise ValueError(f"no claim {review_id} in this ledger")
    with InvestLedger.open_existing(path, at) as led:
        rec, _ = led.put(f"claim_review|{review_id}", "claim_review",
                         {"review_id": review_id, "verdict": verdict, "failed": sorted(failed), "reviewer": reviewer,
                          "note": note, "rubric_version": RUBRIC_VERSION, "blinded": True}, at)
    return rec
