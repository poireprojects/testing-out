"""The owner's daily report: what was decided, what was simulated, what was avoided, how reliable the system was,
and every safety event. Built only from the ledger, after verifying its hash chain."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from stockpilot.clock import ET

from .ledger import Ledger


def _day(r: dict) -> str:
    p = r["payload"]
    t = p.get("decision_time") or r["recorded_at"]
    return datetime.fromisoformat(t).astimezone(ET).date().isoformat()


def daily_report(ledger: Ledger, day: str | None = None) -> str:
    ok, problems = ledger.verify()
    decs = [r for r in ledger.of_type("decision") if day is None or _day(r) == day]
    seqs = {r["seq"] for r in decs}
    risks = [r for r in ledger.of_type("risk") if r["payload"]["decision_seq"] in seqs]
    outs = [r for r in ledger.of_type("outcome") if r["payload"]["decision_seq"] in seqs]
    t0 = decs[0]["recorded_at"] if decs else None
    safety = [r for r in ledger.of_type("safety_event") if day is None or _day(r) == day]
    title = day or "all days"
    L = [f"# Autonomous trader — daily report ({title})", "",
         f"Mode: **{ledger.mode}** (simulation only; no order was sent anywhere). "
         + ("Replay runs use previously examined history: **development only, not evidence.**"
            if ledger.mode == "replay" else "Forward: every decision was written before its outcome existed."),
         f"Ledger integrity: **{'verified' if ok else 'FAILED'}** ({len(ledger.records)} records)"
         + ("" if ok else " — " + "; ".join(problems[:3])), ""]

    by = defaultdict(list)
    for r in decs:
        by[r["payload"]["decider"]].append(r["payload"]["decision"])
    L += ["## Decisions", "", "| decider | events | proposed trades | no trade | invalid / failed answers |",
          "|---|---:|---:|---:|---:|"]
    for k, v in by.items():
        L.append(f"| {k} | {len(v)} | {sum(d['action'] == 'long' for d in v)} | {sum(d['action'] != 'long' for d in v)} "
                 f"| {sum(not d.get('valid', True) for d in v)} |")

    L += ["", "## Simulated trades (after spreads, slippage and fees)", ""]
    scored = [o["payload"] for o in outs if not o["payload"].get("unscorable")]
    if scored:
        L += ["| decider | instrument | exit | net per trade | P/L |", "|---|---|---|---:|---:|"]
        for p in scored:
            L.append(f"| {p['decider']} | {p['symbol']} | {p['reason']} | {p['net_pct']:+.2f}% | ${p['pnl_usd']:+.3f} |")
        L += ["", "| decider | trades | mean net | total P/L | winners |", "|---|---:|---:|---:|---:|"]
        agg = defaultdict(list)
        for p in scored:
            agg[p["decider"]].append(p)
        for k, v in agg.items():
            L.append(f"| {k} | {len(v)} | {sum(x['net_pct'] for x in v) / len(v):+.2f}% | "
                     f"${sum(x['pnl_usd'] for x in v):+.3f} | {sum(x['net_pct'] > 0 for x in v)} |")
    else:
        L.append("No simulated trades.")
    flagged = [p for p in scored if p.get("flags")]
    if flagged:
        L.append(f"\n{len(flagged)} trade(s) carry simulation flags (see the ledger).")

    L += ["", "## Avoided opportunities", ""]
    reasons = Counter()
    for r in decs:
        d = r["payload"]["decision"]
        if d["action"] != "long" and r["payload"]["decider"] not in ("no_trade",):
            reasons[(r["payload"]["decider"], (d.get("reasons_to_avoid") or [d.get("rationale", "")])[0][:110])] += 1
    for (k, why), n in reasons.most_common(8):
        L.append(f"* {k}: {why} ({n})")
    rej = Counter()
    for r in risks:
        p = r["payload"]
        if not p.get("approved") and not p.get("no_order"):
            rej[(p["decider"], (p.get("reasons") or ["?"])[0][:110])] += 1
    if rej:
        L += ["", "Rejected by the risk controller (no one was asked):"]
        for (k, why), n in rej.most_common(8):
            L.append(f"* {k}: {why} ({n})")

    llm = [d for k, v in by.items() for d in v if d.get("model")]
    lat = [d["model"]["latency_ms"] for d in llm if d["model"].get("latency_ms")]
    L += ["", "## Reliability", "",
          f"* model answers: {len(llm)}; mean latency {sum(lat) / len(lat) / 1000:.1f} s" if lat else "* model answers: 0",
          f"* invalid or ungrounded answers: {sum(not d.get('valid', True) for d in llm)}",
          f"* model cost: ${sum(d['model'].get('cost_usd', 0) or 0 for d in llm):.4f}",
          f"* data failures: {sum(1 for s in safety if s['payload']['kind'] == 'data')}",
          f"* model failures: {sum(1 for s in safety if s['payload']['kind'] == 'model')}"]
    L += ["", "## Safety events", ""]
    L += [f"* {s['recorded_at'][:19]} {s['payload']['kind']}: {s['payload']['detail']}" for s in safety] or ["None."]
    if t0:
        L += ["", f"First decision recorded at {t0} (UTC)."]
    return "\n".join(L) + "\n"
