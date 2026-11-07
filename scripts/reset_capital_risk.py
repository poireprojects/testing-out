"""Strategic reset: capital and risk analysis (simulation only; nothing here changes the app's limits).

    python scripts/reset_capital_risk.py OUT_DIR

1. Which past failures larger capital fixes. It uses each hypothesis's MEASURED gross result on unseen data,
   with costs split into proportional (spread, slippage, impact) and fixed (cent-rounded daily fees).
2. Yearly cost drag by capital and trading frequency.
3. Monte Carlo of fixed-fractional risk (0.25%, 0.5%, 1%, 2%, and a 5% STRESS case) for hypothetical edges
   stated in R (the amount risked per trade). Reported: drawdowns, ruin, losing streaks, monthly and yearly
   loss probabilities.
4. Capital needed for $1,000 / $3,000 / $5,000 a month: the return that is mathematically required against
   the return credibly achievable from documented evidence.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

CAPS = [100, 500, 1_000, 5_000, 10_000, 25_000, 50_000, 100_000]
RISKS = [0.0025, 0.005, 0.01, 0.02, 0.05]
TARGETS = [1_000, 3_000, 5_000]
DAILY_FEE_FLOOR = 0.03         # SEC + TAF + CAT, each rounded up to $0.01 per day when nonzero (Alpaca rule)

# Measured on data the rule had not seen (gross = before all costs; spread+slippage as each test modelled it)
FAILED = [
    {"id": "R1 rules + ranker (intraday, 30 min)", "gross": 0.042, "spreadSlip": 0.13, "tradesPerDay": 2.8, "adv": 2e8},
    {"id": "R1 H2 relative strength (intraday)", "gross": 0.073, "spreadSlip": 0.13, "tradesPerDay": 1.9, "adv": 2e8},
    {"id": "P2 H1 momentum to close", "gross": -0.016, "spreadSlip": 0.13, "tradesPerDay": 2.4, "adv": 2e8},
    {"id": "P2 H2 gap stocks in play", "gross": 0.092, "spreadSlip": 0.20, "tradesPerDay": 1.0, "adv": 5e7},
    {"id": "P2 H3 passive limit entry", "gross": -0.052, "spreadSlip": 0.065, "tradesPerDay": 2.3, "adv": 2e8},
    {"id": "P2 H4 ETFs overnight", "gross": 0.052, "spreadSlip": 0.13, "tradesPerDay": 4.0, "adv": 5e9},
    {"id": "P4 earnings-day continuation", "gross": 0.086, "spreadSlip": 0.244, "tradesPerDay": 3.8, "adv": 5e7},
]


def impact_pct(position: float, adv: float, daily_vol: float = 0.02, y: float = 0.7) -> float:
    """Square-root market impact per side (Toth et al. 2011; Almgren et al. 2005), in percent."""
    return y * daily_vol * math.sqrt(max(position, 0.0) / adv) * 100


def fee_pct(position: float, trades_per_day: float) -> float:
    """Cent-rounded regulatory fees per round trip, aggregated per day across that day's trades."""
    return DAILY_FEE_FLOOR / max(trades_per_day, 1e-9) / position * 100


def capital_fixes() -> list[dict]:
    out = []
    for s in FAILED:
        row = {"strategy": s["id"], "grossPct": s["gross"], "spreadSlipPct": s["spreadSlip"], "byCapital": {}}
        for c in CAPS:
            pos = 0.2 * c                                    # the app's sizing: 2 slots of 20%
            f = fee_pct(pos, s["tradesPerDay"])
            imp = 2 * impact_pct(pos, s["adv"])
            row["byCapital"][f"${c:,}"] = {"position": pos, "feesPct": f, "impactPct": imp,
                                           "netPct": s["gross"] - s["spreadSlip"] - f - imp}
        row["bestPossibleNetPct"] = s["gross"] - s["spreadSlip"]     # fees -> 0, impact ignored
        row["capitalFixesIt"] = row["bestPossibleNetPct"] > 0
        out.append(row)
    return out


def cost_drag() -> dict:
    """Yearly cost as % of capital: (round trips per year) x (position/capital) x (cost per round trip)."""
    styles = {"intraday, 2 round trips/day, liquid stocks": (500, 0.4, 0.13, 2.0, 2e8),
              "intraday on news stocks (phase 4 costs)": (500, 0.4, 0.244, 2.0, 5e7),
              "swing, ~1 round trip/week": (50, 0.5, 0.13, 0.2, 2e8),
              "monthly rebalance of ETFs, 50% turnover": (12, 0.5, 0.03, 0.05, 5e9)}
    out = {}
    for name, (n, frac, ss, tpd, adv) in styles.items():
        out[name] = {}
        for c in CAPS:
            pos = frac * c
            per = ss + fee_pct(pos, max(tpd, 1.0)) + 2 * impact_pct(pos, adv)
            out[name][f"${c:,}"] = {"costPerRoundTripPct": per, "yearlyDragPctOfCapital": n * frac * per}
    return out


def simulate(edge_r: float, risk: float, trades_per_year: int = 250, years: int = 5, paths: int = 5_000,
             loss_prob: float = 0.55, stop_overshoot: float = 0.05, seed: int = 7) -> dict:
    """Fixed-fractional sizing. Losers lose 1R (plus an overshoot), winners are exponential with the mean that
    gives the stated net edge per trade. Equity compounds; ruin = a 50% or 90% drawdown at any time."""
    rng = np.random.default_rng(seed)
    loss = -(1 + stop_overshoot)
    win_mean = (edge_r - loss_prob * loss) / (1 - loss_prob)
    n = trades_per_year * years
    lose = rng.random((paths, n)) < loss_prob
    r = np.where(lose, loss, rng.exponential(win_mean, (paths, n)))
    eq = np.cumprod(1 + risk * r, axis=1)
    eq = np.maximum(eq, 0.0)
    peak = np.maximum.accumulate(np.concatenate([np.ones((paths, 1)), eq], axis=1), axis=1)[:, 1:]
    dd = 1 - eq / peak
    per_month = trades_per_year // 12
    months = eq[:, per_month - 1::per_month][:, : 12 * years]
    mret = months / np.concatenate([np.ones((paths, 1)), months[:, :-1]], axis=1) - 1
    yr = eq[:, trades_per_year - 1::trades_per_year] / np.concatenate([np.ones((paths, 1)), eq[:, trades_per_year - 1::trades_per_year][:, :-1]], axis=1) - 1
    streak = np.zeros(paths)
    for i in range(0, paths, 2000):                       # longest run of losing trades (first year)
        blk = lose[i:i + 2000, :trades_per_year].astype(int)
        best = np.zeros(blk.shape[0]); cur = np.zeros(blk.shape[0])
        for j in range(blk.shape[1]):
            cur = (cur + 1) * blk[:, j]
            best = np.maximum(best, cur)
        streak[i:i + 2000] = best
    sd_r = float(np.std(np.where(lose[:200], loss, rng.exponential(win_mean, (200, n)))))
    return {"edgeR": edge_r, "riskPerTrade": risk, "tradesPerYear": trades_per_year,
            "impliedAnnualSharpe": edge_r / sd_r * math.sqrt(trades_per_year),
            "annualReturnMedianPct": float(np.median(yr) * 100), "annualReturnMeanPct": float(np.mean(yr) * 100),
            "annualReturnP05Pct": float(np.percentile(yr, 5) * 100), "annualReturnP95Pct": float(np.percentile(yr, 95) * 100),
            "monthReturnP05Pct": float(np.percentile(mret, 5) * 100), "monthReturnMedianPct": float(np.median(mret) * 100),
            "monthReturnP95Pct": float(np.percentile(mret, 95) * 100),
            "pLosingMonth": float((mret < 0).mean()), "pLosingYear": float((yr < 0).mean()),
            "maxDrawdown5yMedianPct": float(np.median(dd.max(axis=1)) * 100), "maxDrawdown5yP95Pct": float(np.percentile(dd.max(axis=1), 95) * 100),
            "pDrawdown25": float((dd.max(axis=1) >= 0.25).mean()), "pDrawdown50": float((dd.max(axis=1) >= 0.5).mean()),
            "pDrawdown90": float((dd.max(axis=1) >= 0.9).mean()),
            "longestLosingStreakMedian": float(np.median(streak)), "longestLosingStreakP95": float(np.percentile(streak, 95)),
            "endingWealth5yMedian": float(np.median(eq[:, -1]))}


def income_needs() -> dict:
    """Capital required = 12 x target / annual return, for evidence-anchored return assumptions."""
    anchors = {
        "4% sustainable withdrawal (spending rule, diversified portfolio)": 0.04,
        "7% (long-run real-ish equity return; US 1926-2020 nominal was ~9-10%)": 0.07,
        "10% (equity nominal long-run, with 50-84% historical drawdowns)": 0.10,
        "20% (top-decile hedge fund year; not sustained by any verifiable retail record)": 0.20,
        "60%/yr = 5%/month (claimed by many bot vendors; no verified long record)": 0.60,
    }
    out = {"capitalNeeded": {}, "requiredReturnByCapital": {}}
    for name, r in anchors.items():
        out["capitalNeeded"][name] = {f"${t:,}/month": 12 * t / r for t in TARGETS}
    for c in CAPS:
        out["requiredReturnByCapital"][f"${c:,}"] = {f"${t:,}/month": 12 * t / c * 100 for t in TARGETS}
    return out


def charts(res: dict, figs: Path) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
    S1, S2 = "#2a78d6", "#eb6834"

    def style(ax, title, sub):
        ax.set_facecolor(SURFACE)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(colors=INK2, labelsize=10, length=0)
        ax.set_axisbelow(True)
        ax.set_title(title, loc="left", fontsize=14, color=INK, fontweight="bold", pad=26)
        ax.text(0, 1.02, sub, transform=ax.transAxes, fontsize=10, color=INK2)

    out = []
    # 1. gross edge vs proportional cost (what capital cannot fix)
    rows = res["capitalFixes"]
    fig, ax = plt.subplots(figsize=(10, 4.8), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    y = np.arange(len(rows))[::-1]
    ax.barh(y + 0.19, [r["grossPct"] for r in rows], 0.34, color=S1, label="measured gross edge per trade (unseen data)")
    ax.barh(y - 0.19, [r["spreadSlipPct"] for r in rows], 0.34, color=S2, label="spread + slippage per round trip (does not shrink with capital)")
    ax.axvline(0, color=INK2, linewidth=1)
    ax.set_yticks(y, [r["strategy"] for r in rows], fontsize=9.5, color=INK)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.2f}%")
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    style(ax, "Bigger accounts would not have rescued any failed strategy",
          "even with fees at zero, every strategy's gross edge was smaller than its spread and slippage")
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.08), ncol=1, frameon=False, fontsize=9, labelcolor=INK2)
    fig.tight_layout()
    p = figs / "1-gross-edge-vs-proportional-cost.png"; fig.savefig(p, facecolor=SURFACE); plt.close(fig); out.append(p)
    # 2. capital needed per income target at evidence-anchored returns
    cn = res["income"]["capitalNeeded"]
    names = list(cn)[:4]
    fig, ax = plt.subplots(figsize=(10, 4.6), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    x = np.arange(len(TARGETS))
    w = 0.2
    cols = ["#1c4f8c", S1, "#7fb0e8", S2]
    for k, n in enumerate(names):
        vals = [cn[n][f"${t:,}/month"] / 1000 for t in TARGETS]
        ax.bar(x + (k - 1.5) * w, vals, w * 0.92, color=cols[k], label=n.split(" (")[0])
        for xi, v in zip(x, vals):
            ax.text(xi + (k - 1.5) * w, v + 20, f"${v:,.0f}k", ha="center", fontsize=7.5, color=INK2)
    ax.set_xticks(x, [f"${t:,}/month" for t in TARGETS], fontsize=10, color=INK)
    ax.yaxis.set_major_formatter(lambda v, _: f"${v:,.0f}k")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    style(ax, "Capital needed for a monthly income, at evidence-based return rates",
          "average income only; bad years still lose money, so a dependable salary needs the lower rates")
    ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK2)
    fig.tight_layout()
    p = figs / "2-capital-needed-for-income.png"; fig.savefig(p, facecolor=SURFACE); plt.close(fig); out.append(p)
    # 3. drawdown risk by risk-per-trade for a GOOD hypothetical edge
    mc = [m for m in res["monteCarlo"] if abs(m["edgeR"] - 0.05) < 1e-9]
    fig, ax = plt.subplots(figsize=(10, 4.4), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    xs = np.arange(len(mc))
    ax.bar(xs - 0.2, [m["maxDrawdown5yMedianPct"] for m in mc], 0.38, color=S1, label="typical (median) worst drawdown over 5 years")
    ax.bar(xs + 0.2, [m["pDrawdown50"] * 100 for m in mc], 0.38, color=S2, label="chance of losing half the account at some point (%)")
    ax.set_xticks(xs, [f"{m['riskPerTrade']*100:g}% risk/trade" + (" (stress)" if m["riskPerTrade"] >= 0.05 else "") for m in mc], fontsize=10, color=INK)
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0f}%")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    style(ax, "Even a real edge is dangerous when sized aggressively",
          "hypothetical +0.05R-per-trade edge (an annual Sharpe of about 0.5), 250 trades a year, 5,000 simulated 5-year paths")
    ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK2)
    fig.tight_layout()
    p = figs / "3-risk-per-trade-drawdowns.png"; fig.savefig(p, facecolor=SURFACE); plt.close(fig); out.append(p)
    return out


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    res = {"capitalFixes": capital_fixes(), "costDrag": cost_drag(), "income": income_needs(), "monteCarlo": []}
    for edge in (-0.07, 0.0, 0.05, 0.10):
        for risk in RISKS:
            res["monteCarlo"].append(simulate(edge, risk))
    (out / "capital_risk.json").write_text(json.dumps(res, indent=1))
    figs = out / "figures"
    figs.mkdir(exist_ok=True)
    for p in charts(res, figs):
        print("wrote", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
