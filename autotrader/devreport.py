"""Coverage, reconciliation, the registered gates and the descriptive results of the development evaluation.

Everything here is computed offline from the run's own files (ledger, inputs manifest, executions, journal
summary); `python -m autotrader devreport OUT_DIR` reproduces it without any call. No aggregate contains a price,
a quote or source text.

Kept apart, and labelled:
* the registered reliability gates (PLAN.md), applied exactly;
* the descriptive development economics (PLAN.md: "reported but not gated");
* the research director's proposed advancement screen (dev-eval/ADVANCEMENT-SCREEN.md), which is a proposal for
  the owner to freeze before any paid call, not part of the registration.
"""
from __future__ import annotations

import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Optional

from .experiment import FROZEN, event_id, post_cutoff
from .execution import EXECUTABLE_VERIFIED

CAL_BINS = ((0.0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 1.0000001))
SPARSE = 10
MODEL_FAILURES = {"refusal", "max_tokens", "model_mismatch", "timeout", "transport_error", "api_error",
                  "unknown_after_restart", "missing_or_invalid_usage", "incomplete", "contradictory_usage",
                  "unpriced_or_contradictory_usage"}


# ------------------------------------------------------------------ statistics helpers
def brier(pairs: list[tuple[float, int]]) -> Optional[float]:
    return sum((p - u) ** 2 for p, u in pairs) / len(pairs) if pairs else None


def auc(pairs: list[tuple[float, int]]) -> Optional[float]:
    """Mann-Whitney AUC with ties counted as one half."""
    pos = [p for p, u in pairs if u == 1]
    neg = [p for p, u in pairs if u == 0]
    if not pos or not neg:
        return None
    s = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in pos for b in neg)
    return s / (len(pos) * len(neg))


def day_bootstrap(daily: dict[str, float], reps: int, seed: int, level: float) -> Optional[dict]:
    """Mean over dates and its percentile interval, resampling whole dates."""
    days = sorted(daily)
    if not days:
        return None
    rng = random.Random(seed)
    vals = [daily[d] for d in days]
    n = len(vals)
    means = sorted(sum(vals[rng.randrange(n)] for _ in range(n)) / n for _ in range(reps))
    lo, hi = means[int((1 - level) / 2 * reps)], means[int((1 + level) / 2 * reps) - 1]
    return {"mean": sum(vals) / n, "lo": lo, "hi": hi, "dates": n, "replicates": reps, "seed": seed, "level": level}


def joint_bootstrap(series: dict[str, dict[str, float]], reps: int, seed: int, level: float) -> dict:
    """The same resampled dates for every series (all arms together)."""
    days = sorted(set().union(*[set(s) for s in series.values()])) if series else []
    rng = random.Random(seed)
    n = len(days)
    draws = {k: [] for k in series}
    for _ in range(reps):
        pick = [days[rng.randrange(n)] for _ in range(n)]
        for k, s in series.items():
            draws[k].append(sum(s.get(d, 0.0) for d in pick) / n)
    out = {}
    for k, v in draws.items():
        v.sort()
        out[k] = {"mean": sum(series[k].get(d, 0.0) for d in days) / n if n else None,
                  "lo": v[int((1 - level) / 2 * reps)] if v else None,
                  "hi": v[int((1 + level) / 2 * reps) - 1] if v else None}
    return {"dates": n, "replicates": reps, "seed": seed, "level": level, "series": out}


# ------------------------------------------------------------------ classification
def ai_status(d: Optional[dict]) -> str:
    if d is None:
        return "missing"
    if d.get("skip"):
        return {"not_run": "not_run", "input_incomplete": "input_incomplete",
                "circuit_breaker": "circuit_breaker_skip"}.get(d["skip"], d["skip"])
    j = d.get("journal") or {}
    if d.get("failure"):
        o = j.get("outcome") or (d.get("problems") or ["?"])[0]
        return "provider_error" if o in ("timeout", "transport_error", "api_error") else \
            "infrastructure_failure" if o in ("unknown_after_restart", "missing_or_invalid_usage") else o
    probs = d.get("problems") or []
    if probs and any(p.startswith(("schema", "not JSON")) for p in probs):
        return "invalid_schema"
    if probs or not d.get("valid"):
        return "invalid_grounding"
    return "grounded_valid"


def build_report(out: Path, *, rows: list[dict], manifest: dict, ledger, results: dict, journal: Optional[dict],
                 proof) -> dict:
    ok, problems = ledger.verify()
    decs = {(r["payload"]["event_id"], r["payload"]["decider"]): r["payload"]["decision"] for r in ledger.of_type("decision")}
    market = results["market"]
    arms = FROZEN["arms"]
    dispositions = {v: {(d["event_id"], d["decider"]): d for d in res["dispositions"]}
                    for v, res in results["executions"].items()}
    # ---------------------------------------------------------------- coverage (one row per event)
    cov = []
    for r in rows:
        eid = event_id(r)
        d = decs.get((eid, "llm"))
        row = {"event_id": eid, "day": r["day"], "fold": r["fold"], "post_cutoff": post_cutoff(r["day"]),
               "input_status": manifest[eid]["input_status"], "ai_status": ai_status(d),
               "ai_decision": (d or {}).get("action", "none") if d else "none",
               "ai_probability": "present" if d and d.get("p_up_by_close") is not None else "missing",
               "ai_confidence": ((d or {}).get("thesis") or {}).get("confidence") or "",
               "charge_state": ((d or {}).get("journal") or {}).get("state", "none"),
               "market_outcome": "present" if market.get(eid) else "missing"}
        for arm in arms:
            for v in dispositions:
                x = dispositions[v].get((eid, arm))
                st = "missing" if x is None else x["status"]
                if x is not None and st == "filled":
                    st = "filled_scored" if (x.get("outcome") or {}).get("scored") else "filled_unscorable"
                row[f"{arm}:{v}"] = st
        cov.append(row)
    with open(out / "coverage.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cov[0]))
        w.writeheader()
        w.writerows(cov)
    # ---------------------------------------------------------------- reconciliation
    rec = {"ledger_verified": ok, "ledger_problems": problems[:5], "events": len(rows),
           "decisions_per_arm": {a: sum((event_id(r), a) in decs for r in rows) for a in arms},
           "market_outcomes": sum(1 for r in rows if market.get(event_id(r))),
           "proposed_without_disposition": 0, "filled_without_outcome": 0, "unscorable_trades": 0,
           "unreconciled_trades": 0}
    for v, ds in dispositions.items():
        for (eid, arm), x in ds.items():
            if x["action"] == "long" and x["status"] == "no_order":
                rec["proposed_without_disposition"] += 1
            if x["status"] == "filled":
                o = x.get("outcome")
                if o is None:
                    rec["filled_without_outcome"] += 1
                elif not o.get("scored"):
                    rec["unscorable_trades"] += 1
                elif not o.get("reconciles"):
                    rec["unreconciled_trades"] += 1
        for r in rows:
            for a in arms:
                if (event_id(r), a) in decs and (event_id(r), a) not in ds:
                    rec["proposed_without_disposition"] += 1
    if journal is not None:
        rec["journal"] = {k: journal[k] for k in ("attempts", "states", "settled_micro", "held_micro", "committed_micro",
                                                    "ceiling_micro")}
        rec["journal_within_ceiling"] = journal["committed_micro"] <= journal["ceiling_micro"]
    rec["complete"] = (ok and all(n == len(rows) for n in rec["decisions_per_arm"].values())
                       and rec["market_outcomes"] == len(rows) and rec["proposed_without_disposition"] == 0
                       and rec["filled_without_outcome"] == 0 and rec["unreconciled_trades"] == 0
                       and rec.get("journal_within_ceiling", True))
    # ---------------------------------------------------------------- registered gates
    st = Counter(c["ai_status"] for c in cov)
    n_valid = st["grounded_valid"]
    n_fail = sum(v for k, v in st.items() if k in MODEL_FAILURES or k in ("provider_error", "infrastructure_failure"))
    eps = eps_gate(decs, rows, results.get("eps_labels"))
    spend = (journal or {}).get("committed_micro")
    model_ran = any(c["ai_status"] not in ("not_run",) for c in cov)
    gates = {
        "valid_grounded_answers": {"value": n_valid, "pass_if": ">= 152", "pass": n_valid >= 152},
        "model_failures": {"value": n_fail, "pass_if": "<= 3", "pass": model_ran and n_fail <= 3},
        "eps_extraction": eps,
        "end_to_end": {"pass": rec["market_outcomes"] == len(rows) and rec["proposed_without_disposition"] == 0
                       and rec["filled_without_outcome"] == 0, "unscorable_trades": rec["unscorable_trades"]},
        "spend": {"committed_micro": spend, "pass_if": "<= 12,500,000", "pass": spend is not None and spend <= 12_500_000},
        "unanswered": {k: st[k] for k in ("not_run", "input_incomplete", "circuit_breaker_skip", "missing")},
        "ai_status_counts": dict(st),
    }
    gates["all_pass"] = all(g.get("pass") for k, g in gates.items() if isinstance(g, dict) and "pass" in g)
    # ---------------------------------------------------------------- prediction
    pred = prediction(decs, rows, market)
    # ---------------------------------------------------------------- economics
    econ = {v: economics(res, rows, decs, journal, market) for v, res in results["executions"].items()}
    screen = advancement_screen(econ, pred, gates, rec)
    status = "complete" if rec["complete"] else "INCOMPLETE"
    report = {"status": status, "dry_run": not model_ran, "synthetic_data": bool(results.get("synthetic_data")),
              "executable_quotes_verified": EXECUTABLE_VERIFIED,
              "budget_proof": {"proven": proof.proven, "reasons": proof.reasons, "totals": proof.totals},
              "reconciliation": rec, "gates": gates, "prediction": pred, "economics": econ,
              "advancement_screen_proposed": screen,
              "input_status": dict(Counter(m["input_status"] for m in manifest.values()))}
    (out / "results.json").write_text(json.dumps(report, indent=1, default=str))
    (out / "REPORT.md").write_text(markdown(report))
    return report


def eps_gate(decs: dict, rows: list[dict], eps_labels: Optional[dict]) -> dict:
    if not eps_labels:
        return {"pass": False, "note": "no EPS labels were computed in this run", "checked": 0, "correct": 0}
    from .labels import eps_correct
    checkable = [event_id(r) for r in rows if (eps_labels.get(event_id(r)) or {}).get("checkable")]
    checked = correct = omitted = wrong = 0
    for eid in checkable:
        d = decs.get((eid, "llm")) or {}
        rep = ((d.get("thesis") or {}).get("reported") or {}).get("eps_diluted") if d.get("valid") else None
        c = eps_correct(rep, eps_labels[eid])
        if c is None:
            omitted += 1
            continue
        checked += 1
        correct += int(c)
        wrong += int(not c)
    acc = correct / checked if checked else None
    return {"checkable_events": len(checkable), "checked": checked, "correct": correct, "wrong": wrong,
            "omitted": omitted, "accuracy": acc, "coverage": checked / len(checkable) if checkable else None,
            "tolerance": "|reported - label| <= 0.01 in decimal arithmetic",
            "pass_if": ">= 90% correct on >= 30 checked", "pass": bool(acc is not None and checked >= 30 and acc >= 0.9)}


def prediction(decs: dict, rows: list[dict], market: dict) -> dict:
    base = FROZEN["base_rate"]
    sets = {"valid_only": [], "all_issued": [], "missing_as_baseline": []}
    post = {"valid_only": []}
    invalid_parseable = missing = 0
    conf = defaultdict(list)
    daily_diff = defaultdict(float)
    for r in rows:
        eid = event_id(r)
        m = market.get(eid)
        d = decs.get((eid, "llm")) or {}
        p = d.get("p_up_by_close")
        if m is None:
            continue
        u = m["up"]
        if p is not None and d.get("valid"):
            sets["valid_only"].append((p, u))
            if post_cutoff(r["day"]):
                post["valid_only"].append((p, u))
            c = (d.get("thesis") or {}).get("confidence")
            if c:
                conf[c].append((p, u))
        if p is not None:
            sets["all_issued"].append((p, u))
            if not d.get("valid"):
                invalid_parseable += 1
        else:
            missing += 1
        q = p if p is not None else base
        sets["missing_as_baseline"].append((q, u))
        daily_diff[r["day"]] += ((q - u) ** 2 - (base - u) ** 2) / 4
    out = {"base_rate_pinned": base, "base_rate_historical": FROZEN["base_rate_historical"],
           "invalid_but_parseable": invalid_parseable, "missing_forecasts": missing}
    for k, pairs in sets.items():
        out[k] = {"n": len(pairs), "brier_model": brier(pairs), "brier_base": brier([(base, u) for _, u in pairs]),
                  "auc": auc(pairs)}
    vp = sets["valid_only"]
    out["calibration_valid_only"] = [
        {"bin": f"[{lo:.1f}, {min(hi, 1.0):.1f}{']' if hi > 1 else ')'}", "n": len(b),
         "mean_p": mean(p for p, _ in b) if b else None, "up_rate": mean(u for _, u in b) if b else None,
         "sparse": len(b) < SPARSE}
        for lo, hi in CAL_BINS for b in [[x for x in vp if lo <= x[0] < hi]]]
    out["confidence"] = {k: {"n": len(v), "mean_p": mean(p for p, _ in v), "up_rate": mean(u for _, u in v)}
                         for k, v in conf.items()}
    out["post_cutoff_descriptive"] = {"n": len(post["valid_only"]), "brier_model": brier(post["valid_only"]),
                                      "note": "events after the model's June 2026 training cutoff; descriptive only"}
    b = FROZEN["bootstrap"]
    out["paired_brier_difference_by_day"] = day_bootstrap(dict(daily_diff), b["replicates"], b["seed"], b["level"]) \
        if any(d.get("p_up_by_close") is not None for d in decs.values()) else None
    return out


def economics(res: dict, rows: list[dict], decs: dict, journal: Optional[dict], market: dict) -> dict:
    days = sorted({r["day"] for r in rows})
    inference_by_day = defaultdict(float)
    for r in rows:
        j = (decs.get((event_id(r), "llm")) or {}).get("journal") or {}
        if j.get("charge_micro") is not None:
            inference_by_day[r["day"]] += j["charge_micro"] / 1e6
    out = {}
    for arm in FROZEN["arms"]:
        ds = [d for d in res["dispositions"] if d["decider"] == arm]
        trades = [d for d in ds if d["status"] == "filled" and (d.get("outcome") or {}).get("scored")]
        daily = {d: 0.0 for d in days}
        for t in trades:
            daily[next(r["day"] for r in rows if event_id(r) == t["event_id"])] += t["outcome"]["pnl_usd"]
        pnl = [t["outcome"]["pnl_usd"] for t in trades]
        eq, peak, dd = 0.0, 0.0, 0.0
        for d in days:
            eq += daily[d]
            peak, dd = max(peak, eq), max(dd, peak - eq)
        best_day = max(days, key=lambda d: daily[d]) if days else None
        gains = sorted((p for p in pnl if p > 0), reverse=True)
        o = {"trades": len(trades), "proposed": sum(d["action"] == "long" for d in ds),
             "status": dict(Counter(d["status"] for d in ds)),
             "total_pnl_usd": sum(pnl), "net_per_event_usd": sum(pnl) / len(rows) if rows else None,
             "net_per_trade_usd": sum(pnl) / len(pnl) if pnl else None,
             "gross_usd": sum(t["outcome"]["gross_usd"] for t in trades),
             "spread_usd": sum(t["outcome"]["spread_usd"] for t in trades),
             "slippage_usd": sum(t["outcome"]["slippage_usd"] for t in trades),
             "fees_usd": sum(t["outcome"]["fees_usd"] for t in trades),
             "notional_usd": sum(t["outcome"]["notional"] for t in trades),
             "max_drawdown_usd": dd, "best_day": {"day": best_day, "pnl_usd": daily.get(best_day)} if best_day else None,
             "pnl_without_best_day_usd": sum(pnl) - (daily[best_day] if best_day else 0.0),
             "best_trade_usd": max(pnl) if pnl else None,
             "top5_share_of_gains": sum(gains[:5]) / sum(gains) if gains else None,
             "daily_pnl_usd": daily}
        if arm == "llm":
            o["inference_usd"] = sum(inference_by_day.values())
            o["owner_pnl_after_inference_usd"] = sum(pnl) - o["inference_usd"]
            o["daily_all_in_usd"] = {d: daily[d] - inference_by_day.get(d, 0.0) for d in days}
            exp = [((decs.get((t["event_id"], "llm")) or {}).get("expected_net_return_pct"), t["outcome"]["net_pct"])
                   for t in trades]
            exp = [(a, b) for a, b in exp if a is not None]
            o["expected_vs_realized_pct"] = {"n": len(exp), "mean_expected": mean(a for a, _ in exp) if exp else None,
                                             "mean_realized": mean(b for _, b in exp) if exp else None}
            sel, dec = [], []
            for r in rows:
                d = decs.get((event_id(r), "llm")) or {}
                m = market.get(event_id(r))
                if m is None or not d.get("valid"):
                    continue
                mv = (m["exit_mid"] / m["entry_mid"] - 1) * 100
                (sel if d.get("action") == "long" else dec).append(mv)
            o["selectivity_stock_move_pct"] = {"selected_n": len(sel), "selected_mean": mean(sel) if sel else None,
                                               "declined_n": len(dec), "declined_mean": mean(dec) if dec else None,
                                               "note": "descriptive; not evidence of incremental value"}
        out[arm] = o
    return out


def advancement_screen(econ: dict, pred: dict, gates: dict, rec: dict) -> dict:
    """The director's PROPOSED screen (not frozen, not registered). Evaluated only for a complete paid run."""
    ex, x2 = econ.get("executable", {}), econ.get("executable_2x_costs", {})
    ai = ex.get("llm") or {}
    if "daily_all_in_usd" not in ai or gates.get("ai_status_counts", {}).get("not_run"):
        return {"status": "not evaluated: no model decisions in this run"}
    b = FROZEN["bootstrap"]
    series = {"ai_all_in": ai["daily_all_in_usd"]}
    for c in ("price_only", "rule_p4h1", "no_trade"):
        series[f"excess_vs_{c}"] = {d: ai["daily_all_in_usd"][d] - ex[c]["daily_pnl_usd"][d] for d in ai["daily_all_in_usd"]}
    boot = joint_bootstrap(series, b["replicates"], b["seed"], b["level"])
    ai2 = x2.get("llm") or {}
    checks = {
        "complete_and_gates": bool(rec["complete"] and gates["all_pass"]),
        "brier_below_base": bool((pred["valid_only"]["brier_model"] or 1) < (pred["valid_only"]["brier_base"] or 0)),
        "positive_account_pnl_after_costs": ai.get("owner_pnl_after_inference_usd", 0) > 0,
        "positive_same_day_excess_each_active_control": all(
            boot["series"][f"excess_vs_{c}"]["mean"] > 0 for c in ("price_only", "rule_p4h1")),
        "positive_2x_costs_incl_inference": (ai2.get("total_pnl_usd", 0) - ai.get("inference_usd", 0)) > 0,
        "positive_without_best_day": ai.get("pnl_without_best_day_usd", 0) - ai.get("inference_usd", 0) > 0,
        "lower_bound_ai_all_in_above_zero": (boot["series"]["ai_all_in"]["lo"] or 0) > 0,
        "lower_bound_excess_vs_price_only_above_zero": (boot["series"]["excess_vs_price_only"]["lo"] or 0) > 0,
    }
    return {"status": "PROPOSED (not frozen)", "checks": checks, "advance": all(checks.values()), "bootstrap": boot,
            "note": "material unsupported claims behind approved trades and risk-limit breaches also block "
                    "advancement; they need the blinded claim review (autotrader/review.py)"}


def _f(x, nd=4):
    return "n/a" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def markdown(rep: dict) -> str:
    rec, g, p = rep["reconciliation"], rep["gates"], rep["prediction"]
    L = [f"# Phase 5 development evaluation: {'DRY RUN' if rep['dry_run'] else 'RUN'} report", ""]
    if rep.get("synthetic_data"):
        L += ["**Synthetic data.** Every price, quote, filing and company fact in this run was invented by "
              "`autotrader/synthetic.py`; only the 160 event identities are real. The numbers below test the "
              "pipeline and mean nothing about any strategy or model.", ""]
    L += [
         f"Status: **{rep['status']}**. Executable quotes verified: **{rep['executable_quotes_verified']}** "
         "(the cache holds following-minute medians only, so every fill is a labelled proxy).",
         f"Budget proof: **{'holds' if rep['budget_proof']['proven'] else 'does not hold (paid path BLOCKED)'}**.", ""]
    L += [f"* {r}" for r in rep["budget_proof"]["reasons"]]
    L += ["", "## Reconciliation", "", "| check | value |", "|---|---|"]
    for k in ("ledger_verified", "events", "market_outcomes", "proposed_without_disposition", "filled_without_outcome",
              "unscorable_trades", "unreconciled_trades", "complete"):
        L.append(f"| {k} | {rec[k]} |")
    L.append(f"| decisions per arm | {', '.join(f'{a} {n}' for a, n in rec['decisions_per_arm'].items())} |")
    L += ["", "## Registered gates (PLAN.md)", "", "| gate | value | pass |", "|---|---|---|",
          f"| valid grounded answers ≥ 152 | {g['valid_grounded_answers']['value']} | {g['valid_grounded_answers']['pass']} |",
          f"| model failures ≤ 3 | {g['model_failures']['value']} | {g['model_failures']['pass']} |",
          f"| GAAP diluted EPS ≥ 90% on ≥ 30 checked | {_f(g['eps_extraction'].get('accuracy'))} on "
          f"{g['eps_extraction'].get('checked')} | {g['eps_extraction']['pass']} |",
          f"| every event scored, every proposal disposed | | {g['end_to_end']['pass']} |",
          f"| spend ≤ $12.50 (committed) | {g['spend']['committed_micro']} µ$ | {g['spend']['pass']} |",
          "", f"Model-arm status counts: {g['ai_status_counts']}", ""]
    L += ["## Prediction (descriptive)", "",
          f"Pinned base rate {p['base_rate_pinned']} (historical {p['base_rate_historical']}); "
          f"invalid-but-parseable {p['invalid_but_parseable']}, missing {p['missing_forecasts']}.", "",
          "| set | n | Brier model | Brier base | AUC |", "|---|---:|---:|---:|---:|"]
    for k in ("valid_only", "all_issued", "missing_as_baseline"):
        s = p[k]
        L.append(f"| {k} | {s['n']} | {_f(s['brier_model'])} | {_f(s['brier_base'])} | {_f(s['auc'], 3)} |")
    L += ["", "## Economics (descriptive, proxy fills)", ""]
    for v, arms in rep["economics"].items():
        L += [f"### {v}", "", "| arm | proposed | trades | total P&L | per event | per trade | fees | max drawdown |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for a, o in arms.items():
            L.append(f"| {a} | {o['proposed']} | {o['trades']} | {_f(o['total_pnl_usd'])} | {_f(o['net_per_event_usd'])} "
                     f"| {_f(o['net_per_trade_usd'])} | {_f(o['fees_usd'])} | {_f(o['max_drawdown_usd'])} |")
        L.append("")
    L += ["## Proposed advancement screen (not frozen)", "", f"{rep['advancement_screen_proposed'].get('status')}", ""]
    return "\n".join(L) + "\n"


def rebuild(out_dir: Path) -> dict:
    """Recompute the whole report from the run's files, offline and without any call."""
    from .budgetproof_view import ProofView
    from .experiment import frozen_order, load_sample
    from .ledger import Ledger
    out = Path(out_dir)
    man = json.loads((out / "inputs_manifest.json").read_text())
    sample = load_sample()
    rows = frozen_order([e | {"flash": None} for e in sample["events"]])
    order = {eid: i for i, eid in enumerate(man["events"])}
    rows.sort(key=lambda r: order[event_id(r)])
    led = Ledger(out / "ledger.jsonl", "replay")
    results = json.loads((out / "executions.json").read_text())
    journal = json.loads((out / "journal_summary.json").read_text())
    proof = ProofView(json.loads((out / "budget_proof.json").read_text()))
    return build_report(out, rows=rows, manifest=man["events"], ledger=led, results=results, journal=journal, proof=proof)
