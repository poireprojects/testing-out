"""The three questions, kept separate (research/PREREGISTRATION-2026-11-07-phase5.md).

Q1 understanding  - are the model's answers valid and grounded, and are the figures it reports correct?
                    (validity rate, grounding failures, extraction accuracy against XBRL filed later)
Q2 prediction     - does its probability that the stock rises from 10:00:30 to 15:45 **on the session the decision
                    targets** beat a constant base rate? (Brier score, every event it answered, traded or not)
Q3 trading        - after spreads, slippage, fees and latency, does acting on it beat no trade and the baselines?

Evaluation protocol v2 (this file): predictions are scored on the decision's recorded target session (a decision
written on Saturday 2026-11-07 for the session of Monday 2026-11-09 is scored on 11-09), preferably from the
ledger's own market-outcome records. Protocol v1 scored on the decision timestamp's date, which is wrong for any
decision written after the close, before the open or on a non-trading day. A forward decision without a recorded
target session is not scored. As-of baselines are reported separately from real-time decisions.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time as dtime
from typing import Callable, Optional

from dkapp.clock import ET

from .ledger import Ledger

PROTOCOL = "2"


def target_session(rec: dict, mode: str) -> Optional[date]:
    """The session a decision applies to: its recorded `session`; for a replay decision without one (v1 ledgers),
    the decision day, because replays decide at 10:00 on the session itself; for a forward decision without
    one, None (it cannot be scored honestly)."""
    s = rec["payload"]["decision"].get("session")
    if s:
        return date.fromisoformat(s)
    if mode == "replay":
        return datetime.fromisoformat(rec["payload"]["decision_time"]).astimezone(ET).date()
    return None


def _up(prices, sym: str, day: date) -> Optional[int]:
    a = prices.quote(sym, datetime.combine(day, dtime(10, 0, 30), ET))
    b = prices.quote(sym, datetime.combine(day, dtime(15, 45), ET))
    return None if a is None or b is None else int(b.mid > a.mid)


def base_rate(source, fold: str = "dev") -> float:
    ups = [u for r in source.plan if r["fold"] == fold
           for u in [_up(source.prices, r["symbol"], date.fromisoformat(r["day"]))] if u is not None]
    return sum(ups) / len(ups)


def _filing_day(ev: dict) -> Optional[date]:
    t = [i["available_at"] for i in ev.get("items", []) if i["kind"] == "filing"]
    return datetime.fromisoformat(t[0]).astimezone(ET).date() if t else None


def evaluate(ledger: Ledger, source=None, edgar=None, base: Optional[float] = None, prices=None,
             cik_of: Optional[Callable[[str], int]] = None, registered: bool = False) -> dict:
    """`registered` is True only for a forward ledger collected under an active pre-registration."""
    prices = prices or (source.prices if source is not None else None)
    if cik_of is None and source is not None:
        cik_of = lambda sym: int(source.companies[sym]["cik"])  # noqa: E731
    decs = ledger.of_type("decision")
    evidence = {r["payload"]["event_id"]: r["payload"] for r in ledger.of_type("evidence")}
    market = {m["payload"]["event_id"]: m["payload"] for m in ledger.of_type("market_outcome")}
    outs = {o["payload"]["decision_seq"]: o["payload"] for o in ledger.of_type("outcome")}
    res: dict = {"mode": ledger.mode, "protocol": PROTOCOL,
                 "countsAsEvidence": ledger.mode == "forward" and registered}

    # Q1
    llm = [r for r in decs if r["payload"]["decider"] == "llm"]
    probs = [p for r in llm for p in r["payload"]["decision"].get("problems", [])]
    q1 = {"answers": len(llm),
          "valid": sum(r["payload"]["decision"].get("valid", False) for r in llm),
          "modelFailures": sum(bool(r["payload"]["decision"].get("failure")) for r in llm),
          "schemaFailures": sum(any(p.startswith(("schema", "not JSON")) for p in r["payload"]["decision"].get("problems", []))
                                for r in llm),
          "quoteNotFound": sum("quote not found" in p for p in probs),
          "numbersNotInQuote": sum("numbers not in its" in p for p in probs),
          "reportedNotInEvidence": sum("does not appear in" in p for p in probs)}
    if edgar is not None and cik_of is not None:
        from .sources import xbrl_truth
        checks = defaultdict(lambda: [0, 0])
        for r in llm:
            rep = ((r["payload"]["decision"].get("thesis") or {}).get("reported")) or {}
            sym = r["payload"]["event_id"].split(":")[0]
            day = _filing_day(evidence.get(r["payload"]["event_id"], {})) or \
                datetime.fromisoformat(r["payload"]["decision_time"]).date()
            try:
                truth = xbrl_truth(edgar, cik_of(sym), day)
            except Exception:
                continue
            for k, tol in (("eps_diluted", lambda a, b: abs(a - b) <= 0.011),
                           ("revenue_usd_millions", lambda a, b: abs(a - b) <= 0.005 * abs(b))):
                if rep.get(k) is not None and truth.get(k) is not None:
                    checks[k][0] += int(tol(rep[k], truth[k]))
                    checks[k][1] += 1
        q1["extraction"] = {k: {"correct": c, "checked": n, "accuracy": c / n if n else None} for k, (c, n) in checks.items()}
    res["Q1_understanding"] = q1

    # Q2 (on the target session)
    pts, missing, no_session = [], 0, 0
    for r in llm:
        p = r["payload"]["decision"].get("p_up_by_close")
        if p is None:
            continue
        day = target_session(r, ledger.mode)
        if day is None:
            no_session += 1
            continue
        m = market.get(r["payload"]["event_id"])
        if m is not None and m["session"] == day.isoformat():
            u = m["up"]
        elif prices is not None:
            u = _up(prices, r["payload"]["event_id"].split(":")[0], day)
        else:
            u = None
        if u is None:
            missing += 1
            continue
        pts.append((float(p), u))
    q2 = {"n": len(pts), "noTargetSession": no_session, "noPrices": missing}
    if pts:
        b = base if base is not None else 0.5
        q2 |= {"baseRate": b, "brierModel": sum((p - u) ** 2 for p, u in pts) / len(pts),
               "brierBaseRate": sum((b - u) ** 2 for _, u in pts) / len(pts), "upRate": sum(u for _, u in pts) / len(pts)}
    res["Q2_prediction"] = q2

    # Q3
    q3 = {}
    by = defaultdict(list)
    for r in decs:
        name = r["payload"]["decider"] + (" (as of 10:00)" if r["payload"].get("asof") else "")
        by[name].append(r)
    for k, rs in by.items():
        tr = [outs[r["seq"]] for r in rs if r["seq"] in outs and not outs[r["seq"]].get("unscorable")]
        net = [t["net_pct"] for t in tr]
        q3[k] = {"events": len(rs), "proposed": sum(r["payload"]["decision"]["action"] == "long" for r in rs),
                 "trades": len(tr), "meanNetPct": sum(net) / len(net) if net else None,
                 "totalPnlUsd": sum(t["pnl_usd"] for t in tr), "winners": sum(x > 0 for x in net)}
    res["Q3_trading"] = q3
    return res
