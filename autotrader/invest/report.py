"""The pilot report, rebuilt offline from the ledger alone (no data directory, no model, no network).

Reported separately: ending wealth; dollar and percentage-point excess over VOO; maximum drawdown; turnover;
trading costs (fees, and the modeled spread and slippage); model costs. Investment performance is shown before and
after model costs. All-in wealth subtracts every model charge grown at the voo portfolio's return from the charge's
cutoff to the end (the money would otherwise have stayed in VOO).

Verdicts, in this order:
1. any fixture decision or synthetic data: NOT A RESULT (dry run); the outcome rule is shown only as a mechanics check;
2. an incomplete record (missing marks, unresolved gaps, missing months, unreconciled charges): INCONCLUSIVE;
3. all-in wealth at or below VOO after 12 complete months: STOP this version;
4. otherwise: eligible for independent review of a longer paper test (not evidence of skill).
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from .ledger import read_records
from .registration import CAPITAL, FROZEN, schedule, sessions_between

TWO, FOUR = Decimal("0.01"), Decimal("0.0001")
D = Decimal


def _max_drawdown(values: list[Decimal]) -> Decimal:
    peak, worst = None, D(0)
    for v in values:
        peak = v if peak is None or v > peak else peak
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


def verdict(mode: str, fixture: int, synthetic: bool, complete: bool, all_in, voo_end) -> str:
    if fixture or synthetic or mode == "dry_run":
        return "NOT A RESULT: dry run with synthetic data and fixture decisions"
    if not complete or all_in is None or voo_end is None:
        return "INCONCLUSIVE: the record is incomplete"
    if all_in <= voo_end:
        return "STOP this version: all-in wealth at or below VOO after 12 complete months"
    return "ELIGIBLE FOR INDEPENDENT REVIEW of a longer paper test (not evidence of skill)"


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
        marks = {r["payload"]["session"]: D(r["payload"]["wealth"]) for r in recs
                 if r["type"] == "mark" and r["payload"]["book"] == name}
        opens = [r["payload"] for r in recs if r["type"] == "session_open" and r["payload"]["book"] == name]
        trades = [o["trade"] for o in opens if o["trade"]]
        series = [(d, marks[d.isoformat()]) for d in days if d.isoformat() in marks]
        month_end = [marks[s.period_end.day.isoformat()] for s in slots if s.period_end.day.isoformat() in marks]
        purchases = sum((D(t["purchases"]) for t in trades), D(0))
        sales = sum((D(t["sales"]) for t in trades), D(0))
        avg = sum(month_end, D(0)) / len(month_end) if month_end else None
        books[name] = {
            "marks": len(series), "sessions": len(days), "series": series,
            "ending_wealth": series[-1][1] if series else None, "as_of": series[-1][0].isoformat() if series else None,
            "max_drawdown_pct": (_max_drawdown([v for _, v in series]) * 100).quantize(TWO) if series else None,
            "purchases": purchases, "sales": sales,
            "turnover_pct": (min(purchases, sales) / avg * 100).quantize(TWO) if avg else None,
            "fees": sum((D(t["fees"]["total"]) for t in trades), D(0)),
            "slippage": sum((D(t["slippage_usd"]) for t in trades), D(0)).quantize(FOUR),
            "dividends": sum((D(a["amount"]) for o in opens for a in o["actions"] if a["kind"] == "dividend_paid"), D(0)),
        }
    decisions = [idx.get(f"decision|{s.index}") for s in slots]
    costs = [idx.get(f"cost|{s.index}") for s in slots]
    gaps = [r["payload"] for r in recs if r["type"] == "gap" and f"gap_resolved|{r['key'][4:]}" not in idx]
    fixture = sum(1 for r in decisions if r and not r["payload"]["genuine"])
    genuine = sum(1 for r in decisions if r and r["payload"]["genuine"])
    pack_ok = 0
    for r in decisions:
        p = r["payload"] if r else {}
        f = state_dir / p.get("pack_file", "-")
        if p.get("pack_file") and f.exists() and \
                hashlib.sha256(json.dumps(json.loads(f.read_text()), sort_keys=True, separators=(",", ":"),
                                          default=str).encode()).hexdigest() == p["pack_sha256"]:
            pack_ok += 1
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
    if any(c is None or not c["payload"].get("reconciled") for c in costs):
        missing_reasons.append("model charges missing or not reconciled with the provider's usage report")
    asked = [p for p in (r["payload"] for r in decisions if r) if p.get("pack_file")]
    if pack_ok != len(asked):
        missing_reasons.append(f"{len(asked) - pack_ok} frozen pack file(s) missing or altered")
    complete = not missing_reasons

    voo, ai = books["voo"], books["ai"]
    voo_marks = {d.isoformat(): v for d, v in voo["series"]}
    charges, nominal, grown = [], D(0), D(0)
    for s, c in zip(slots, costs):
        if c is None:
            continue
        usd = D(c["payload"]["usd"])
        start = voo_marks.get(s.cutoff.day.isoformat(), CAPITAL if s.index == 1 else None)
        factor = (voo["ending_wealth"] / start) if (start and voo["ending_wealth"]) else None
        g = usd * factor if factor is not None else None
        nominal += usd
        grown += g if g is not None else usd
        charges.append({"slot": s.index, "month": s.month, "usd": usd, "basis": c["payload"]["basis"],
                        "grown_to_end": g.quantize(FOUR) if g is not None else None})
    out = {"mode": mode, "identity": ident, "complete": complete, "incomplete_because": missing_reasons,
           "decisions_genuine": genuine, "decisions_fixture": fixture, "pack_files_verified": pack_ok}
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
        allin = []                                       # each charge, grown at VOO from its cutoff to that day
        for d, v in ai["series"]:
            charged = D(0)
            for ch in charges:
                cut = slots[ch["slot"] - 1].cutoff.day
                if cut <= d and d.isoformat() in voo_marks:
                    charged += ch["usd"] * voo_marks[d.isoformat()] / voo_marks.get(cut.isoformat(), CAPITAL)
            allin.append(v - charged)
        out["ai_all_in_max_drawdown_pct"] = (_max_drawdown(allin) * 100).quantize(TWO) if allin else None
    rule = None
    if "ai_end_all_in" in out:
        rule = "stop this version" if out["ai_end_all_in"] <= out["voo_end"] else "independent review of a longer paper test"
    v = verdict(mode, fixture, bool(ident.get("synthetic_data")), complete, out.get("ai_end_all_in"), out.get("voo_end"))
    out |= {"verdict": v, "outcome_rule_mechanics": rule,
            "books": {n: {k: v for k, v in b.items() if k != "series"} for n, b in books.items()},
            "model_costs": {"nominal_usd": nominal, "grown_usd": grown.quantize(FOUR), "charges": charges},
            "decisions": [_decision_row(r, s) for r, s in zip(decisions, slots)], "unresolved_gaps": gaps}
    return json.loads(json.dumps(out, default=str))


def _decision_row(r, s) -> dict:
    if r is None:
        return {"slot": s.index, "month": s.month, "status": "not recorded"}
    p = r["payload"]
    acts = [f"{x['action']} {x['symbol']}" + ("" if x["action"] in ("hold", "sell") else f" {x['target_weight_pct']}%")
            for x in ((p.get("decision") or {}).get("positions") or [])]
    return {"slot": s.index, "month": s.month, "status": p["status"], "decider": p["decider"],
            "genuine": p["genuine"], "actions": acts or (["100% VOO"] if p.get("decision") else []),
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
                  f"| model costs (USD, nominal / grown at VOO) | | | {_c(mc['nominal_usd'])} / {_c(mc['grown_usd'])} |", ""]
    lines += [f"Sessions valued: VOO {b['voo']['marks']}/{b['voo']['sessions']}, AI {b['ai']['marks']}/{b['ai']['sessions']}. "
              f"Frozen packs verified: {rep['pack_files_verified']}.", "",
              "| slot | month | decider | status | actions | VOO % | problems |", "|---:|---|---|---|---|---:|---|"]
    for d in rep["decisions"]:
        who = "" if "decider" not in d else (d["decider"] + ("" if d["genuine"] else " (FIXTURE)"))
        lines.append(f"| {d['slot']} | {d['month']} | {who} | {d['status']} | {'; '.join(d.get('actions') or [])} | "
                     f"{d.get('voo_pct') or ''} | {'; '.join(d.get('problems') or [])[:160]} |")
    lines += ["", "Model charges: " + ", ".join(f"{c['month']} {c['usd']} ({c['basis']})" for c in mc["charges"]), ""]
    return "\n".join(lines) + "\n"


def write_report(state_dir: Path) -> dict:
    rep = build_report(state_dir)
    Path(state_dir, "report.json").write_text(json.dumps(rep, sort_keys=True, indent=1) + "\n")
    Path(state_dir, "REPORT.md").write_text(markdown(rep))
    return rep
