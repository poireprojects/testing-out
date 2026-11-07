"""The three questions, kept separate (research/PREREGISTRATION-2026-11-07-phase5.md):

Q1 understanding  - are the model's answers valid and grounded, and are the figures it reports correct?
                    (validity rate, grounding failures, extraction accuracy against XBRL filed later)
Q2 prediction     - does its probability that the stock rises from entry to 15:45 beat a constant base rate?
                    (Brier score, scored on every event it saw, traded or not)
Q3 trading        - after spreads, slippage, fees and latency, does acting on it beat no trade and the baselines?
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time as dtime
from typing import Optional

from dkapp.clock import ET

from .ledger import Ledger


def _up(prices, sym: str, day: date) -> Optional[int]:
    a = prices.quote(sym, datetime.combine(day, dtime(10, 0, 30), ET))
    b = prices.quote(sym, datetime.combine(day, dtime(15, 45), ET))
    return None if a is None or b is None else int(b.mid > a.mid)


def base_rate(source, fold: str = "dev") -> float:
    ups = [u for r in source.plan if r["fold"] == fold
           for u in [_up(source.prices, r["symbol"], date.fromisoformat(r["day"]))] if u is not None]
    return sum(ups) / len(ups)


def evaluate(ledger: Ledger, source=None, edgar=None, base: Optional[float] = None) -> dict:
    decs = ledger.of_type("decision")
    outs = {o["payload"]["decision_seq"]: o["payload"] for o in ledger.of_type("outcome")}
    res: dict = {"mode": ledger.mode, "independentEvidence": ledger.mode == "forward"}

    # Q1
    llm = [r for r in decs if r["payload"]["decider"] == "llm"]
    probs = [p for r in llm for p in r["payload"]["decision"].get("problems", [])]
    q1 = {"answers": len(llm),
          "valid": sum(r["payload"]["decision"].get("valid", False) for r in llm),
          "modelFailures": sum(bool(r["payload"]["decision"].get("failure")) for r in llm),
          "schemaFailures": sum(any(p.startswith(("schema", "not JSON")) for p in r["payload"]["decision"].get("problems", []))
                                for r in llm),
          "quoteNotFound": sum("quote not found" in p for p in probs),
          "numbersNotInQuote": sum("numbers not in its quote" in p for p in probs),
          "reportedNotInEvidence": sum("does not appear in the evidence" in p for p in probs)}
    if edgar is not None and source is not None:
        from .sources import xbrl_truth
        checks = defaultdict(lambda: [0, 0])
        for r in llm:
            d = r["payload"]["decision"]
            rep = (d.get("thesis") or {}).get("reported") or {}
            sym, acc = r["payload"]["event_id"].split(":")
            day = datetime.fromisoformat(r["payload"]["decision_time"]).date()
            try:
                truth = xbrl_truth(edgar, int(source.companies[sym]["cik"]), day)
            except Exception:
                continue
            for k, tol in (("eps_diluted", lambda a, b: abs(a - b) <= 0.011),
                           ("revenue_usd_millions", lambda a, b: abs(a - b) <= 0.005 * abs(b))):
                if rep.get(k) is not None and truth.get(k) is not None:
                    checks[k][0] += int(tol(rep[k], truth[k]))
                    checks[k][1] += 1
        q1["extraction"] = {k: {"correct": c, "checked": n, "accuracy": c / n if n else None} for k, (c, n) in checks.items()}
    res["Q1_understanding"] = q1

    # Q2
    if source is not None:
        pts = []
        for r in llm:
            p = r["payload"]["decision"].get("p_up_by_close")
            sym = r["payload"]["event_id"].split(":")[0]
            u = _up(source.prices, sym, datetime.fromisoformat(r["payload"]["decision_time"]).date())
            if p is not None and u is not None:
                pts.append((float(p), u))
        if pts:
            b = base if base is not None else 0.5
            res["Q2_prediction"] = {"n": len(pts), "baseRate": b,
                                    "brierModel": sum((p - u) ** 2 for p, u in pts) / len(pts),
                                    "brierBaseRate": sum((b - u) ** 2 for _, u in pts) / len(pts),
                                    "upRate": sum(u for _, u in pts) / len(pts)}

    # Q3
    q3 = {}
    by = defaultdict(list)
    for r in decs:
        by[r["payload"]["decider"]].append(r)
    for k, rs in by.items():
        tr = [outs[r["seq"]] for r in rs if r["seq"] in outs and not outs[r["seq"]].get("unscorable")]
        net = [t["net_pct"] for t in tr]
        q3[k] = {"events": len(rs), "proposed": sum(r["payload"]["decision"]["action"] == "long" for r in rs),
                 "trades": len(tr), "meanNetPct": sum(net) / len(net) if net else None,
                 "totalPnlUsd": sum(t["pnl_usd"] for t in tr), "winners": sum(x > 0 for x in net)}
    res["Q3_trading"] = q3
    return res
