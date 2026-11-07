"""Render phase 2 figures, tables and the app's evidence summary from the saved JSON results.

    python scripts/research_report_phase2.py research/runs/2026-11-07-phase2 [DEV_CANDIDATES_PKL]

Writes figures/*.png, tables.md (every number the report quotes, regenerated from dev.json + holdout.json)
and stockpilot/assets/research/phase2-summary.json. REPORT.md is written by hand around these tables.
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SURFACE, INK, INK2, GRID, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1", "#a3a29d"
S1, S2 = "#2a78d6", "#eb6834"
ROWS = [("P2-H1", "H1 momentum, held to the close"), ("H1-random", "  baseline: random entry time (biased)"),
        ("P2-H3", "H3 = H1 with a passive limit entry"), ("P2-H2", "H2 gap stocks in play, breakout"),
        ("H2-at-9:35", "  baseline: same stocks bought at 9:35"), ("P2-H4", "H4 index ETFs overnight (research)")]
HYP = ["P2-H1", "P2-H2", "P2-H3", "P2-H4", "P2-H5"]


def style(ax, title, subtitle, grid_axis="x"):
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


def chart_net(dev: dict, hold: dict, figs: Path) -> Path:
    rows = [("doing nothing", 0.0, 0.0, 0.0, None)]
    for k, lab in ROWS:
        e, d = hold["evals"][k], dev["evals"][k]
        rows.append((lab, e["avgRetPct"], e["ci90"][0], e["ci90"][1], d["avgRetPct"]))
    rows = rows[::-1]
    fig, ax = plt.subplots(figsize=(9.6, 5.2), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    for i, (lab, v, lo, hi, dv) in enumerate(rows):
        if lab == "doing nothing":
            ax.scatter([0], [i], s=46, color=INK, zorder=3)
            ax.text(0.02, i, "$0, no risk", va="center", fontsize=9, color=INK2)
            continue
        ax.plot([lo, hi], [i, i], color=S1, linewidth=2, solid_capstyle="round", zorder=2)
        ax.scatter([v], [i], s=50, color=S1, edgecolor=SURFACE, linewidth=2, zorder=3)
        ax.scatter([dv], [i], s=42, facecolor=SURFACE, edgecolor=S2, linewidth=2, zorder=4)
    ax.axvline(0, color=INK2, linewidth=1)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows], fontsize=10, color=INK)
    ax.xaxis.set_major_formatter(lambda x, _: f"{x:+.2f}%")
    style(ax, "Net result per trade after all costs",
          "dot = holdout 2023-09 → 2025-08 (used once) with its 90% range · ring = development data")
    h = [plt.Line2D([], [], marker="o", color=S1, linewidth=2, markersize=7, label="holdout ± 90% range"),
         plt.Line2D([], [], marker="o", linestyle="none", markerfacecolor=SURFACE, markeredgecolor=S2,
                    markeredgewidth=2, markersize=7, label="development")]
    ax.legend(handles=h, loc="upper left", frameon=False, fontsize=9, labelcolor=INK2)
    fig.tight_layout()
    p = figs / "1-net-per-trade.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    return p


def chart_gross_cost(hold: dict, figs: Path) -> Path:
    keys = [k for k, _ in ROWS]
    labs = [lab.strip().replace("baseline: ", "") for _, lab in ROWS]
    gross = [hold["evals"][k]["grossAvgRetPct"] for k in keys]
    cost = [hold["evals"][k]["grossAvgRetPct"] - hold["evals"][k]["avgRetPct"] for k in keys]
    y = np.arange(len(keys))[::-1]
    fig, ax = plt.subplots(figsize=(9.6, 5.1), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    ax.barh(y + 0.19, gross, height=0.34, color=S1, label="gross move captured (before costs)")
    ax.barh(y - 0.19, cost, height=0.34, color=S2, label="cost of the round trip")
    for yi, g, c in zip(y, gross, cost):
        ax.text(max(g, 0) + 0.006, yi + 0.19, pct(g, 2), va="center", fontsize=8.5, color=INK2)
        ax.text(c + 0.006, yi - 0.19, pct(c, 2).lstrip("+"), va="center", fontsize=8.5, color=INK2)
    ax.axvline(0, color=INK2, linewidth=1)
    ax.set_yticks(y, labs, fontsize=10, color=INK)
    ax.xaxis.set_major_formatter(lambda x, _: f"{x:.2f}%")
    style(ax, "Where the money goes (holdout)",
          "per trade: a strategy profits only when its blue bar is longer than its orange bar")
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.08), ncol=2, frameon=False, fontsize=9, labelcolor=INK2)
    fig.tight_layout()
    p = figs / "2-gross-vs-cost.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    return p


def chart_horizon(dev: dict, hold: dict, figs: Path) -> Path:
    hz = ["30", "60", "120", "180", "close"]
    labels = ["30 min", "60 min", "120 min", "180 min", "15:50"]
    fig, ax = plt.subplots(figsize=(9.6, 4.6), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    for k, lab, col in (("P2-H1", "H1 momentum", S1), ("P2-H2", "H2 gap stocks in play", S2)):
        for src, ls, alpha, tag in ((hold, "-", 1.0, "holdout"), (dev, "--", 0.55, "development")):
            v = [src["profiles"][k]["byHorizonNetPct"][h] for h in hz]
            ax.plot(range(len(hz)), v, ls, color=col, alpha=alpha, linewidth=2, marker="o", markersize=6,
                    markeredgecolor=SURFACE, markeredgewidth=1.5, label=f"{lab} · {tag}")
    ax.axhline(0, color=INK2, linewidth=1)
    ax.set_xticks(range(len(hz)), labels, fontsize=10, color=INK)
    ax.yaxis.set_major_formatter(lambda x, _: f"{x:+.2f}%")
    style(ax, "Does holding longer help?",
          "average net per candidate if every position were closed at each time (stops still apply)", grid_axis="y")
    ax.legend(loc="lower right", frameon=False, fontsize=9, labelcolor=INK2, ncol=2)
    fig.tight_layout()
    p = figs / "3-holding-period.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    return p


def filters(pkl: Path) -> list[dict]:
    """Descriptive only (development candidates): the pre-registered deterministic filters."""
    d = pickle.loads(pkl.read_bytes())
    out = []
    rules = [("H1 unfiltered", "h1", lambda c: True),
             ("H1 + relative volume ≥ 2 and SPY above VWAP", "h1",
              lambda c: c.feats.get("rvol", 0) >= 2 and bool(c.feats.get("spy_above_vwap"))),
             ("H2 unfiltered", "h2", lambda c: True),
             ("H2 + rvol5 ≥ 4", "h2", lambda c: c.feats.get("rvol5", 0) >= 4)]
    for lab, key, f in rules:
        cs = [c for c in d[key] if f(c)]
        out.append({"label": lab, "n": len(cs), "grossPct": float(np.mean([c.trade.gross_ret_pct for c in cs])),
                    "netPct": float(np.mean([c.trade.ret_pct for c in cs]))})
    return out


def tables(dev: dict, hold: dict, filt: list[dict] | None) -> str:
    L = ["# Phase 2 tables (generated)", "", "Regenerate: `python scripts/research_report_phase2.py "
         "research/runs/2026-11-07-phase2 DEV_CANDIDATES_PKL`", ""]
    L += ["## Verdicts (pre-registered rules, Holm across H1–H5)", "",
          "| hypothesis | dev trades | dev net/trade | holdout trades | holdout net/trade | holdout gross | 90% range | p | Holm p | verdict |",
          "|---|---:|---:|---:|---:|---:|---|---:|---:|---|"]
    for h in HYP:
        e, d, v = hold["evals"][h], dev["evals"][h], hold["verdicts"][h]
        L.append(f"| {h} | {d['trades']} | {pct(d['avgRetPct'])} | {e['trades']} | {pct(e['avgRetPct'])} | "
                 f"{pct(e.get('grossAvgRetPct')) if h != 'P2-H5' else '—'} | {pct(e['ci90'][0], 2)} … {pct(e['ci90'][1], 2)} | "
                 f"{e['p']:.3f} | {v['holmP']:.3f} | **{v['verdict']}** |")
    L += ["", "Reasons (holdout):", ""]
    for h in HYP:
        L.append(f"* **{h}** — " + "; ".join(hold["verdicts"][h]["reasons"]))
    L += ["", "## Baselines", "", "| baseline | dev trades | dev net | holdout trades | holdout net | holdout gross |",
          "|---|---:|---:|---:|---:|---:|"]
    for k in ("H1-random", "H2-at-9:35"):
        e, d = hold["evals"][k], dev["evals"][k]
        L.append(f"| {k} | {d['trades']} | {pct(d['avgRetPct'])} | {e['trades']} | {pct(e['avgRetPct'])} | {pct(e['grossAvgRetPct'])} |")
    L += ["", "## Robustness (holdout)", "", "| | positive quarters | top-5 share of profit | slippage ×2 | other stress | max drawdown $ | Sharpe |",
          "|---|---:|---:|---:|---|---:|---:|"]
    for h in ("P2-H1", "P2-H2", "P2-H3", "P2-H4", "H2-at-9:35"):
        e = hold["evals"][h]
        other = ""
        if "stressSpread20AvgRetPct" in e:
            other = f"20 bps spread {pct(e['stressSpread20AvgRetPct'])}"
        if "tightCostAvgRetPct" in e:
            other = f"tight costs {pct(e['tightCostAvgRetPct'])}"
        ts = e.get("top5Share")
        sh = e.get("riskAdjusted", {}).get("sharpe")
        L.append(f"| {h} | {e['positiveFolds']}/{e['usedFolds']} | {'—' if ts is None else f'{ts:.2f}'} | "
                 f"{pct(e.get('stress2xAvgRetPct'))} | {other} | {e['maxDrawdown']:.2f} | {'—' if sh is None else f'{sh:.2f}'} |")
    L += ["", "## Opportunity profiles (every candidate, before slot limits)", "",
          "| | period | n | gross | cost | net | avg MFE | avg MAE | reach +0.5% | +1% | +2% | capture | win rate | avg hold |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for h in ("P2-H1", "P2-H3", "P2-H2", "H2-at-9:35"):
        for tag, src in (("dev", dev), ("holdout", hold)):
            p = src["profiles"][h]
            r = p["reach"]
            cap = p.get("captureRatio")
            L.append(f"| {h} | {tag} | {p['n']} | {pct(p['grossPct'])} | {p['costPct']:.3f}% | {pct(p['netPct'])} | "
                     f"{p['avgMfePct']:.2f}% | {p['avgMaePct']:.2f}% | {r['+0.5%']:.0%} | {r['+1.0%']:.0%} | {r['+2.0%']:.0%} | "
                     f"{'—' if cap is None else f'{cap:.2f}'} | {p['winRate']:.0%} | {p['avgHoldMin']:.0f} min |")
    L += ["", "## Holding period (net per candidate if closed at each time)", "",
          "| | period | 30 min | 60 min | 120 min | 180 min | 15:50 |", "|---|---|---:|---:|---:|---:|---:|"]
    for h in ("P2-H1", "P2-H2", "H2-at-9:35"):
        for tag, src in (("dev", dev), ("holdout", hold)):
            z = src["profiles"][h]["byHorizonNetPct"]
            L.append(f"| {h} | {tag} | " + " | ".join(pct(z[k]) for k in ("30", "60", "120", "180", "close")) + " |")
    L += ["", "## Exits", "", "| | period | exit | n | net/trade |", "|---|---|---|---:|---:|"]
    for h in ("P2-H1", "P2-H2"):
        for tag, src in (("dev", dev), ("holdout", hold)):
            for ex, v in src["profiles"][h]["byExit"].items():
                L.append(f"| {h} | {tag} | {ex} | {v['n']} | {pct(v['netPct'])} |")
    L += ["", "## Passive entry (H3): fills and adverse selection", "",
          "| period | signals | filled | fill rate | H1 net when the limit filled | H1 net when it did not |",
          "|---|---:|---:|---:|---:|---:|"]
    for tag, src in (("dev", dev), ("holdout", hold)):
        p, h3 = src["profiles"]["P2-H3"], src["h3"]
        L.append(f"| {tag} | {h3['candidates']} | {h3['filled']} | {p['fillRate']:.1%} | {pct(p['h1NetWhenFilled'])} | "
                 f"{pct(p['h1NetWhenUnfilled'])} |")
    L += ["", "## Regimes (label from SPY's whole day — reporting only, not knowable at entry)", "",
          "| | period | up calm | up volatile | down calm | down volatile |", "|---|---|---|---|---|---|"]
    for h in ("P2-H1", "P2-H2"):
        for tag, src in (("dev", dev), ("holdout", hold)):
            r = src["regimes"][h]
            cell = lambda k: f"{pct(r[k]['netPct'], 2)} (n={r[k]['n']})" if k in r else "—"  # noqa: E731
            L.append(f"| {h} | {tag} | {cell('up calm')} | {cell('up volatile')} | {cell('down calm')} | {cell('down volatile')} |")
    L += ["", "## Overnight (H4) and trend (H5)", "", "| | period | nights/days | gross | net | worst night | 1st pct | nights ≤ −2% |",
          "|---|---|---:|---:|---:|---:|---:|---:|"]
    for tag, src in (("dev", dev), ("holdout", hold)):
        e = src["evals"]["P2-H4"]
        g = e["gapRisk"]
        L.append(f"| H4 | {tag} | {e['trades']} | {pct(e['grossAvgRetPct'])} | {pct(e['avgRetPct'])} | {g['worstPct']:.2f}% | "
                 f"{g['p01Pct']:.2f}% | {g['nightsBelowMinus2Pct']} |")
    L += ["", "| H5 | period | days | strategy total | Sharpe | max drawdown | buy & hold total | Sharpe | max drawdown | switches |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for tag, src in (("dev", dev), ("holdout", hold)):
        e = src["evals"]["P2-H5"]
        s, b = e["strategy"], e["buyHold"]
        L.append(f"| SPY > 100-day SMA | {tag} | {s['days']} | {s['totalPct']:+.1f}% | {s['sharpe']:.2f} | {s['maxDrawdownPct']:.1f}% | "
                 f"{b['totalPct']:+.1f}% | {b['sharpe']:.2f} | {b['maxDrawdownPct']:.1f}% | {e['switches']} |")
    if filt:
        L += ["", "## Deterministic filters (development candidates only — descriptive, not a test)", "",
              "| | n | gross | net |", "|---|---:|---:|---:|"]
        for f in filt:
            L.append(f"| {f['label']} | {f['n']} | {pct(f['grossPct'])} | {pct(f['netPct'])} |")
    L += ["", "## Coverage", "", "| | dev | holdout |", "|---|---:|---:|"]
    for k in ("h1Days", "h2Days", "h4Nights", "h5Days"):
        L.append(f"| {k} | {dev['coverage'][k]} | {hold['coverage'][k]} |")
    return "\n".join(L) + "\n"


def app_summary(dev: dict, hold: dict) -> dict:
    rows = [{"key": "no_trade", "label": "doing nothing", "trades": 0, "netPct": 0.0, "lo": 0.0, "hi": 0.0, "pnl": 0.0,
             "verdict": "baseline"}]
    for k, lab in ROWS:
        e = hold["evals"][k]
        rows.append({"key": k, "label": lab.strip(), "trades": e["trades"], "netPct": e["avgRetPct"],
                     "grossPct": e["grossAvgRetPct"], "lo": e["ci90"][0], "hi": e["ci90"][1], "pnl": e["totalPnl"],
                     "devTrades": dev["evals"][k]["trades"], "devNetPct": dev["evals"][k]["avgRetPct"],
                     "verdict": hold["verdicts"].get(k, {}).get("verdict", "baseline")})
    passed = [h for h in HYP if hold["verdicts"][h]["verdict"] == "eligible_for_forward_test"]
    ev, pr = hold["evals"], hold["profiles"]
    h2c = ev["P2-H2"]["grossAvgRetPct"] - ev["P2-H2"]["avgRetPct"]
    h3 = pr["P2-H3"]
    note = (f"gap stocks in play moved the most (+{pr['P2-H2']['avgMfePct']:.1f}% at best on average) but kept only "
            f"{ev['P2-H2']['grossAvgRetPct']:+.2f}% per trade before costs, and a $20 round trip in them costs about "
            f"{h2c:.2f}% (wider spreads plus fees rounded up to a cent each day). holding momentum signals to the close "
            f"did not help ({ev['P2-H1']['grossAvgRetPct']:+.2f}% before costs). a passive limit entry filled "
            f"{h3['fillRate']:.0%} of the time — mostly on trades that went on to lose "
            f"({h3['h1NetWhenFilled']:+.2f}% when filled vs {h3['h1NetWhenUnfilled']:+.2f}% when not).")
    t = ev["P2-H5"]
    trend = (f"research only (the app never holds overnight): an SPY trend filter returned {t['strategy']['totalPct']:+.0f}% "
             f"vs {t['buyHold']['totalPct']:+.0f}% buy-and-hold, and its worst drop was {t['strategy']['maxDrawdownPct']:.0f}% "
             f"instead of {t['buyHold']['maxDrawdownPct']:.0f}%, but after testing five ideas it can't be told apart from "
             f"luck (adjusted p={hold['verdicts']['P2-H5']['holmP']:.2f}). holding index ETFs overnight lost "
             f"{ev['P2-H4']['avgRetPct']:+.2f}% per night after costs; its worst night was {ev['P2-H4']['gapRisk']['worstPct']:.1f}%.")
    return {"round": "research phase 2", "holdout": "2023-09-01 → 2025-08-29 (used once)",
            "development": "2025-09-02 → 2026-11-05",
            "source": "Alpaca IEX bars; $20 positions, 2 slots, after spread, slippage and fees (daily cent rounding)",
            "rows": rows, "passed": passed, "note": note, "trend": trend,
            "conclusion": ("No phase 2 strategy passed: every one lost money after costs on the unseen holdout, "
                           "or could not be told apart from luck." if not passed else
                           f"{', '.join(passed)} passed the historical test; it is only eligible for forward observation.")}


def main(run: Path, pkl: Path | None) -> int:
    dev = json.loads((run / "dev.json").read_text())
    hold = json.loads((run / "holdout.json").read_text())
    figs = run / "figures"
    figs.mkdir(exist_ok=True)
    for p in (chart_net(dev, hold, figs), chart_gross_cost(hold, figs), chart_horizon(dev, hold, figs)):
        print("wrote", p)
    filt = filters(pkl) if pkl and pkl.exists() else None
    if filt:
        (run / "dev_filters.json").write_text(json.dumps(filt, indent=1))
    elif (run / "dev_filters.json").exists():
        filt = json.loads((run / "dev_filters.json").read_text())
    (run / "tables.md").write_text(tables(dev, hold, filt))
    print("wrote", run / "tables.md")
    dst = ROOT / "stockpilot" / "assets" / "research"
    (dst / "phase2-summary.json").write_text(json.dumps(app_summary(dev, hold), indent=1))
    print("wrote", dst / "phase2-summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None))
