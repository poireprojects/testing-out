"""PNG charts for the real-history report (python scripts/report_charts.py RESULTS_DIR)."""
from __future__ import annotations

import json
import random
import sys
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
S1, S2 = "#2a78d6", "#eb6834"  # categorical slots 1-2 (validated reference palette)


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


def label(ax, bar, value, fmt):
    x = bar.get_width()
    ax.text(x + (0.004 if x >= 0 else -0.004), bar.get_y() + bar.get_height() / 2, fmt(value), va="center",
            ha="left" if x >= 0 else "right", fontsize=9, color=INK)


def gross_net(out: Path) -> dict:
    from dkapp.backtest.engine import BTConfig, run_day
    from dkapp.backtest.runner import DEFAULT_BACKTEST_SYMBOLS, HistoryCache, historical_days
    from dkapp.clock import MarketCalendar
    from dkapp.strategies import ALL_STRATEGIES
    res = json.loads((out / "backtest.json").read_text())

    class CacheOnly:
        def get_intraday_bars(self, *a, **k):
            raise RuntimeError("cache only")
    days = historical_days(CacheOnly(), MarketCalendar(), DEFAULT_BACKTEST_SYMBOLS[:40], date.fromisoformat(res["lastDay"]),
                           res["days"], cache=HistoryCache(out / "cache"))
    cfg = BTConfig()
    rng = random.Random(cfg.seed)
    trades = [t for d in sorted(days, key=lambda d: d.day) for t in run_day(d, list(ALL_STRATEGIES), cfg, rng)]
    rows = []
    for sid, strat in ALL_STRATEGIES.items():
        ts = [t for t in trades if t.strategy == sid and not t.random_baseline]
        rows.append((strat.name, sum((t.ref_exit / t.ref_entry - 1) * 100 for t in ts) / len(ts),
                     sum(t.ret_pct for t in ts) / len(ts), len(ts)))
    return {"rows": rows, "res": res}


def chart_costs(out: Path, figs: Path) -> Path:
    d = gross_net(out)
    rows = d["rows"][::-1]
    fig, ax = plt.subplots(figsize=(9, 5.2), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    y = range(len(rows))
    h = 0.34
    b1 = ax.barh([i + h / 2 + 0.02 for i in y], [r[1] for r in rows], height=h, color=S1, label="before costs")
    b2 = ax.barh([i - h / 2 - 0.02 for i in y], [r[2] for r in rows], height=h, color=S2, label="after costs ($20 trade)")
    for bars, vals in ((b1, [r[1] for r in rows]), (b2, [r[2] for r in rows])):
        for bar, v in zip(bars, vals):
            label(ax, bar, v, lambda v: f"{v:+.3f}%")
    ax.set_yticks(list(y), [f"{r[0]}\n{r[3]} trades" for r in rows], fontsize=10, color=INK)
    ax.axvline(0, color=INK2, linewidth=1)
    ax.set_xlim(-0.36, 0.08)
    ax.xaxis.set_major_locator(matplotlib.ticker.MultipleLocator(0.1))
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: "0%" if abs(v) < 1e-9 else f"{v:+.1f}%"))
    res = d["res"]
    style(ax, "Average return per trade, real history",
          f"Alpaca IEX 1-minute bars, {res['firstDay']} → {res['lastDay']} ({res['days']} trading days, 40 symbols)")
    ax.legend(loc="lower left", frameon=False, fontsize=9, labelcolor=INK)
    fig.tight_layout()
    p = figs / "1-returns-before-after-costs.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    return p


def money(v: float) -> str:
    return f"{'−' if v < -1e-9 else '+'}${abs(v):.2f}"


def chart_approaches(out: Path, figs: Path) -> Path:
    res = json.loads((out / "backtest.json").read_text())
    ap = res["approaches"]
    # only what a $100 account could actually have done (2 positions of $20)
    items = [("rules, first come first served", ap["first_come"]["totalPnl"], ap["first_come"]["trades"]),
             ("rules + ranker (what the app does)", ap["ranked"]["totalPnl"], ap["ranked"]["trades"])]
    b = res["benchmarks"]
    items.append(("buying SPY with the $100 and holding", (b["spyBuyHoldPct"] or 0) / 100 * 100, 1))
    items.append(("doing nothing", 0.0, 0))
    items = items[::-1]
    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    bars = ax.barh(range(len(items)), [i[1] for i in items], height=0.5, color=S1)
    for bar, it in zip(bars, items):
        label(ax, bar, it[1], money)
    ax.set_yticks(range(len(items)), [f"{i[0]}" + (f"\n{i[2]} trades" if i[2] > 1 else "") for i in items],
                  fontsize=10, color=INK)
    ax.axvline(0, color=INK2, linewidth=1)
    lo = min(i[1] for i in items)
    ax.set_xlim(lo * 1.3, max(2.0, max(i[1] for i in items) * 1.3))
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: money(v)[:-3]))
    style(ax, "Dollars made on the unseen test period, $100 account",
          f"{res['splits']['test'][0]} → {res['splits']['test'][1]} · after spread, slippage and fees")
    fig.tight_layout()
    p = figs / "2-approaches-test-period.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    return p


def chart_ai(out: Path, figs: Path) -> Path | None:
    f = out / "ai_modes.json"
    if not f.exists():
        return None
    cmp = json.loads(f.read_text())
    names = {"rules_ranker": "rules + ranker (no AI)", "ai_confirm": "AI confirms each pick",
             "ai_select": "AI picks one of the top 3"}
    items = [(names[k], m["totalPnl"], m["trades"], m["avgRetPct"]) for k, m in cmp["modes"].items()][::-1]
    fig, ax = plt.subplots(figsize=(9, 3.8), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    bars = ax.barh(range(len(items)), [i[1] for i in items], height=0.5, color=S1)
    for bar, it in zip(bars, items):
        label(ax, bar, it[1], lambda v, it=it: f"{money(v)}  ({it[3]:+.3f}% per trade)")
    ax.set_yticks(range(len(items)), [f"{i[0]}\n{i[2]} trades" for i in items], fontsize=10, color=INK)
    ax.axvline(0, color=INK2, linewidth=1)
    lo = min(i[1] for i in items + [("", 0, 0, 0)])
    ax.set_xlim(lo * 1.6 - 0.5, 1.0)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: money(v)[:-3]))
    flt = cmp["filter"]
    style(ax, "AI decision modes vs rules-only, same candidates and costs",
          f"local {cmp['ai']['model']} (free) · all {cmp['days']} days · AI approved {flt['approved']} of {flt['asked']} setups it was asked about")
    fig.tight_layout()
    p = figs / "3-ai-modes.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    return p


if __name__ == "__main__":
    out = Path(sys.argv[1])
    figs = out / "figures"
    figs.mkdir(exist_ok=True)
    for fn in (chart_costs, chart_approaches, chart_ai):
        p = fn(out, figs)
        if p:
            print("wrote", p)
