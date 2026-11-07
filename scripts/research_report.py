"""Render research round 1 (REPORT.md + figures) from the saved JSON results.

    python scripts/research_report.py research/runs/2026-11-06-round1
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockpilot.research.economics import economics  # noqa: E402
from stockpilot.risk.fees import CostModel  # noqa: E402

SURFACE, INK, INK2, GRID, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1", "#a3a29d"
S1, S2 = "#2a78d6", "#eb6834"
ROWS = [("no_trade", "doing nothing"), ("rules_ranker", "rules + ranker (the app's logic)"),
        ("h1", "H1 mean reversion"), ("h2", "H2 relative strength"), ("h3", "H3 market intraday momentum"),
        ("dense_random", "random pick (same slots)"), ("h4", "H4 statistical model"), ("h5", "H5 rules + model filter")]


def style(ax, title, subtitle):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=10, length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(title, loc="left", fontsize=14, color=INK, fontweight="semibold", pad=26)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=10, color=INK2)


def pct(v, nd=3):
    return f"{v:+.{nd}f}%"


def chart_holdout(hold: dict, figs: Path) -> Path:
    ev = hold["evals"]
    rows = []
    for k, lab in ROWS:
        if k == "no_trade":
            rows.append((lab, 0.0, 0.0, 0.0, 0))
        else:
            e = ev[k]
            rows.append((lab, e["avgRetPct"], e["ci90"][0], e["ci90"][1], e["trades"]))
    rows = rows[::-1]
    fig, ax = plt.subplots(figsize=(9, 5.0), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    for i, (lab, v, lo, hi, n) in enumerate(rows):
        if n == 0:
            ax.text(0.004, i, "no trades — it found nothing worth buying" if "model" in lab else "$0, no risk",
                    va="center", fontsize=9, color=INK2)
            ax.plot([0], [i], marker="o", markersize=8, color=MUTED, markeredgecolor=SURFACE, markeredgewidth=2)
            continue
        ax.plot([lo, hi], [i, i], color=S1, linewidth=2, solid_capstyle="round")
        ax.plot([v], [i], marker="o", markersize=8, color=S1, markeredgecolor=SURFACE, markeredgewidth=2)
        ax.text(hi + 0.006, i, f"{pct(v)}  ·  {n:,} trades", va="center", fontsize=9, color=INK)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows], fontsize=10, color=INK)
    ax.axvline(0, color=INK2, linewidth=1)
    ax.set_xlim(-0.45, 0.32)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: "0%" if abs(v) < 1e-9 else f"{v:+.1f}%"))
    style(ax, "Result per trade on the unseen holdout, after costs",
          "81 never-used trading days (May 1 – Aug 26, 2026) · dot = average · line = 90% range")
    fig.tight_layout()
    p = figs / "1-holdout-net-per-trade.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    return p


def chart_gross(dev: dict, hold: dict, figs: Path, be: float) -> Path:
    keys = [("rules_ranker", "rules + ranker"), ("h1", "H1 mean reversion"), ("h2", "H2 relative strength"),
            ("h3", "H3 market momentum"), ("dense_random", "random pick")][::-1]
    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    h = 0.34
    for j, (res, color, lab) in enumerate(((dev, S1, "development (197 days)"), (hold, S2, "holdout (81 days)"))):
        vals = [res["evals"][k]["grossAvgRetPct"] for k, _ in keys]
        bars = ax.barh([i + (h / 2 + 0.02) * (1 if j == 0 else -1) for i in range(len(keys))], vals, height=h,
                       color=color, label=lab)
        for b, v in zip(bars, vals):
            ax.text(v + (0.003 if v >= 0 else -0.003), b.get_y() + b.get_height() / 2, pct(v), va="center",
                    ha="left" if v >= 0 else "right", fontsize=8.5, color=INK)
    ax.axvline(be, color=INK, linewidth=1.2, linestyle=(0, (4, 3)))
    ax.text(be, len(keys) - 0.35, f" break-even ≈ {be:.2f}%\n (spread + slippage + fees, $20 trade)", fontsize=8.5,
            color=INK, va="top")
    ax.set_yticks(range(len(keys)), [k[1] for k in keys], fontsize=10, color=INK)
    ax.axvline(0, color=INK2, linewidth=1)
    ax.set_xlim(-0.05, 0.24)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: "0%" if abs(v) < 1e-9 else f"{v:+.2f}%"))
    style(ax, "Average move captured per trade BEFORE costs",
          "every approach is far below what a $20 round trip costs · 1-minute IEX bars, 40 symbols")
    ax.legend(loc="lower right", frameon=False, fontsize=9, labelcolor=INK)
    fig.tight_layout()
    p = figs / "2-gross-vs-break-even.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    return p


def chart_calibration(hold: dict, figs: Path) -> Path:
    m = hold["models"]["h5"]
    t = [r for r in m["calibration"]["table"] if r["n"] >= 10]
    fig, ax = plt.subplots(figsize=(6.4, 5.0), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    ax.plot([0, 0.7], [0, 0.7], color=MUTED, linewidth=1, linestyle=(0, (4, 3)))
    ax.scatter([r["predicted"] for r in t], [r["observed"] for r in t], s=[max(30, min(400, r["n"] / 8)) for r in t],
               color=S1, edgecolor=SURFACE, linewidth=2, zorder=3)
    for r in t:
        ax.text(r["predicted"] + 0.012 + min(0.03, r["n"] / 100000), r["observed"], f"{r['n']:,}", fontsize=8.5, color=INK2, va="center")
    ax.set_xlim(0, 0.7)
    ax.set_ylim(0, 0.7)
    ax.set_xlabel("predicted chance the trade makes money", color=INK2, fontsize=10)
    ax.set_ylabel("share that actually did", color=INK2, fontsize=10)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    style(ax, "Statistical filter: calibrated, but no edge",
          f"holdout · {m['n']:,} rule signals · AUC {m['auc']:.2f} · Brier {m['brier']:.3f} vs {m['brierClimatology']:.3f} base rate")
    fig.tight_layout()
    p = figs / "3-model-calibration-holdout.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    return p


def markdown(run: Path, dev: dict, hold: dict, llm: dict, econ: dict) -> str:
    be = econ["breakEvenBySize"]["$20"]["totalPct"]
    L = ["# Research round 1 — can better trade selection beat costs?", "",
         "Pre-registered in `research/PREREGISTRATION-2026-11-06.md` (committed before any result). "
         "Development = walk-forward over 197 days (2025-09-16 → 2026-04-30 and the already examined "
         "2026-09-11 → 2026-11-05). **Holdout = 81 days (2026-05-01 → 2026-08-26), registered before download and "
         "evaluated once** (`research/HOLDOUT_USED.json`). 40 symbols, Alpaca IEX 1-minute bars, "
         f"{hold['coverage']['symbolDays']}/{hold['coverage']['expected']} holdout symbol-days and "
         f"{dev['coverage']['symbolDays']}/{dev['coverage']['expected']} development symbol-days present.", "",
         "## Answer", "",
         "* **No positive-expected-value strategy was found.** Every approach that trades loses money after costs on "
         "the unseen holdout; doing nothing was best.",
         "* **The statistical models did not beat the rules on profit** — they did better only by declining every trade. "
         "Their win probabilities are calibrated and slightly informative, but they cannot predict the *size* of "
         "results, so no candidate ever showed a positive expected result after costs.",
         "* **The 3B language model adds no measurable value**: it approved 72 of 72 picks and ~14% of its explanations "
         "contradicted the app's own numbers.",
         f"* **Why:** a $20 round trip costs ≈{be:.2f}%; the best measured signal is worth ≈0.04% over 30 minutes.", "",
         "![holdout](figures/1-holdout-net-per-trade.png)", "", "![gross](figures/2-gross-vs-break-even.png)", "",
         "## Results (after costs, $100 account, 2 slots of $20)", "",
         "| approach | dev trades | dev net/trade | dev 90% range | holdout trades | holdout net/trade | holdout 90% range | holdout $ | verdict |",
         "|---|---|---|---|---|---|---|---|---|"]
    for k, lab in ROWS[1:]:
        d, h = dev["evals"][k], hold["evals"][k]
        v = hold.get("verdicts", {}).get(k, {}).get("verdict", "baseline")
        L.append(f"| {lab} | {d['trades']:,} | {pct(d['avgRetPct'])} | {pct(d['ci90'][0])} … {pct(d['ci90'][1])} | "
                 f"{h['trades']:,} | {pct(h['avgRetPct'])} | {pct(h['ci90'][0])} … {pct(h['ci90'][1])} | "
                 f"{h['totalPnl']:+.2f} | {v} |")
    L += ["| doing nothing | 0 | 0 | — | 0 | 0 | — | 0.00 | baseline |", "",
          "Percentages are per trade (P/L ÷ the $20 position), not of the account. Holm–Bonferroni across H1–H5: "
          "every adjusted p = 1.00. \"insufficient\" = fewer than 100 trades.", "",
          "### Why each was rejected (holdout)", ""]
    for k, v in hold["verdicts"].items():
        L.append(f"* **{k.upper()}** — {v['verdict']}: {'; '.join(v['reasons'])}")
    m4, m5 = hold["models"]["h4"], hold["models"]["h5"]
    L += ["", "## The statistical models", "",
          "| model | holdout rows | base rate | Brier | Brier of base rate | AUC (win/lose) | AUC of expected result | calibration error |",
          "|---|---|---|---|---|---|---|---|",
          f"| H4 dense (every symbol, every 30 min) | {m4['n']:,} | {m4['baseRate']:.1%} | {m4['brier']:.4f} | {m4['brierClimatology']:.4f} | {m4['auc']:.3f} | {m4['aucExpNet']:.3f} | {m4['calibration']['ece']:.4f} |",
          f"| H5 filter on the rules' signals | {m5['n']:,} | {m5['baseRate']:.1%} | {m5['brier']:.4f} | {m5['brierClimatology']:.4f} | {m5['auc']:.3f} | {m5['aucExpNet']:.3f} | {m5['calibration']['ece']:.4f} |",
          "", "![calibration](figures/3-model-calibration-holdout.png)", "",
          "L2-regularised logistic + ridge regression (numpy), 15 point-in-time features (+9 strategy features for H5), "
          "standardised on training days only, expanding walk-forward with a 1-day embargo. The H5 model ships with "
          "the app at **shadow** stage (`stockpilot/assets/models/strategy-filter-v1.json`): it shows its estimate for "
          "every candidate and never changes a trade.", "",
          "## Why the original strategies lose (development, 197 days)", ""]
    dg = dev["diagnosis"]
    L += ["| strategy | signals | before costs | after costs | 30-min move vs average (90% range) | best/worst excursion | gave back a move > cost |",
          "|---|---|---|---|---|---|---|"]
    for sid, r in dg["strategies"].items():
        u = r["fwd30VsUnconditional"]
        L.append(f"| {sid} | {r['trades']:,} | {pct(r['realizedGrossPct'])} | {pct(r['realizedNetPct'])} | "
                 f"{pct(u['diff'])} ({pct(u['ci90'][0])} … {pct(u['ci90'][1])}) | {pct(r['avgMfePct'], 2)} / {pct(r['avgMaePct'], 2)} | "
                 f"{r['mfeAboveCostButLost']:.0%} |")
    mv = dg["moveVsCost"]
    L += ["", "* **Signal information is real but tiny.** Momentum and volume-breakout entries drift +0.03–0.04% more than "
          "an average entry over 30 minutes; trend, opening-range and VWAP entries ≈0.",
          f"* **Moves are too small for the costs.** The median 30-minute move is {mv['medianAbs30mGrossPct']:.2f}% in "
          f"either direction; break-even is {be:.2f}%.",
          "* **Exits:** the strategies' own exit rules close most trades at a small loss before costs; only the rare "
          "targets (≈5–10%) make +1.3%. 11–29% of trades were ahead by more than the cost at some point and still lost.",
          "* **Regime matters, but only after the fact:** up-and-volatile days averaged "
          f"{pct(dg['regimes'].get('up day, volatile', {}).get('grossPct', 0))} before costs, down-and-volatile "
          f"{pct(dg['regimes'].get('down day, volatile', {}).get('grossPct', 0))}. Nothing here predicts the day in advance.",
          f"* **The ranker does select better than chance** ({pct(dg['selection']['ranker']['grossPct'])} vs "
          f"{pct(dg['selection']['randomPick']['grossPct'])} random before costs) — not by enough.", "",
          "## Language model (Llama 3.2 3B, local)", "",
          f"* {llm['summary']['answers']} recorded answers: approved {llm['summary']['approved']}, average confidence "
          f"{llm['summary']['avgConfidence']:.2f}, median {llm['summary']['medianSeconds']:.0f} s per answer.",
          f"* {llm['summary']['inconsistent']} ({llm['summary']['inconsistentShare']:.0%}) explanations contradicted numbers "
          "the app computed (invented stop distances, RSI called \"oversold\" at 64, prices written as percentages). "
          "From this build such answers are rejected, and the model is given the computed numbers as facts.",
          "* Rules + ranker vs rules + LLM confirmation: identical trades and results (build 20 replay). It fails two "
          "pre-registered checks (latency > 30 s, ≥ 5% inconsistent), so it gets **no trade authority with real money**; "
          "the default decision mode is now rules + ranker.",
          "* A stronger local model was not downloaded: on a 4-core laptop a 7–8B model takes ~2–3× longer per answer, "
          "which makes intraday signals stale before it replies.", "",
          "## Economics", "",
          "| position | spread | slippage | SEC | TAF | CAT | fee rounding | total round trip |", "|---|---|---|---|---|---|---|---|"]
    for size, b in econ["breakEvenBySize"].items():
        L.append(f"| {size} | {b['spreadPct']:.3f}% | {b['slippagePct']:.3f}% | {b['secPct']:.4f}% | {b['tafPct']:.4f}% | "
                 f"{b['catPct']:.5f}% | {b['roundingPct']:.4f}% | **{b['totalPct']:.3f}%** |")
    L += ["", "Spread (3 bps) and slippage (5 bps per side) are modeled assumptions; fees are from the broker's current "
          "schedule (rounded once per fee type per day). Bigger positions only dilute the fixed fee rounding — the "
          "0.13% of spread + slippage stays, so more capital does not fix a missing edge.", "",
          "Hypothetical month (21 days, 4 trades/day, $20-of-$100 sizing scaled to capital) — arithmetic, not a forecast:", "",
          "| capital | −0.10%/trade | 0% | +0.05%/trade | +0.10%/trade | 90% range at +0.05% |", "|---|---|---|---|---|---|"]
    sc = [s for s in econ["scenarios"] if s["tradesPerDay"] == 4.0]
    for cap in (100.0, 500.0, 1000.0, 5000.0, 10000.0):
        row = {s["netPerTradePct"]: s for s in sc if s["capital"] == cap}
        L.append(f"| ${cap:,.0f} | {row[-0.10]['monthlyPnl']:+.2f} | {row[0.0]['monthlyPnl']:+.2f} | "
                 f"{row[0.05]['monthlyPnl']:+.2f} ({row[0.05]['onAccountPct']:+.2f}% of account) | "
                 f"{row[0.10]['monthlyPnl']:+.2f} | {row[0.05]['p05']:+.2f} … {row[0.05]['p95']:+.2f} |")
    need = econ["capitalNeeded"]
    L += ["", f"Capital needed for $100/month at +0.05% net per trade and 4 trades/day: **${need['+0.05%']['$100/mo']:,.0f}**; "
          f"at +0.10%: ${need['+0.10%']['$100/mo']:,.0f}. A $99/month data plan would need ≈"
          f"${(99 / (84 * 0.2 * 0.0005)):,.0f} of capital at +0.05% just to pay for itself. None of these edges exists "
          "today; they show the scale required if one is found.", "",
          "## What would count as progress next", "",
          "1. **Forward shadow evidence** (starts automatically from 2026-11-09): the app records the rules, ranker and "
          "model estimate for every candidate; nothing is promoted without ≥ 100 forward trades over ≥ 30 days, "
          "positive after costs with a multiple-testing-adjusted p < 0.05, and your approval.",
          "2. **Cut costs, not just pick better:** the measured signal is ≈¼ of the cost. Realistic routes are lower "
          "turnover (fewer, longer holds) or verified cheaper execution (limit orders that actually fill) — both need "
          "new pre-registered tests; neither is assumed.",
          "3. **Longer horizons** (multi-day) need overnight risk the app does not take today (fractional stops are "
          "day orders). That is a change of mission and needs your decision first.", ""]
    return "\n".join(L)


def main(run: Path) -> int:
    dev = json.loads((run / "dev.json").read_text())
    hold = json.loads((run / "holdout.json").read_text())
    llm = json.loads((run / "llm_consistency_audit.json").read_text())
    econ = economics(CostModel(), 3.0, per_trade_sd_pct=hold["evals"]["rules_ranker"]["sdPct"])
    (run / "economics.json").write_text(json.dumps(econ, indent=1))
    figs = run / "figures"
    figs.mkdir(exist_ok=True)
    be = econ["breakEvenBySize"]["$20"]["totalPct"]
    for p in (chart_holdout(hold, figs), chart_gross(dev, hold, figs, be), chart_calibration(hold, figs)):
        print("wrote", p)
    (run / "REPORT.md").write_text(markdown(run, dev, hold, llm, econ))
    print("wrote", run / "REPORT.md")
    write_app_summary(dev, hold, llm, econ)
    return 0


def write_app_summary(dev: dict, hold: dict, llm: dict, econ: dict) -> None:
    """The compact evidence the app's Performance page shows (shipped in stockpilot/assets/research)."""
    rows = []
    for k, lab in ROWS:
        if k == "no_trade":
            rows.append({"key": k, "label": lab, "trades": 0, "netPct": 0.0, "lo": 0.0, "hi": 0.0, "pnl": 0.0,
                         "verdict": "baseline"})
            continue
        e = hold["evals"][k]
        rows.append({"key": k, "label": lab, "trades": e["trades"], "netPct": e["avgRetPct"], "grossPct": e["grossAvgRetPct"],
                     "lo": e["ci90"][0], "hi": e["ci90"][1], "pnl": e["totalPnl"],
                     "devTrades": dev["evals"][k]["trades"], "devNetPct": dev["evals"][k]["avgRetPct"],
                     "verdict": hold.get("verdicts", {}).get(k, {}).get("verdict", "baseline")})
    m5 = hold["models"]["h5"]
    out = {"round": "research round 1", "holdout": "2026-05-01 → 2026-08-26 (81 trading days, used once)",
           "development": "2025-09-16 → 2026-04-30 + 2026-09-11 → 2026-11-05 (197 days)",
           "source": "Alpaca IEX 1-minute bars, 40 symbols, $20 positions, 2 slots, after spread, slippage and fees",
           "breakEvenPct": econ["breakEvenBySize"]["$20"]["totalPct"], "bestSignalPct": 0.04, "rows": rows,
           "model": {"auc": m5["auc"], "brier": m5["brier"], "brierBase": m5["brierClimatology"],
                     "ece": m5["calibration"]["ece"], "expNetAuc": m5["aucExpNet"], "n": m5["n"]},
           "llm": llm["summary"],
           "capitalFor100PerMonth": econ["capitalNeeded"]["+0.05%"]["$100/mo"],
           "conclusion": "No approach made money after costs on the unseen holdout; not trading was best."}
    dst = ROOT / "stockpilot" / "assets" / "research"
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "round1-summary.json").write_text(json.dumps(out, indent=1))
    print("wrote", dst / "round1-summary.json")


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
