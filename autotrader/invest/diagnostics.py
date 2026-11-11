"""Secondary diagnostics (proposed, frozen before any genuine answer). They never change the primary verdict.

Forecast outcomes. For a valid decision in slot k, every assessed candidate's forecast is scored over a fixed horizon:
from the open of slot k's fill session to the close of the last session of slot k+2's month (three monthly holding
periods). Total return per share bought at that open, no costs:

    TR = (close_end x S + sum over dividends with ex-date in (fill day, end day] of amount x S_at_ex) / open_fill - 1

where S is the product of split ratios with ex-dates in (fill day, end day] and S_at_ex the product up to and
including that dividend's ex-date (dividends are quoted per post-split share on their ex-date). A dividend or split
with its ex-date on the fill day itself is not counted: a share bought at that open is already ex. Dividends count
when entitled, paid or not, and are not reinvested. Excess = (TR_stock - TR_VOO) x 100 percentage points, over
identical dates. A missing open or close leaves the forecast missing (never filled in). Slots 11 and 12 mature after
the fixed twelve-month end: their forecasts stay pending and are excluded from matured scores, without making the
primary record incomplete.

Scores (on matured, scored forecasts only):
* outcome y = 1 if excess > 0, 0 if excess < 0, 0.5 if exactly 0 (tie);
* Brier score mean((p - y)^2), with the constant-0.5 reference 0.25;
* calibration in fixed bins [0,0.2) [0.2,0.4) [0.4,0.6) [0.6,0.8) [0.8,1.0];
* rank association per decision: Spearman correlation between attractiveness (-rank) and realized excess, average
  ranks for ties, only with at least three scored forecasts and some variation in both;
* selected (kept after the decision) versus rejected (assessed, not kept): mean realized excess and counts;
* forecast error: mean of expected minus realized excess, and its mean absolute value.
Horizons overlap (three months from monthly decisions) and the same companies recur, so company-month observations
are not independent; twelve decisions cannot establish statistical significance and nothing here claims it.

Shadow portfolio. 70% VOO and 30% split equally across the twelve frozen candidates (2.5% each), rebalanced at each
fill session with the same data and execution convention; a diagnostic of what a mechanical spread across the
candidates did, with twelve holdings against the AI's at most three, and its own costs. At $100 a 2.5% position is
$2.50, so most rebalancing trades fall under the $1 discretionary minimum and are skipped: the shadow drifts from
70/30 between rebalances, and the report shows its VOO weight after each fill.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

D = Decimal
BINS = (D("0"), D("0.2"), D("0.4"), D("0.6"), D("0.8"), D("1.0"))
HORIZON_SLOTS = 3
SHADOW_STOCK_PCT = D("30")


def horizon(slots: list, k: int) -> Optional[tuple]:
    """(fill session day, end day) for slot k (1-based), or None when it ends after the fixed twelve-month end."""
    j = k + HORIZON_SLOTS - 1
    if j > len(slots):
        return None
    return slots[k - 1].fill.day, slots[j - 1].period_end.day


def total_return(data, symbol: str, start: date, end: date) -> Optional[dict]:
    p0, p1 = data.price(symbol, start, "open"), data.price(symbol, end, "close")
    if p0 is None or p1 is None:
        return None

    def inside(x: str) -> bool:
        return start < date.fromisoformat(x) <= end

    splits = sorted((s for s in data.splits if s["symbol"] == symbol and inside(s["ex_date"])), key=lambda s: s["ex_date"])
    divs = sorted((d for d in data.dividends if d["symbol"] == symbol and inside(d["ex_date"])), key=lambda d: d["ex_date"])
    total_s = D(1)
    for s in splits:
        total_s *= D(s["num"]) / D(s["den"])
    cash = D(0)
    for dv in divs:
        s_at = D(1)
        for s in splits:
            if s["ex_date"] <= dv["ex_date"]:
                s_at *= D(s["num"]) / D(s["den"])
        cash += D(dv["amount"]) * s_at
    tr = (p1 * total_s + cash) / p0 - 1
    return {"symbol": symbol, "start": start.isoformat(), "end": end.isoformat(), "open": str(p0), "close": str(p1),
            "split_factor": str(total_s), "dividends_per_start_share": str(cash), "total_return": str(tr)}


def _avg_ranks(xs: list) -> list[Decimal]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [D(0)] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = D(i + j + 2) / 2
        for t in range(i, j + 1):
            r[order[t]] = avg
        i = j + 1
    return r


def spearman(a: list, b: list) -> Optional[Decimal]:
    if len(a) < 3 or len(set(a)) < 2 or len(set(b)) < 2:
        return None
    ra, rb = _avg_ranks(a), _avg_ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra)
    vb = sum((y - mb) ** 2 for y in rb)
    return (cov / (va * vb).sqrt()).quantize(D("0.0001"))


def score(decisions: list[dict], outcomes: dict, slots: list, last_day: Optional[date]) -> dict:
    """`decisions`: decision payloads; `outcomes`: {(slot, symbol): outcome payload}; `last_day`: the latest session
    valued in the ledger (a horizon ending after it is not matured yet)."""
    rows, pending, missing, per = [], [], [], []
    for p in decisions:
        if p["status"] != "valid":
            continue
        k, dec_ = p["slot"], p["decision"]
        kept = {x["symbol"] for x in dec_["positions"] if x["action"] != "sell"}
        h = horizon(slots, k)
        these = []
        for a in dec_.get("assessments") or []:
            sym = a["symbol"]
            base = {"slot": k, "symbol": sym, "rank": a["rank"], "expected_excess_pct_3m": D(str(a["expected_excess_pct_3m"])),
                    "p": D(str(a["p_positive_excess"])), "selected": sym in kept}
            if h is None:
                pending.append(base | {"why": "matures after the fixed twelve-month end"})
                continue
            if last_day is None or h[1] > last_day:
                pending.append(base | {"why": f"horizon ends {h[1]}"})
                continue
            o, v = outcomes.get((k, sym)), outcomes.get((k, "VOO"))
            if o is None or v is None:
                missing.append(base | {"why": "price missing for the horizon"})
                continue
            ex = (D(o["total_return"]) - D(v["total_return"])) * 100
            y = D(1) if ex > 0 else D(0) if ex < 0 else D("0.5")
            r = base | {"excess_pp": ex, "y": y}
            rows.append(r)
            these.append(r)
        if these:
            per.append({"slot": k, "n": len(these),
                        "spearman": spearman([-x["rank"] for x in these], [x["excess_pp"] for x in these])})
    n = len(rows)
    out = {"scored": n, "pending": len(pending), "missing": len(missing), "decisions_scored": len(per),
           "pending_rows": pending, "missing_rows": missing,
           "notes": ["three-month horizons from monthly decisions overlap and companies recur: observations are not "
                     "independent; no statistical significance is claimed",
                     "secondary diagnostics only: they cannot change the primary verdict"]}
    if n:
        out["brier"] = (sum((r["p"] - r["y"]) ** 2 for r in rows) / n).quantize(D("0.0001"))
        out["brier_constant_half"] = D("0.25")
        cal = []
        for lo, hi in zip(BINS, BINS[1:]):
            inb = [r for r in rows if lo <= r["p"] < hi or (hi == BINS[-1] and r["p"] == hi)]
            cal.append({"bin": f"[{lo}, {hi}{']' if hi == BINS[-1] else ')'}", "n": len(inb),
                        "mean_p": (sum(r["p"] for r in inb) / len(inb)).quantize(D("0.0001")) if inb else None,
                        "mean_y": (sum(r["y"] for r in inb) / len(inb)).quantize(D("0.0001")) if inb else None})
        out["calibration"] = cal
        sel = [r["excess_pp"] for r in rows if r["selected"]]
        rej = [r["excess_pp"] for r in rows if not r["selected"]]
        out["selected_vs_rejected"] = {
            "selected_n": len(sel), "rejected_n": len(rej),
            "selected_mean_excess_pp": (sum(sel) / len(sel)).quantize(D("0.0001")) if sel else None,
            "rejected_mean_excess_pp": (sum(rej) / len(rej)).quantize(D("0.0001")) if rej else None}
        err = [r["expected_excess_pct_3m"] - r["excess_pp"] for r in rows]
        out["forecast_error"] = {"mean_pp": (sum(err) / n).quantize(D("0.0001")),
                                 "mean_abs_pp": (sum(abs(e) for e in err) / n).quantize(D("0.0001"))}
        rhos = [x["spearman"] for x in per if x["spearman"] is not None]
        out["rank_association"] = {"per_decision": per, "decisions_with_rho": len(rhos),
                                   "mean_spearman": (sum(rhos) / len(rhos)).quantize(D("0.0001")) if rhos else None}
    out["rows"] = rows
    return out
