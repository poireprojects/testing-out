"""The pilot report, rebuilt offline from the ledger and its frozen packs (no data directory, model or network).

Reported separately: ending wealth; dollar and percentage-point excess over VOO; maximum drawdown; turnover;
trading costs (fees, and the modeled spread and slippage); model costs. Investment performance is shown before and
after model costs. All-in wealth subtracts every model charge grown at the voo portfolio's return from the charge's
cutoff to the end (the money would otherwise have stayed in VOO).

Statuses, the same everywhere: decisions are `valid`, `invalid_answer`, `missed_answer` (no answer, late, lost in
flight, not requested) or `missing_input` (research inputs incomplete, no request); charges are `reconciled`,
`not_requested` or `unreconciled` (unknown billing: never counted as zero; resolved only by a later `reconcile`
record).

The record is complete only when every session is valued, no data gap is open, all 12 months are recorded, no month
is missing_input, every frozen pack verifies and covers every candidate and held stock with a reproducible status,
every charge is known, and every claim of a valid genuine decision has a blinded review.

Verdicts, in this order:
1. any fixture decision or synthetic data: NOT A RESULT (dry run); the outcome rule is shown only as a mechanics check;
2. an incomplete record: INCONCLUSIVE;
3. all-in wealth at or below VOO after 12 complete months: STOP this version;
4. no valid genuine decision: INCONCLUSIVE (a favorable result needs at least one genuine decision behind it);
5. otherwise: eligible for independent review of a longer paper test (not evidence of skill).
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from .claims import claims
from .diagnostics import score
from .ledger import read_records
from .registration import BENCHMARK, CAPITAL, FROZEN, schedule, sessions_between

TWO, FOUR = Decimal("0.01"), Decimal("0.0001")
D = Decimal
CAP = D(FROZEN["limits"]["stock_max_pct"])
TOL = D(FROZEN["execution"]["cap_tolerance_pp"])
DECISION_STATUSES = ("valid", "invalid_answer", "missed_answer", "missing_input")


def _max_drawdown(values: list[Decimal]) -> Decimal:
    peak, worst = None, D(0)
    for v in values:
        peak = v if peak is None or v > peak else peak
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


def verdict(mode: str, fixture: int, synthetic: bool, complete: bool, all_in, voo_end, genuine_valid: int = 0) -> str:
    if fixture or synthetic or mode == "dry_run":
        return "NOT A RESULT: dry run with synthetic data and fixture decisions"
    if not complete or all_in is None or voo_end is None:
        return "INCONCLUSIVE: the record is incomplete"
    if all_in <= voo_end:
        return "STOP this version: all-in wealth at or below VOO after 12 complete months"
    if genuine_valid < 1:
        return "INCONCLUSIVE: no valid genuine decision stands behind the result"
    return "ELIGIBLE FOR INDEPENDENT REVIEW of a longer paper test (not evidence of skill)"


def _pack_check(state_dir: Path, p: dict) -> list[str]:
    """Problems with a decision's frozen pack: missing or altered file, or coverage that is not complete and
    reproducible for every candidate and held stock."""
    f = state_dir / p["pack_file"]
    if not f.exists():
        return [f"slot {p['slot']}: frozen pack file missing"]
    rec = json.loads(f.read_text())
    if hashlib.sha256(json.dumps(rec, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest() \
            != p["pack_sha256"]:
        return [f"slot {p['slot']}: frozen pack file altered"]
    cov = {c["symbol"]: c for c in rec.get("coverage") or []}
    need = {c["symbol"] for c in rec["candidates"]} | {h["symbol"] for h in rec["holdings"] if h["symbol"] != BENCHMARK}
    out = [f"slot {p['slot']}: no coverage record for {s}" for s in sorted(need - set(cov))]
    for s in sorted(need & set(cov)):
        c = cov[s]
        if c["status"] == "collection_failure":
            out.append(f"slot {p['slot']}: {s} collection failure ({c['reason']})")
        elif c["status"] == "unavailable" and not c.get("reason"):
            out.append(f"slot {p['slot']}: {s} excluded without a reproducible reason")
        elif c["status"] not in ("complete", "unavailable"):
            out.append(f"slot {p['slot']}: {s} has unknown coverage status {c['status']!r}")
    return out


def build_report(state_dir: Path) -> dict:
    state_dir = Path(state_dir)
    recs = read_records(state_dir / "ledger.jsonl")
    idx = {r["key"]: r for r in recs}
    ident = idx["open"]["payload"]["identity"]
    mode = idx["open"]["payload"]["mode"]
    slots = schedule(ident["first_fill_month"])
    days = [s.day for s in sessions_between(slots[0].fill.day, slots[-1].period_end.day)]
    books = {}
    for name in FROZEN["books"]:
        mk = {r["payload"]["session"]: r["payload"] for r in recs if r["type"] == "mark" and r["payload"]["book"] == name}
        marks = {d: D(p["wealth"]) for d, p in mk.items()}
        opens = [r["payload"] for r in recs if r["type"] == "session_open" and r["payload"]["book"] == name]
        trades = [o["trade"] for o in opens if o["trade"]]
        series = [(d, marks[d.isoformat()]) for d in days if d.isoformat() in marks]
        month_end = [marks[s.period_end.day.isoformat()] for s in slots if s.period_end.day.isoformat() in marks]
        purchases = sum((D(t["purchases"]) for t in trades), D(0))
        sales = sum((D(t["sales"]) for t in trades), D(0))
        avg = sum(month_end, D(0)) / len(month_end) if month_end else None
        drift, worst = 0, D(0)                          # stock weights at the closes, after targets were set
        for p in mk.values():
            inv = D(p["wealth"]) - D(p["receivable"])
            ws = [D(v["value"]) / inv * 100 for s, v in p["positions"].items() if s != BENCHMARK] if inv > 0 else []
            worst = max([worst] + ws)
            drift += any(w > CAP + TOL for w in ws)
        plans = [e for o in opens for e in (o.get("plan") or [])]
        books[name] = {
            "marks": len(series), "sessions": len(days), "series": series,
            "ending_wealth": series[-1][1] if series else None, "as_of": series[-1][0].isoformat() if series else None,
            "max_drawdown_pct": (_max_drawdown([v for _, v in series]) * 100).quantize(TWO) if series else None,
            "purchases": purchases, "sales": sales,
            "turnover_pct": (min(purchases, sales) / avg * 100).quantize(TWO) if avg else None,
            "fees": sum((D(t["fees"]["total"]) for t in trades), D(0)),
            "slippage": sum((D(t["slippage_usd"]) for t in trades), D(0)).quantize(FOUR),
            "dividends": sum((D(a["amount"]) for o in opens for a in o["actions"] if a["kind"] == "dividend_paid"), D(0)),
            "max_stock_weight_at_close_pct": worst.quantize(TWO),
            "sessions_with_drift_above_cap": drift,
            "risk_required_reductions": sum(1 for e in plans if e.get("risk_required") and e.get("order")),
            "skipped_discretionary_trades": sum(1 for e in plans if e["note"].startswith("skipped")),
            "residuals_above_cap": [e for e in plans if e.get("within_cap_tolerance") is False],
        }
    decisions = [idx.get(f"decision|{s.index}") for s in slots]
    open_gaps = [r["payload"] for r in recs if r["type"] == "gap" and f"gap_resolved|{r['key'][4:]}" not in idx]
    gaps = [g for g in open_gaps if g["book"] in FROZEN["books"]]          # the shadow book is diagnostic only
    fixture = sum(1 for r in decisions if r and not r["payload"]["genuine"])
    genuine = sum(1 for r in decisions if r and r["payload"]["genuine"])
    genuine_valid = sum(1 for r in decisions if r and r["payload"]["genuine"] and r["payload"]["status"] == "valid")
    counts = {k: sum(1 for r in decisions if r and r["payload"]["status"] == k) for k in DECISION_STATUSES}
    pack_problems = [x for r in decisions if r and r["payload"].get("pack_file") for x in _pack_check(state_dir, r["payload"])]
    asked = [r for r in decisions if r and r["payload"].get("pack_file")]

    charges, cost_state = [], {"reconciled": 0, "not_requested": 0, "unreconciled": 0, "missing": 0}
    for s in slots:
        c, rc = idx.get(f"cost|{s.index}"), idx.get(f"reconcile|{s.index}")
        if c is None:
            cost_state["missing"] += 1
            charges.append({"slot": s.index, "month": s.month, "status": "missing", "usd": None, "basis": ""})
            continue
        p = c["payload"]
        if p["status"] == "unreconciled" and rc is not None:
            charges.append({"slot": s.index, "month": s.month, "status": "reconciled", "usd": D(rc["payload"]["usd"]),
                            "basis": "reconciled later: " + rc["payload"]["evidence"]})
            cost_state["reconciled"] += 1
        else:
            charges.append({"slot": s.index, "month": s.month, "status": p["status"],
                            "usd": None if p["usd"] is None else D(p["usd"]), "basis": p["basis"]})
            cost_state[p["status"]] += 1

    genuine_claims = claims(recs, genuine_only=True)
    reviews = {r["payload"]["review_id"]: r["payload"] for r in recs if r["type"] == "claim_review"}
    pending = [c["review_id"] for c in genuine_claims if c["review_id"] not in reviews]
    unsupported = [dict(c, review=reviews[c["review_id"]]) for c in genuine_claims
                   if reviews.get(c["review_id"], {}).get("verdict") == "unsupported"]

    missing_reasons = []
    for name, b in books.items():
        if b["marks"] != b["sessions"]:
            missing_reasons.append(f"{name}: {b['sessions'] - b['marks']} of {b['sessions']} sessions not valued")
    if gaps:
        missing_reasons.append(f"{len(gaps)} unresolved data gap(s): "
                               + "; ".join(f"{g['book']} {g['session']} {g['phase']} ({', '.join(g['missing'])})"
                                           for g in gaps[:4]))
    if any(r is None for r in decisions):
        missing_reasons.append(f"{sum(r is None for r in decisions)} month(s) without a decision record")
    if counts["missing_input"]:
        missing_reasons.append(f"{counts['missing_input']} month(s) missing_input: research inputs were incomplete, "
                               "so no decision was requested")
    if pack_problems:
        missing_reasons.append(f"{len(pack_problems)} frozen-pack problem(s): " + "; ".join(pack_problems[:4]))
    if cost_state["unreconciled"] or cost_state["missing"]:
        missing_reasons.append(f"{cost_state['unreconciled']} unreconciled and {cost_state['missing']} missing model "
                               "charge(s): billing unknown, not assumed zero")
    if pending:
        missing_reasons.append(f"{len(pending)} claim(s) of genuine decisions await blinded review")
    complete = not missing_reasons

    voo, ai = books["voo"], books["ai"]
    voo_marks = {d.isoformat(): v for d, v in voo["series"]}
    nominal, grown = D(0), D(0)
    for s, ch in zip(slots, charges):
        if ch["usd"] is None:
            ch["grown_to_end"] = None
            continue
        start = voo_marks.get(s.cutoff.day.isoformat(), CAPITAL if s.index == 1 else None)
        g = ch["usd"] * voo["ending_wealth"] / start if (start and voo["ending_wealth"]) else ch["usd"]
        nominal += ch["usd"]
        grown += g
        ch["grown_to_end"] = g.quantize(FOUR)
    out = {"mode": mode, "identity": ident, "complete": complete, "incomplete_because": missing_reasons,
           "decisions_genuine": genuine, "decisions_fixture": fixture, "decisions_genuine_valid": genuine_valid,
           "decision_status_counts": counts, "cost_status_counts": cost_state,
           "packs_frozen": len(asked), "pack_problems": pack_problems,
           "claims": {"genuine_needing_review": len(genuine_claims), "reviewed": len(genuine_claims) - len(pending),
                      "pending": len(pending), "unsupported": unsupported,
                      "fixture_claims_not_reviewed": len(claims(recs, genuine_only=False)) - len(genuine_claims)}}
    if voo["ending_wealth"] is not None and ai["ending_wealth"] is not None:
        before = ai["ending_wealth"]
        after = before - grown.quantize(FOUR)
        out |= {"voo_end": voo["ending_wealth"], "ai_end_before_model": before, "ai_end_all_in": after,
                "excess_usd_before": (before - voo["ending_wealth"]).quantize(FOUR),
                "excess_usd_all_in": (after - voo["ending_wealth"]).quantize(FOUR),
                "excess_pp_before": ((before - voo["ending_wealth"]) / CAPITAL * 100).quantize(FOUR),
                "excess_pp_all_in": ((after - voo["ending_wealth"]) / CAPITAL * 100).quantize(FOUR),
                "return_pct": {"voo": ((voo["ending_wealth"] / CAPITAL - 1) * 100).quantize(FOUR),
                               "ai_before": ((before / CAPITAL - 1) * 100).quantize(FOUR),
                               "ai_all_in": ((after / CAPITAL - 1) * 100).quantize(FOUR)}}
        allin = []                                       # each known charge, grown at VOO from its cutoff to that day
        for d, v in ai["series"]:
            charged = D(0)
            for ch in charges:
                cut = slots[ch["slot"] - 1].cutoff.day
                if ch["usd"] is not None and cut <= d and d.isoformat() in voo_marks:
                    charged += ch["usd"] * voo_marks[d.isoformat()] / voo_marks.get(cut.isoformat(), CAPITAL)
            allin.append(v - charged)
        out["ai_all_in_max_drawdown_pct"] = (_max_drawdown(allin) * 100).quantize(TWO) if allin else None
    # secondary diagnostics: never consulted by the verdict below
    sh = {r["payload"]["session"]: D(r["payload"]["wealth"]) for r in recs
          if r["type"] == "mark" and r["payload"]["book"] == "shadow"}
    sh_opens = [r["payload"] for r in recs if r["type"] == "session_open" and r["payload"]["book"] == "shadow"]
    sh_trades = [o["trade"] for o in sh_opens if o["trade"]]
    sh_series = [sh[d.isoformat()] for d in days if d.isoformat() in sh]
    last_marks = [r["payload"] for r in recs if r["type"] == "mark" and r["payload"]["book"] == "shadow"]
    shadow = {"label": "diagnostic only: 70% VOO + 2.5% in each of the 12 frozen candidates, rebalanced monthly",
              "sessions_valued": len(sh_series), "sessions": len(days),
              "complete": len(sh_series) == len(days) and not any(g["book"] == "shadow" for g in open_gaps),
              "unresolved_gaps": [g for g in open_gaps if g["book"] == "shadow"],
              "ending_wealth": sh_series[-1] if sh_series else None,
              "max_drawdown_pct": (_max_drawdown(sh_series) * 100).quantize(TWO) if sh_series else None,
              "fees": sum((D(t["fees"]["total"]) for t in sh_trades), D(0)),
              "slippage": sum((D(t["slippage_usd"]) for t in sh_trades), D(0)).quantize(FOUR),
              "stock_holdings_at_end": len([s for s in (last_marks[-1]["positions"] if last_marks else {})
                                            if s != BENCHMARK]),
              "ai_max_stock_holdings": FROZEN["limits"]["max_stocks"],
              "voo_pct_after_each_fill": [o["weights_after"]["voo_pct"] for o in sh_opens
                                          if o.get("decision") and o.get("weights_after")],
              "skipped_rebalancing_trades": sum(1 for o in sh_opens for e in (o.get("plan") or [])
                                                if e["note"].startswith("skipped")),
              "drift_note": "orders under the $1 discretionary minimum are skipped, so at $100 most monthly "
                            "rebalancing trades of 2.5% positions are skipped and the shadow drifts from 70/30"}
    shadow["cost_burden_vs_ai_usd"] = (shadow["fees"] + shadow["slippage"] - books["ai"]["fees"]
                                       - books["ai"]["slippage"]).quantize(FOUR)
    oc = {(r["payload"]["slot"], r["payload"]["symbol"]): r["payload"] for r in recs if r["type"] == "forecast_outcome"}
    og = {(r["payload"]["slot"], r["payload"]["symbol"]): r["payload"]["reason"] for r in recs
          if r["type"] == "outcome_gap"}
    valued = [d for d, _ in books["voo"]["series"]]
    forecasts = score([r["payload"] for r in decisions if r], oc, slots, valued[-1] if valued else None, og)
    out["diagnostics"] = {"secondary_only": True, "shadow": shadow, "forecasts": forecasts,
                          "consistency_notes": {r["payload"]["slot"]: r["payload"].get("consistency_notes")
                                                for r in decisions if r and r["payload"].get("consistency_notes")},
                          "fixture_claim_review": {"claims": len(claims(recs, genuine_only=False)) - len(genuine_claims),
                                                   "reviewed": sum(1 for c in claims(recs, genuine_only=False)
                                                                   if not c["genuine"] and c["review_id"] in reviews),
                                                   "unsupported": [c["review_id"] for c in claims(recs, genuine_only=False)
                                                                   if not c["genuine"] and reviews.get(c["review_id"], {}).get("verdict") == "unsupported"]}}
    rule = None
    if "ai_end_all_in" in out:
        rule = "stop this version" if out["ai_end_all_in"] <= out["voo_end"] else "independent review of a longer paper test"
    v = verdict(mode, fixture, bool(ident.get("synthetic_data")), complete, out.get("ai_end_all_in"),
                out.get("voo_end"), genuine_valid)
    out |= {"verdict": v, "outcome_rule_mechanics": rule,
            "books": {n: {k: v for k, v in b.items() if k != "series"} for n, b in books.items()},
            "model_costs": {"nominal_known_usd": nominal, "grown_known_usd": grown.quantize(FOUR), "charges": charges},
            "decisions": [_decision_row(r, s) for r, s in zip(decisions, slots)], "unresolved_gaps": gaps,
            "limitations": [FROZEN["execution"]["paper_convention"],
                            "citation checks verify quotation mechanics, not financial correctness (see claims)"]}
    return json.loads(json.dumps(out, default=str))


def _decision_row(r, s) -> dict:
    if r is None:
        return {"slot": s.index, "month": s.month, "status": "not recorded"}
    p = r["payload"]
    acts = [f"{x['action']} {x['symbol']}" + ("" if x["action"] in ("hold", "sell") else f" {x['target_weight_pct']}%")
            for x in ((p.get("decision") or {}).get("positions") or [])]
    return {"slot": s.index, "month": s.month, "status": p["status"], "reason": p.get("reason", ""),
            "decider": p["decider"], "genuine": p["genuine"],
            "actions": acts or (["100% VOO"] if p.get("decision") else []),
            "voo_pct": p.get("voo_pct"), "problems": p.get("problems", [])[:3], "frozen_at": p.get("frozen_at")}


def _c(x) -> str:
    return "" if x is None else f"{D(str(x)).quantize(TWO):,}"


def markdown(rep: dict) -> str:
    """Dollars rounded to the cent for reading; report.json keeps the exact values."""
    b, mc = rep["books"], rep["model_costs"]
    lines = [f"# Investing pilot report ({rep['identity']['experiment']}, {rep['mode']})", ""]
    if rep["decisions_fixture"] or rep["mode"] == "dry_run":
        lines += ["> **DRY RUN. FIXTURE DECISIONS, SYNTHETIC DATA.** "
                  f"{rep['decisions_fixture']} of 12 decisions are scripted fixtures and "
                  f"{rep['decisions_genuine']} are genuine model decisions. Prices, companies and filings are invented. "
                  "Nothing here says anything about AI stock selection; it only shows the accounting and the rules.", ""]
    lines += [f"**Verdict: {rep['verdict']}**", ""]
    if rep["incomplete_because"]:
        lines += ["Incomplete because:"] + [f"* {x}" for x in rep["incomplete_because"]] + [""]
    if rep.get("outcome_rule_mechanics"):
        lines += [f"Outcome rule, mechanics check only: *{rep['outcome_rule_mechanics']}*"
                  + (" (meaningless here: fixture decisions)." if rep["decisions_fixture"] else "."), ""]
    sc, cc = rep["decision_status_counts"], rep["cost_status_counts"]
    lines += ["Decisions: " + ", ".join(f"{k} {v}" for k, v in sc.items())
              + f"; valid genuine decisions {rep['decisions_genuine_valid']}.",
              "Model charges: " + ", ".join(f"{k} {v}" for k, v in cc.items()) + ".", ""]
    if "voo_end" in rep:
        lines += ["| | VOO | AI before model costs | AI all-in |", "|---|---:|---:|---:|",
                  f"| ending wealth (USD) | {_c(rep['voo_end'])} | {_c(rep['ai_end_before_model'])} | {_c(rep['ai_end_all_in'])} |",
                  f"| return | {_c(rep['return_pct']['voo'])}% | {_c(rep['return_pct']['ai_before'])}% | {_c(rep['return_pct']['ai_all_in'])}% |",
                  f"| excess over VOO (USD) | | {_c(rep['excess_usd_before'])} | {_c(rep['excess_usd_all_in'])} |",
                  f"| excess over VOO (percentage points) | | {_c(rep['excess_pp_before'])} | {_c(rep['excess_pp_all_in'])} |",
                  f"| maximum drawdown | {b['voo']['max_drawdown_pct']}% | {b['ai']['max_drawdown_pct']}% | "
                  f"{rep.get('ai_all_in_max_drawdown_pct')}% |",
                  f"| turnover (lesser of purchases and sales / average month-end wealth) | {b['voo']['turnover_pct']}% | "
                  f"{b['ai']['turnover_pct']}% | |",
                  f"| fees (USD) | {_c(b['voo']['fees'])} | {_c(b['ai']['fees'])} | |",
                  f"| modeled spread and slippage (USD) | {_c(b['voo']['slippage'])} | {_c(b['ai']['slippage'])} | |",
                  f"| dividends received (USD) | {_c(b['voo']['dividends'])} | {_c(b['ai']['dividends'])} | |",
                  f"| known model charges (USD, nominal / grown at VOO) | | | {_c(mc['nominal_known_usd'])} / "
                  f"{_c(mc['grown_known_usd'])} |", ""]
    a = b["ai"]
    lines += [f"Sessions valued: VOO {b['voo']['marks']}/{b['voo']['sessions']}, AI {a['marks']}/{a['sessions']}. "
              f"Frozen packs: {rep['packs_frozen']}, problems: {len(rep['pack_problems'])}.",
              f"Limits: largest stock weight at a close {a['max_stock_weight_at_close_pct']}% (sessions with drift "
              f"above the cap after targets were set: {a['sessions_with_drift_above_cap']}); risk-required reductions "
              f"{a['risk_required_reductions']}; skipped discretionary trades {a['skipped_discretionary_trades']}; "
              f"residuals above the cap {len(a['residuals_above_cap'])}.",
              f"Claims: {rep['claims']['genuine_needing_review']} genuine claim(s) need blinded review, "
              f"{rep['claims']['pending']} pending, {len(rep['claims']['unsupported'])} unsupported; "
              f"{rep['claims']['fixture_claims_not_reviewed']} fixture claim(s) are not reviewed.", "",
              "| slot | month | decider | status | actions | VOO % | problems |", "|---:|---|---|---|---|---:|---|"]
    for d in rep["decisions"]:
        who = "" if "decider" not in d else (d["decider"] + ("" if d["genuine"] else " (FIXTURE)"))
        st = d["status"] + (f" ({d['reason']})" if d.get("reason") else "")
        lines.append(f"| {d['slot']} | {d['month']} | {who} | {st} | {'; '.join(d.get('actions') or [])} | "
                     f"{d.get('voo_pct') or ''} | {'; '.join(d.get('problems') or [])[:160]} |")
    dg = rep["diagnostics"]
    fs, shd = dg["forecasts"], dg["shadow"]
    lines += ["", "## Secondary diagnostics (cannot change the verdict)", "",
              f"Forecast scores: {fs['scored']} scored, {fs['pending']} pending, {fs['missing']} missing (prices) over "
              f"{fs['decisions_scored']} decision(s)."
              + (f" Brier {fs['brier']} (constant 0.5: 0.25); selected mean excess "
                 f"{fs['selected_vs_rejected']['selected_mean_excess_pp']} pp (n={fs['selected_vs_rejected']['selected_n']}) "
                 f"vs rejected {fs['selected_vs_rejected']['rejected_mean_excess_pp']} pp "
                 f"(n={fs['selected_vs_rejected']['rejected_n']}); mean Spearman "
                 f"{fs['rank_association']['mean_spearman']} over {fs['rank_association']['decisions_with_rho']} "
                 "decision(s)." if fs["scored"] else ""),
              "Horizons overlap and companies recur: these are not independent observations and prove nothing "
              "statistically." + (" Fixture forecasts are placeholders, not assessments." if rep["decisions_fixture"] else ""),
              f"Shadow (70% VOO + 2.5% in each of 12 candidates): ending wealth {_c(shd['ending_wealth'])}, "
              f"{shd['stock_holdings_at_end']} stock holdings vs the AI's at most {shd['ai_max_stock_holdings']}, fees "
              f"{_c(shd['fees'])}, spread and slippage {_c(shd['slippage'])} (cost burden vs the AI "
              f"{_c(shd['cost_burden_vs_ai_usd'])}); {'complete' if shd['complete'] else 'INCOMPLETE'}.",
              "Factual accuracy is the blinded claim review above; forecast accuracy is this section; wealth after all "
              "costs is the primary result. They are separate results.", ""]
    lines += ["", "Model charges by month: " + ", ".join(
        f"{c['month']} {c['usd'] if c['usd'] is not None else 'UNKNOWN'} ({c['status']}: {c['basis'][:40]})"
        for c in mc["charges"]), "",
        "Limitations: " + " ".join(f"{x[0].upper()}{x[1:]}." for x in rep["limitations"]), ""]
    return "\n".join(lines) + "\n"


def write_report(state_dir: Path) -> dict:
    rep = build_report(state_dir)
    Path(state_dir, "report.json").write_text(json.dumps(rep, sort_keys=True, indent=1) + "\n")
    Path(state_dir, "REPORT.md").write_text(markdown(rep))
    return rep
