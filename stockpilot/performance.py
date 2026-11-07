"""Performance metrics computed only from recorded trades and account snapshots.

Nothing here is projected or simulated: if there are no closed trades, the numbers
are empty. Demo-mode numbers live in the demo database and are labeled as such.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .clock import ET
from .config import AppSettings
from .storage.db import Database, from_iso, to_iso
from .strategies import ALL_STRATEGIES


def trade_stats(pnls: Iterable[float]) -> dict:
    p = [float(x) for x in pnls if x is not None]
    n = len(p)
    wins = [x for x in p if x > 0]
    losses = [x for x in p if x <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "count": n,
        "wins": len(wins),
        "losses": len(losses),
        "winRate": len(wins) / n if n else 0.0,
        "avgWin": gross_win / len(wins) if wins else 0.0,
        "avgLoss": -gross_loss / len(losses) if losses else 0.0,
        "profitFactor": (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0),
        "expectancy": sum(p) / n if n else 0.0,
        "total": sum(p),
        "best": max(p) if p else 0.0,
        "worst": min(p) if p else 0.0,
    }


def max_drawdown(values: list[float]) -> tuple[float, float]:
    """(largest peak-to-trough drop in dollars, as % of the peak)."""
    peak = -math.inf
    dd_abs = dd_pct = 0.0
    for v in values:
        if v is None:
            continue
        peak = max(peak, v)
        if peak > 0:
            dd = peak - v
            if dd > dd_abs:
                dd_abs, dd_pct = dd, dd / peak * 100
    return dd_abs, dd_pct


def _finite(x: float, cap: float = 999.0) -> float:
    if x is None or not math.isfinite(x):
        return cap
    return x


def performance_report(db: Database, settings: AppSettings, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    closed = db.query("SELECT * FROM trades WHERE status='closed' ORDER BY exit_time")
    stats = trade_stats(t["realized_pnl"] for t in closed)
    stats["profitFactor"] = _finite(stats["profitFactor"])
    fees_total = sum(t["fees"] or 0 for t in closed)

    days = db.query("SELECT * FROM daily_summary ORDER BY session_date")
    curve = []
    for d in days:
        if d["end_equity"] is not None:
            curve.append({"date": d["session_date"], "equity": d["end_equity"], "pnl": d["realized"] or 0.0,
                          "benchmark": d["benchmark_close"]})
    snaps = db.query("SELECT session_date, MAX(time) t, equity FROM equity_snapshots GROUP BY session_date ORDER BY session_date")
    if not curve and snaps:
        curve = [{"date": s["session_date"], "equity": s["equity"], "pnl": 0.0, "benchmark": None} for s in snaps]
    eq_values = [c["equity"] for c in curve]
    intraday = [r["equity"] for r in db.query("SELECT equity FROM equity_snapshots ORDER BY time")]
    dd_abs, dd_pct = max_drawdown(intraday or eq_values)

    start_equity = None
    if days and days[0]["start_equity"]:
        start_equity = days[0]["start_equity"]
    elif curve:
        start_equity = curve[0]["equity"]
    end_equity = eq_values[-1] if eq_values else None
    total_return_pct = ((end_equity / start_equity - 1) * 100) if start_equity and end_equity else None

    bench = [c["benchmark"] for c in curve if c.get("benchmark")]
    bench_return_pct = ((bench[-1] / bench[0] - 1) * 100) if len(bench) >= 2 else None
    bench_curve = []
    if bench and start_equity:
        b0 = bench[0]
        for c in curve:
            if c.get("benchmark"):
                bench_curve.append({"date": c["date"], "value": start_equity * c["benchmark"] / b0})

    by_strategy = []
    for sid, strat in ALL_STRATEGIES.items():
        rows = [t for t in closed if t["strategy"] == sid]
        s = trade_stats(t["realized_pnl"] for t in rows)
        ev = db.query_one("SELECT * FROM strategy_evidence WHERE strategy=?", (sid,))
        by_strategy.append({"id": sid, "name": strat.name, "count": s["count"], "winRate": s["winRate"],
                            "total": s["total"], "expectancy": s["expectancy"],
                            "evidence": ev["verdict"] if ev else "untested",
                            "evidenceSource": ev["source"] if ev else "",
                            "enabled": bool(settings.strategies.enabled.get(sid, False))})

    by_mode = []
    for dm in ("rules", "ai_confirm", "ai_select"):
        rows = [t for t in closed if t["decision_mode"] == dm]
        s = trade_stats(t["realized_pnl"] for t in rows)
        by_mode.append({"mode": dm, "count": s["count"], "winRate": s["winRate"], "total": s["total"]})

    shadow = {}
    for verdict in ("approved", "skipped", "rules", "ranked"):
        rows = db.query("SELECT return_pct FROM shadow_trades WHERE status='closed' AND verdict=?", (verdict,))
        r = [x["return_pct"] for x in rows if x["return_pct"] is not None]
        shadow[verdict] = {"count": len(r), "avgReturnPct": sum(r) / len(r) if r else 0.0,
                           "winRate": sum(1 for x in r if x > 0) / len(r) if r else 0.0}

    month_start = now.astimezone(ET).replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    ai_month = float(db.scalar("SELECT SUM(cost_usd) FROM ai_decisions WHERE time >= ?", (to_iso(month_start),), 0.0))
    ai_all = float(db.scalar("SELECT SUM(cost_usd) FROM ai_decisions", (), 0.0))
    fees_month = float(db.scalar("SELECT SUM(fees) FROM trades WHERE status='closed' AND exit_time >= ?",
                                 (to_iso(month_start),), 0.0))
    ai_calls_month = int(db.scalar("SELECT COUNT(*) FROM ai_decisions WHERE time >= ?", (to_iso(month_start),), 0))
    days_into_month = max(1, now.astimezone(ET).day)
    projected_ai = ai_month / days_into_month * 30
    costs = {
        "aiMonth": ai_month, "aiAllTime": ai_all, "aiCallsMonth": ai_calls_month,
        "feesMonth": fees_month, "feesAllTime": fees_total, "dataMonthly": settings.data.monthly_data_cost_usd,
        "projectedMonthly": projected_ai + settings.data.monthly_data_cost_usd + fees_month / days_into_month * 30,
    }

    first_day = days[0]["session_date"] if days else None
    months_running = 0.0
    if first_day:
        try:
            months_running = max(0.0, (now.date() - datetime.fromisoformat(first_day).date()).days / 30.4)
        except ValueError:
            months_running = 0.0
    data_cost_so_far = settings.data.monthly_data_cost_usd * months_running
    costs["dataSoFar"] = data_cost_so_far
    # realized P/L already has trading fees taken out; subtract what it cost to run the app too
    costs["netAfterOperating"] = stats["total"] - ai_all - data_cost_so_far
    costs["operatingTotal"] = ai_all + data_cost_so_far

    daily = [{"date": d["session_date"], "pnl": (d["realized"] or 0.0), "trades": d["trades"] or 0} for d in days
             if d["realized"] is not None]

    recent = []
    for t in reversed(closed[-200:]):
        et, xt = from_iso(t["entry_time"]), from_iso(t["exit_time"])
        recent.append({
            "id": t["id"], "symbol": t["symbol"], "strategy": ALL_STRATEGIES[t["strategy"]].name
            if t["strategy"] in ALL_STRATEGIES else t["strategy"], "qty": t["entry_qty"],
            "entryPrice": t["entry_price"] or 0.0, "exitPrice": t["exit_price"] or 0.0, "pnl": t["realized_pnl"] or 0.0,
            "pnlPct": ((t["exit_price"] / t["entry_price"] - 1) * 100) if t["entry_price"] and t["exit_price"] else 0.0,
            "fees": t["fees"] or 0.0, "entryTime": et.astimezone(ET).strftime("%b %d %H:%M") if et else "",
            "exitTime": xt.astimezone(ET).strftime("%H:%M") if xt else "",
            "heldMinutes": round((xt - et).total_seconds() / 60) if et and xt else 0,
            "exitReason": t["exit_reason"] or "", "reasoning": t["reasoning"] or "",
            "decisionMode": t["decision_mode"] or ""})

    return {
        "stats": stats, "feesTotal": fees_total, "maxDrawdown": dd_abs, "maxDrawdownPct": dd_pct,
        "startEquity": start_equity or 0.0, "endEquity": end_equity or 0.0,
        "totalReturnPct": total_return_pct if total_return_pct is not None else 0.0,
        "hasReturn": total_return_pct is not None, "benchmarkReturnPct": bench_return_pct or 0.0,
        "hasBenchmark": bench_return_pct is not None, "curve": curve, "benchmarkCurve": bench_curve,
        "daily": daily, "byStrategy": by_strategy, "byMode": by_mode, "shadow": shadow, "costs": costs,
        "approaches": compare_approaches(shadow, ai_all, settings),
        "calibration": confidence_calibration(db),
        "trades": recent, "tradingDays": len(days),
    }


MIN_COMPARE = 30   # shadow trades per group before any comparison is called meaningful


def compare_approaches(shadow: dict, ai_cost_total: float, settings: AppSettings) -> dict:
    """Does the AI add anything over the rules + ranker? Measured from shadow trades only (every
    candidate is followed with the same stops/targets, whether or not it was traded), so the
    comparison is like-for-like. Returns a plain-English conclusion; never guesses."""
    rows = [
        {"key": "rules", "label": "every rules signal", "what": "what trading every setup would have done"},
        {"key": "ranked", "label": "rules + ranker", "what": "only the setups the ranker picked"},
        {"key": "approved", "label": "AI said buy", "what": "setups the AI approved or picked"},
        {"key": "skipped", "label": "AI said no", "what": "setups the AI turned down"},
    ]
    for r in rows:
        r.update(shadow.get(r["key"], {"count": 0, "avgReturnPct": 0.0, "winRate": 0.0}))
    g = {r["key"]: r for r in rows}
    ai_decisions = g["approved"]["count"] + g["skipped"]["count"]
    cost_per_decision = ai_cost_total / ai_decisions if ai_decisions else 0.0
    notional = settings.risk.max_position_usd
    # what the AI's filtering is worth per approved trade, in dollars, at the usual position size
    edge_pct = g["approved"]["avgReturnPct"] - g["ranked"]["avgReturnPct"]
    if g["ranked"]["count"] < MIN_COMPARE and g["rules"]["count"] >= MIN_COMPARE:
        edge_pct = g["approved"]["avgReturnPct"] - g["rules"]["avgReturnPct"]
    edge_usd = edge_pct / 100 * notional
    enough = g["approved"]["count"] >= MIN_COMPARE and g["skipped"]["count"] >= MIN_COMPARE and \
        max(g["ranked"]["count"], g["rules"]["count"]) >= MIN_COMPARE
    if not enough:
        need = ", ".join(f"{r['label']} {r['count']}/{MIN_COMPARE}" for r in rows if r["count"] < MIN_COMPARE)
        verdict, text = "not_enough_data", (f"not enough followed setups yet to tell whether the AI helps ({need}). "
                                            "until then, treat the AI as unproven.")
    elif g["approved"]["avgReturnPct"] <= g["skipped"]["avgReturnPct"]:
        verdict, text = "ai_hurts", ("the setups the AI turned down did as well as or better than the ones it approved — "
                                     "its filtering isn't helping. consider rules-only mode (saves AI cost too).")
    elif edge_pct <= 0:
        verdict, text = "no_gain", ("the AI's picks didn't beat the ranker's picks — it adds cost and delay "
                                    "without improving results. rules + ranker is the better choice so far.")
    elif cost_per_decision > 0 and edge_usd < cost_per_decision * (ai_decisions / max(1, g["approved"]["count"])):
        verdict, text = "costs_more", (f"the AI's picks did slightly better (+{edge_pct:.2f}% per trade) but that is "
                                       f"worth less than what the AI costs per trade at a ${notional:.0f} position.")
    else:
        verdict, text = "ai_helps", (f"so far the AI's picks beat the ranker by {edge_pct:+.2f}% per trade after costs, "
                                     "and beat the setups it rejected. keep watching — this can change.")
    return {"rows": rows, "verdict": verdict, "text": text, "aiDecisions": ai_decisions,
            "aiCostPerDecision": cost_per_decision, "edgePct": edge_pct, "minSample": MIN_COMPARE}


def confidence_calibration(db: Database) -> dict:
    """Does a higher AI confidence actually mean better outcomes? Joins each AI entry decision to
    the shadow trade that followed the same setup. Confidence is only shown with this check next
    to it — it is never used as evidence of profit by itself."""
    rows = db.query("SELECT a.confidence c, s.return_pct r FROM ai_decisions a JOIN shadow_trades s "
                    "ON s.signal_id = a.signal_id WHERE a.kind='entry' AND a.valid=1 AND a.confidence IS NOT NULL "
                    "AND s.status='closed' AND s.verdict IN ('approved','skipped') AND s.return_pct IS NOT NULL")
    edges = [(0.0, 0.5, "under 50%"), (0.5, 0.65, "50–65%"), (0.65, 0.8, "65–80%"), (0.8, 1.01, "80%+")]
    buckets = []
    for lo, hi, label in edges:
        r = [x["r"] for x in rows if lo <= x["c"] < hi]
        buckets.append({"label": label, "count": len(r), "winRate": sum(1 for v in r if v > 0) / len(r) if r else 0.0,
                        "avgReturnPct": sum(r) / len(r) if r else 0.0})
    used = [b for b in buckets if b["count"] >= 20]
    if len(used) < 2:
        verdict, text = "unknown", (f"{len(rows)} AI-reviewed setups have finished so far. at least 20 in two different "
                                    "confidence ranges are needed before saying whether confidence means anything.")
    else:
        ups = all(b2["winRate"] >= b1["winRate"] - 0.02 for b1, b2 in zip(used, used[1:]))
        spread = used[-1]["winRate"] - used[0]["winRate"]
        if ups and spread >= 0.05:
            verdict, text = "informative", "higher AI confidence has gone with better outcomes so far."
        else:
            verdict, text = "uninformative", ("higher AI confidence has NOT gone with better outcomes — "
                                              "treat its confidence numbers as meaningless.")
    return {"buckets": buckets, "samples": len(rows), "verdict": verdict, "text": text}
