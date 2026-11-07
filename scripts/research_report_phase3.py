"""Render phase 3 figures, tables and the app's evidence summary from the saved JSON (and dev data for
the H1 excess curve).

    python scripts/research_report_phase3.py research/runs/2026-11-07-phase3 P3_DATA_DIR
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
S1, S2 = "#2a78d6", "#eb6834"


def style(ax, title, subtitle, grid_axis="y"):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=10, length=0)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(title, loc="left", fontsize=14, color=INK, fontweight="bold", pad=26)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=10, color=INK2)


def pct(v, nd=3):
    return "—" if v is None else f"{v:+.{nd}f}%"


def chart_tom(review, dev, hold, figs: Path) -> Path:
    dec = review["market"]["1926-2005"]["turnOfMonth"]["byDecade"]
    labels = list(dec) + ["2006–20\n(holdout A)", "2022–26"]
    tom = [v["tomBps"] for v in dec.values()] + [hold["results"]["P3-H2"]["tomVsOther"]["tomMeanBps"],
                                                dev["H2"]["2022-2026"]["tomVsOther"]["tomMeanBps"]]
    oth = [v["otherBps"] for v in dec.values()] + [hold["results"]["P3-H2"]["tomVsOther"]["otherMeanBps"],
                                                  dev["H2"]["2022-2026"]["tomVsOther"]["otherMeanBps"]]
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(10, 4.8), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    ax.bar(x - 0.2, tom, 0.38, color=S1, label="turn-of-month days (−1 … +3)")
    ax.bar(x + 0.2, oth, 0.38, color=S2, label="all other days")
    ax.axhline(0, color=INK2, linewidth=1)
    ax.axvline(len(dec) - 0.5, color=INK2, linewidth=0.8, linestyle=":")
    ax.set_xticks(x, labels, fontsize=9, color=INK)
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0f} bps")
    style(ax, "The turn-of-the-month effect faded after it was published",
          "average daily market return above T-bills · development 1926–2005 by decade, then data the rule had not seen")
    ax.legend(loc="upper right", frameon=False, fontsize=9, labelcolor=INK2)
    fig.tight_layout()
    p = figs / "1-turn-of-month-by-period.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    return p


def chart_costs(review, figs: Path) -> Path:
    c = review["costs"]
    freq = ["daily", "weekly", "monthly", "quarterly"]
    fig, ax = plt.subplots(figsize=(10, 4.4), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    x = np.arange(len(freq))
    for k, (cap, col) in enumerate((("$100", S2), ("$10,000", S1))):
        v = [c[cap]["annualDragPct"][f] for f in freq]
        ax.bar(x + (k - 0.5) * 0.38, v, 0.36, color=col, label=f"{cap} account")
        for xi, vi in zip(x, v):
            ax.text(xi + (k - 0.5) * 0.38, vi + 0.6, f"{vi:.1f}%", ha="center", fontsize=8.5, color=INK2)
    ax.set_xticks(x, ["rebalance daily", "weekly", "monthly", "quarterly"], fontsize=10, color=INK)
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0f}%")
    style(ax, "Holding longer is what changes the economics",
          "yearly cost of replacing the whole portfolio at each rebalance: spread + slippage + fees (cents rounded up daily)")
    ax.legend(loc="upper right", frameon=False, fontsize=9, labelcolor=INK2)
    fig.tight_layout()
    p = figs / "2-cost-by-holding-period.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    return p


def chart_h1(data_dir: Path, figs: Path) -> Path:
    from stockpilot.research.academic import load_series, price_index
    from stockpilot.research.phase3 import equal_weight_targets, h1_targets
    from stockpilot.research.portfolio import SimCosts, monthly_returns, simulate
    ind = load_series(data_dir, "ind12_daily").loc[:"2005-12-30"]
    p = price_index(ind)
    t, log = h1_targets(p)
    ex = [e["execution"] for e in log]
    R = ind.loc[ex[0]:]
    h = simulate(R, t, 10_000, SimCosts())
    e = simulate(R, equal_weight_targets(p, ex), 10_000, SimCosts())
    exc = (monthly_returns(h.ret) - monthly_returns(e.ret)).dropna()
    cum = (1 + exc).cumprod() - 1
    fig, ax = plt.subplots(figsize=(10, 4.4), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    xs = [q.to_timestamp() for q in cum.index]
    ax.plot(xs, cum.values * 100, color=S1, linewidth=2)
    ax.axhline(0, color=INK2, linewidth=1)
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:+.0f}%")
    style(ax, "Industry relative strength (H1): a small, uneven lead",
          "cumulative net return of the top-3 industries over all 12 equally weighted, 1927–2005 (development), after costs")
    fig.tight_layout()
    p = figs / "3-h1-excess-over-equal-weight.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    return p


def tables(review, dev, ml, hold) -> str:
    L = ["# Phase 3 tables (generated)", "", "Regenerate: `python scripts/research_report_phase3.py research/runs/2026-11-07-phase3 P3_DATA_DIR`", ""]
    L += ["## Factor review (development data; IC = rank correlation with the next period's return)", "",
          "| universe · period | factor | horizon | months/weeks | mean IC | t | top − all per period |", "|---|---|---|---:|---:|---:|---:|"]
    for seg, res in review["industries49"].items():
        for k, v in res.items():
            if "|" in k:
                name, hz = k.split("|")
                L.append(f"| 49 industries · {seg} | {name} | {hz} | {v.get('n')} | {v.get('meanIC', 0):+.3f} | {v.get('tStat', 0):+.2f} | "
                         f"{pct(v.get('quintiles', {}).get('topMinusAllPct'))} |")
    for k, v in review["stocks"].items():
        if "|" in k:
            name, hz = k.split("|")
            L.append(f"| app's 505 stocks · 2022-2026 (survivors) | {name} | {hz} | {v.get('n')} | {v.get('meanIC', 0):+.3f} | "
                     f"{v.get('tStat', 0):+.2f} | {pct(v.get('quintiles', {}).get('topMinusAllPct'))} |")
    lo = review["industries49"]["1926-2005"]["longOnlyTop10_vs_equalWeight"]
    L += ["", f"Long-only top 10 of 49 industries vs equal weight (1926–2005): gross {lo['grossExcessPerYearPct']:+.2f}%/yr, "
              f"turnover {lo['turnoverPerMonth']:.0%}/month, cost {lo['costPerYearPct']:.2f}%/yr, net {lo['netExcessPerYearPct']:+.2f}%/yr.",
          f"alphalens cross-check (12-1 momentum, 21 days, same timing): alphalens IC "
          f"{review['industries49']['1926-2005']['alphalensCheck_mom_12_1_21d']['alphalensMeanIC']:+.4f}, ours "
          f"{review['industries49']['1926-2005']['alphalensCheck_mom_12_1_21d']['oursSameTimingMeanIC']:+.4f}.", ""]
    L += ["## Development verdicts (pre-registered)", "", "| hypothesis | pass | reasons |", "|---|---|---|"]
    for h, v in dev["devVerdicts"].items():
        L.append(f"| {h} | {'yes' if v['pass'] else 'no'} | {'; '.join(v['reasons']) or '—'} |")
    L += ["", f"Trials counted for the deflated Sharpe ratio: {dev['trials']['total']} (review {dev['trials']['review']} + "
              f"{len(dev['trials']['strategyVariants'])} strategy runs).", ""]
    L += ["## Results by period", "",
          "| | period | n | primary (net) | 90% range | p | p (12-mo blocks) | 2× costs | positive decades / 3-yr blocks | top-5 share |",
          "|---|---|---:|---:|---|---:|---:|---:|---|---:|"]
    rows = [("H1 vs equal weight (per month)", dev["H1"]["1926-2005"], "months"), ("H1 vs equal weight (per month)", dev["H1"]["2022-2026"], "months"),
            ("H2 per window (cash 0%)", dev["H2"]["1926-2005"], "windows"), ("H2 per window (cash 0%)", dev["H2"]["2022-2026"], "windows"),
            ("H2 per window (cash 0%) · HOLDOUT A", hold["results"]["P3-H2"], "windows")]
    for lab, r, n in rows:
        pr = r["primary"]
        t5 = "—" if r["top5Share"] is None else f"{r['top5Share']:.2f}"
        L.append(f"| {lab} | {r['from'][:4]}–{r['to'][:4]} | {r[n]} | {pct(pr['meanPct'])} | {pct(pr['ci90Pct'][0], 2)} … {pct(pr['ci90Pct'][1], 2)} | "
                 f"{pr['p']:.3f} | {pr['p12moBlocks']:.3f} | {pct(r['stress2x']['meanPct'])} | {r['decades']['positive']}/{r['decades']['of']} · "
                 f"{r['blocks3y']['positive']}/{r['blocks3y']['of']} | {t5} |")
    L += ["", "## Strategy vs benchmarks (net, $10,000, standard costs)", "",
          "| | period | CAGR | volatility | Sharpe (vs T-bills) | max drawdown | exposure | turnover/yr | worst 2 days |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for lab, r in (("H1", dev["H1"]["1926-2005"]), ("H1", dev["H1"]["2022-2026"])):
        for k, v in r["perf"].items():
            if "days" in v and "cagrPct" in v:
                L.append(f"| {lab}: {k} | {r['from'][:4]}–{r['to'][:4]} | {v['cagrPct']:+.2f}% | {v['volPct']:.1f}% | {v['sharpe']:.2f} | "
                         f"{v['maxDrawdownPct']:.1f}% | {v.get('avgExposure', float('nan')):.2f} | {v.get('turnoverPerYear', float('nan')):.1f} | {v['worst2DayPct']:.1f}% |")
    for lab, r in (("H2", dev["H2"]["1926-2005"]), ("H2", dev["H2"]["2022-2026"]), ("H2 HOLDOUT A", hold["results"]["P3-H2"])):
        for k, v in r["perf"].items():
            if "days" in v and "cagrPct" in v and k != "cash":
                L.append(f"| {lab}: {k} | {r['from'][:4]}–{r['to'][:4]} | {v['cagrPct']:+.2f}% | {v['volPct']:.1f}% | {v['sharpe']:.2f} | "
                         f"{v['maxDrawdownPct']:.1f}% | {v.get('avgExposure', float('nan')):.2f} | {v.get('turnoverPerYear', float('nan')):.1f} | {v['worst2DayPct']:.1f}% |")
    L += ["", "## Capital scenarios (CAGR, cost per year)", "", "| | period | " + " | ".join(dev["H1"]["1926-2005"]["capital"].keys()) + " |",
          "|---|---|" + "---:|" * len(dev["H1"]["1926-2005"]["capital"])]
    for lab, r in (("H1", dev["H1"]["1926-2005"]), ("H2", dev["H2"]["1926-2005"]), ("H2 HOLDOUT A", hold["results"]["P3-H2"])):
        cells = [f"{v['cagrPct']:+.2f}% ({v['costPerYearPct']:.2f}%)" for v in r["capital"].values()]
        L.append(f"| {lab} | {r['from'][:4]}–{r['to'][:4]} | " + " | ".join(cells) + " |")
    L += ["", "## Regimes (primary metric, % per month/window)", "", "| | period | up years | down years | calm months | normal | volatile |", "|---|---|---:|---:|---:|---:|---:|"]
    for lab, r in (("H1", dev["H1"]["1926-2005"]), ("H2", dev["H2"]["1926-2005"]), ("H2 HOLDOUT A", hold["results"]["P3-H2"])):
        g = r["regimes"]
        L.append(f"| {lab} | {r['from'][:4]}–{r['to'][:4]} | {pct(g['up years'])} | {pct(g['down years'])} | {pct(g['calm months'])} | "
                 f"{pct(g['normal months'])} | {pct(g['volatile months'])} |")
    h1 = dev["H1"]["1926-2005"]
    L += ["", "## H1 extras (development)", "",
          f"* Excess over the market (not just equal weight): {pct(h1['excessVsMarket']['meanPct'])}/month, p={h1['excessVsMarket']['p']:.3f}.",
          f"* Random top-3 picks: H1's monthly picks ranked at the {h1['random']['avgPercentile']:.0%} percentile on average "
          f"(gross {pct(h1['random']['h1GrossMonthlyPct'])} vs {pct(h1['random']['randomGrossMonthlyPct'])}/month).",
          "* Sensitivity variants (counted as trials, not chosen): " + "; ".join(f"{k} {pct(v['meanExcessPct'])} (p={v['p']:.3f})" for k, v in h1["variants"].items()),
          f"* Deflated Sharpe: {h1['deflatedSharpe']['dsr']:.3f} (per-month Sharpe {h1['deflatedSharpe']['sr']:.3f} vs threshold {h1['deflatedSharpe']['srStar']:.3f}).", ""]
    h2 = dev["H2"]["1926-2005"]
    ho = hold["results"]["P3-H2"]
    L += ["## H2 extras", "",
          f"* TOM-day vs other-day mean excess: development {h2['tomVsOther']['tomMeanBps']:.1f} vs {h2['tomVsOther']['otherMeanBps']:.1f} bps/day; "
          f"holdout A {ho['tomVsOther']['tomMeanBps']:.2f} vs {ho['tomVsOther']['otherMeanBps']:.2f}; 2022–26 "
          f"{dev['H2']['2022-2026']['tomVsOther']['tomMeanBps']:.1f} vs {dev['H2']['2022-2026']['tomVsOther']['otherMeanBps']:.1f}.",
          f"* Random 4-day windows (gross excess per window): development {h2['random4day']['meanWindowExcessPct']:+.3f}% vs TOM {h2['grossWindowExcessPct']:+.3f}%; "
          f"holdout A {ho['random4day']['meanWindowExcessPct']:+.3f}% vs TOM {ho['grossWindowExcessPct']:+.3f}%.",
          f"* Realistic SPY costs (0.5 bps spread, 1 bp slippage): development {pct(h2['spyRealisticCosts']['meanPct'])}, holdout A "
          f"{pct(ho['spyRealisticCosts']['meanPct'])} (p={ho['spyRealisticCosts']['p']:.3f}).",
          f"* Deflated Sharpe (development): {h2['deflatedSharpe']['dsr']:.3f}.", ""]
    L += ["## Machine learning (H2-ML, development walk-forward 1946–2005)", "",
          f"* Windows tested: {ml['testWindows']}; model held {ml['heldShare']:.0%} of them.",
          f"* Net per month: all windows {pct(ml['netPerMonthPct']['allWindows'])}, model {pct(ml['netPerMonthPct']['mlFiltered'])}; "
          f"improvement p={ml['improvement']['p']:.3f}.",
          f"* Directional skill: AUC {ml['directional']['auc']:.3f}; Brier {ml['directional']['brier']:.4f} vs base rate {ml['directional']['brierBaseRate']:.4f}.",
          f"* Kept: {ml['kept']}.", ""]
    rep = dev["replication"]
    L += ["## Tradable replication on Alpaca ETF bars (2022-11 → 2026-11, price only, $1,000)", "",
          f"* H1 on 11 SPDR sectors from {rep['H1 sectors']['from']}: {rep['H1 sectors']['months']} months, excess over equal weight "
          f"{pct(rep['H1 sectors']['excessVsEW']['meanPct'])}/month (p={rep['H1 sectors']['excessVsEW']['p']:.2f}); CAGR "
          f"{rep['H1 sectors']['perf']['cagrPct']:+.1f}% vs equal weight {rep['H1 sectors']['equalWeight']['cagrPct']:+.1f}% vs SPY {rep['H1 sectors']['SPY']['cagrPct']:+.1f}%.",
          f"* H2 on SPY: {rep['H2 SPY']['windows']} windows, {pct(rep['H2 SPY']['meanNetWindowPct'])}/window (p={rep['H2 SPY']['boot']['p']:.2f}); "
          f"CAGR {rep['H2 SPY']['perf']['cagrPct']:+.1f}% vs SPY {rep['H2 SPY']['SPY']['cagrPct']:+.1f}%.", ""]
    L += ["## Holdout A verdict", ""]
    for h, v in hold["verdicts"].items():
        L.append(f"* **{h}: {v['verdict']}** — " + "; ".join(v["reasons"]))
    return "\n".join(L) + "\n"


def app_summary(dev, hold) -> dict:
    ho = hold["results"]["P3-H2"]
    h2, h1 = dev["H2"]["1926-2005"], dev["H1"]["1926-2005"]
    rows = [
        {"key": "no_trade", "label": "doing nothing (cash at 0%)", "trades": 0, "netPct": 0.0, "lo": 0.0, "hi": 0.0, "pnl": 0.0, "verdict": "baseline"},
        {"key": "h2_hold", "label": "turn of the month · unseen 2006–2020", "trades": ho["windows"], "netPct": ho["primary"]["meanPct"],
         "lo": ho["primary"]["ci90Pct"][0], "hi": ho["primary"]["ci90Pct"][1], "pnl": ho["capital"]["$100"]["cagrPct"], "verdict": "rejected"},
        {"key": "h2_dev", "label": "turn of the month · 1926–2005 (development)", "trades": h2["windows"], "netPct": h2["primary"]["meanPct"],
         "lo": h2["primary"]["ci90Pct"][0], "hi": h2["primary"]["ci90Pct"][1], "pnl": h2["capital"]["$100"]["cagrPct"], "verdict": "dev only"},
        {"key": "h1_dev", "label": "industry relative strength vs equal weight · 1927–2005", "trades": h1["months"], "netPct": h1["primary"]["meanPct"],
         "lo": h1["primary"]["ci90Pct"][0], "hi": h1["primary"]["ci90Pct"][1],
         "pnl": h1["perf"]["H1"]["cagrPct"] - h1["perf"]["equalWeight"]["cagrPct"], "verdict": "rejected"},
        {"key": "mkt_hold", "label": "owning the market · 2006–2020 (benchmark)", "trades": 0, "netPct": 0.0, "lo": 0.0, "hi": 0.0,
         "pnl": ho["perf"]["market"]["cagrPct"], "verdict": "benchmark"},
    ]
    return {"round": "research phase 3", "holdout": "2006-01-03 → 2020-07-24 (used once)",
            "source": "Ken French daily industry/market returns (survivorship-free, dividends included); costs as the app's",
            "moneyLabel": "$/yr", "rows": rows,
            "conclusion": "No multi-day rule passed: the turn-of-the-month effect worked for 80 years and then disappeared, "
                          "and industry relative strength was too weak to tell apart from luck.",
            "note": (f"holding for weeks instead of minutes cuts the cost of trading from about 34–40% a year to under 2%, so costs stop "
                     f"being the problem; what's missing is an edge that survives on data it hasn't seen. the turn of the month earned "
                     f"{h2['primary']['meanPct']:+.2f}% per monthly window in 1926–2005 and {ho['primary']['meanPct']:+.2f}% in 2006–2020. "
                     f"owning the market itself returned {ho['perf']['market']['cagrPct']:.1f}% a year in 2006–2020, with a "
                     f"{ho['perf']['market']['maxDrawdownPct']:.0f}% worst drop. '$/yr' is dollars per year on a $100 account "
                     f"(industry row: per year above the equal-weight industries)."),
            "trend": "simulation only: both rules hold positions overnight, which the app does not do. both stay in the forward "
                     "shadow record (above) so you can see whether anything changes — no orders, no backfilled decisions."}


def main(run: Path, data_dir: Path) -> int:
    review = json.loads((run / "review.json").read_text())
    dev = json.loads((run / "dev.json").read_text())
    ml = json.loads((run / "dev_ml.json").read_text())
    hold = json.loads((run / "holdout.json").read_text())
    figs = run / "figures"
    figs.mkdir(exist_ok=True)
    for p in (chart_tom(review, dev, hold, figs), chart_costs(review, figs), chart_h1(data_dir, figs)):
        print("wrote", p)
    (run / "tables.md").write_text(tables(review, dev, ml, hold))
    dst = ROOT / "stockpilot" / "assets" / "research" / "phase3-summary.json"
    dst.write_text(json.dumps(app_summary(dev, hold), indent=1))
    print("wrote", run / "tables.md", dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
