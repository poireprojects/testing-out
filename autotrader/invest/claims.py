"""Blinded claim review for genuine decisions (defined now, used only in a future genuine run).

The contract's checks (`contract.validate`) verify quotation mechanics: a quote appears verbatim in the cited item
and every number in a figure appears in that quote. They do not verify meaning. A thesis can quote "revenue rose
5%" and then argue from a decline, mix quarterly and annual figures, confuse millions with billions, or misstate
its own valuation row. So every claim cited by a valid genuine decision gets a human review:

* the packet (`packet`) shows the reviewer the cited text, the quote, the figures and the thesis statements, and
  nothing about the action, the weights, any later price or any outcome (`check_blind` from autotrader/review.py);
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
REVIEWED_ACTIONS = ("buy", "increase", "hold")


def claims(records: list[dict], genuine_only: bool = True) -> list[dict]:
    """One claim per citation of each valid decision's buy, increase or hold."""
    seed = next(r for r in records if r["key"] == "open")["hash"]
    out = []
    for r in records:
        p = r["payload"]
        if r["type"] != "decision" or p["status"] != "valid" or (genuine_only and not p["genuine"]):
            continue
        for pos in p["decision"]["positions"]:
            if pos["action"] not in REVIEWED_ACTIONS:
                continue
            for j, c in enumerate(pos["evidence"]):
                rid = hashlib.sha256(f"{seed}|{p['slot']}|{pos['symbol']}|{j}".encode()).hexdigest()[:12]
                out.append({"review_id": rid, "slot": p["slot"], "month": p["month"], "symbol": pos["symbol"],
                            "evidence_id": c["id"], "quote": c["quote"], "figures": c["figures"],
                            "statements": {k: pos[k] for k in ("why_outperform_voo", "main_downside", "invalidation",
                                                               "thesis_still_holds") if pos.get(k)},
                            "pack_file": p.get("pack_file")})
    return out


def packet(state_dir: Path) -> dict:
    """What a reviewer sees: no action, weight, later price or outcome."""
    import json
    state_dir = Path(state_dir)
    items = []
    for c in claims(read_records(state_dir / "ledger.jsonl")):
        pack = json.loads((state_dir / c["pack_file"]).read_text())
        cited = next((i["text"] for i in pack["items"] if i["id"] == c["evidence_id"]), None)
        items.append({"review_id": c["review_id"], "month": c["month"], "company": c["symbol"],
                      "cited_text": cited, "quote": c["quote"], "figures": c["figures"], "statements": c["statements"]})
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
