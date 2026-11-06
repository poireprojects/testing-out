"""Real-history evaluation: every strategy after realistic costs, and the AI decision modes vs rules-only.

    python scripts/real_history_report.py --days 40 --symbols 40 --out results/ [--model path/to/model.gguf]

* Market data: Alpaca's free IEX 1-minute bars (paper keys from ALPACA_PAPER_KEY / ALPACA_PAPER_SECRET).
  Never places an order. Missing data is reported and skipped, never filled in.
* AI: a local model through llama.cpp (pip install llama-cpp-python) answering through the app's own
  AIEngine — the same prompts, schema and approval rule as live. Costs $0. Without --model, only the
  rules/ranker part runs.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dkapp.ai.providers import AIUnavailable, ProviderResult  # noqa: E402
from dkapp.backtest.engine import BTConfig, run_backtest, run_day  # noqa: E402
from dkapp.backtest.runner import DEFAULT_BACKTEST_SYMBOLS, HistoryCache, historical_days  # noqa: E402
from dkapp.clock import ET, MarketCalendar  # noqa: E402
from dkapp.strategies import ALL_STRATEGIES  # noqa: E402

log = logging.getLogger("real_history")


class LlamaCppProvider:
    """Local model via llama.cpp, used exactly like the app's Ollama provider (JSON-schema constrained)."""
    name = "llama.cpp (local)"
    is_paid = False

    def __init__(self, model_path: str, threads: int = 4, ctx: int = 4096, seed: int = 7):
        from llama_cpp import Llama
        self.model = Path(model_path).stem
        self.llm = Llama(model_path=model_path, n_ctx=ctx, n_threads=threads, verbose=False, seed=seed)

    def health(self):
        return True, "ok"

    def complete_json(self, system, user, schema, max_tokens=700):
        t0 = time.monotonic()
        try:
            r = self.llm.create_chat_completion(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object", "schema": schema}, temperature=0.2, max_tokens=max_tokens)
        except Exception as exc:  # context overflow etc. -> treated like an offline AI: no trade
            raise AIUnavailable(f"local model error: {exc}") from exc
        u = r.get("usage", {})
        return ProviderResult(r["choices"][0]["message"]["content"], u.get("prompt_tokens", 0),
                              u.get("completion_tokens", 0), 0.0, int((time.monotonic() - t0) * 1000), self.name,
                              self.model)


def pct(x, nd=3):
    return "—" if x is None else f"{x:+.{nd}f}%"


def gross_stats(days, cfg, strategies) -> dict:
    """Average return per trade BEFORE costs (from the same simulated fills) — shows whether a strategy
    has any signal at all, separately from what trading costs take."""
    rng = random.Random(cfg.seed)
    trades = [t for d in sorted(days, key=lambda d: d.day) for t in run_day(d, strategies, cfg, rng)]
    out = {}
    for sid in strategies + ["random"]:
        ts = [t for t in trades if (t.random_baseline if sid == "random" else t.strategy == sid and not t.random_baseline)]
        if ts:
            out[sid] = {"n": len(ts), "grossAvgPct": sum((t.ref_exit / t.ref_entry - 1) * 100 for t in ts) / len(ts),
                        "netAvgPct": sum(t.ret_pct for t in ts) / len(ts),
                        "strategyExitShare": sum(1 for t in ts if t.reason.startswith("strategy")) / len(ts)}
    return out


def markdown(res: dict, cmp: dict | None, meta: dict, gross: dict | None = None) -> str:
    L = [f"# Real-history backtest — {meta['first']} → {meta['last']}", "",
         f"{res['days']} trading days · {meta['symbols']} symbols · Alpaca IEX 1-minute bars · "
         f"${res['config']['notional']:.0f} positions · spread {res['config']['spreadBps']} bps + slippage "
         f"{res['config']['slippageBps']} bps each way + SEC/FINRA fees. Generated {meta['generated']}.", "",
         f"Data coverage: {res['coverage']['symbolDaysWithData']} of {res['coverage']['symbolDaysExpected']} symbol-days "
         f"(missing ones skipped, never filled in). Splits: train {' → '.join(res['splits']['train'])}, validation "
         f"{' → '.join(res['splits']['validation'])}, unseen test {' → '.join(res['splits']['test'])}.", "",
         "## Each strategy, after costs (average return per trade)", "",
         "| strategy | test trades | train | validation | unseen test | random entries | 2× slippage | stable periods | adj. p | verdict |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for sid, r in res["strategies"].items():
        folds = r["walkForward"]
        used = [f for f in folds if f["trades"]]
        good = [f for f in used if f["avgRetPct"] > 0]
        L.append(f"| {r['name']} | {r['test']['trades']} | {pct(r['train']['avgRetPct'])} | {pct(r['validation']['avgRetPct'])} | "
                 f"{pct(r['test']['avgRetPct'])} | {pct(r['randomBaseline']['avgRetPct'])} | {pct(r['stressAvgRetPct'])} | "
                 f"{len(good)}/{len(used)} | {r['pAdjusted']:.2f} | **{r['verdict']}** |")
    if gross:
        c = BTConfig().costs
        rt = c.round_trip_cost_pct(res["config"]["notional"], 100.0, res["config"]["spreadBps"])
        L += ["", "### Before vs after costs (all 40 days)", "",
              f"A ${res['config']['notional']:.0f} round trip costs about **{rt:.2f}%**: half the spread each side, "
              f"{c.slippage_bps:.0f} bps slippage each side, and SEC/FINRA fees rounded up to the cent "
              f"(${c.sell_fees(res['config']['notional'], res['config']['notional'] / 100):.2f} per sale — a big share of a small trade).", "",
              "| strategy | trades | before costs | after costs | closed by its own exit rule |", "|---|---|---|---|---|"]
        for sid, g in gross.items():
            name = res["strategies"][sid]["name"] if sid in res["strategies"] else "random entries (same stops/targets)"
            L.append(f"| {name} | {g['n']} | {pct(g['grossAvgPct'])} | {pct(g['netAvgPct'])} | "
                     f"{g['strategyExitShare']:.0%} |")
    L += ["", "Why:", ""]
    for sid, r in res["strategies"].items():
        L.append(f"* **{r['name']}** — {r['explanation']}")
    L += ["", "## The whole app on the unseen test period (2 positions, $20 each)", "",
          "| approach | trades | win rate | avg/trade | total $ | worst drop $ |", "|---|---|---|---|---|---|"]
    for k, a in res["approaches"].items():
        L.append(f"| {a['label']} | {a['trades']} | {a['winRate']:.0%} | {pct(a['avgRetPct'])} | {a['totalPnl']:+.2f} | "
                 f"{a['maxDrawdown']:.2f} |")
    b = res["benchmarks"]
    L += ["", f"Benchmarks over the test days: SPY buy & hold {pct(b['spyBuyHoldPct'], 2)}, SPY open→close average "
              f"{pct(b['spyOpenToCloseAvgPct'])} per day, doing nothing $0.00.", ""]
    if cmp:
        L += ["## AI decision modes vs rules-only (same candidates, same slots, same costs)", "",
              f"Model: {cmp['ai']['model']} via {cmp['ai']['provider']} · cost $0.00 · prompts anonymised (no tickers, "
              "no dates, prices rescaled) so the model can't recall what happened.", "",
              "| mode | trades | win rate | avg/trade | total $ | worst drop $ |", "|---|---|---|---|---|---|"]
        names = {"rules_ranker": "rules + ranker (no AI)", "ai_confirm": "ai confirms each pick", "ai_select": "ai picks one of the top 3 (or none)"}
        for k, m in cmp["modes"].items():
            L.append(f"| {names[k]} | {m['trades']} | {m['winRate']:.0%} | {pct(m['avgRetPct'])} | {m['totalPnl']:+.2f} | "
                     f"{m['maxDrawdown']:.2f} |")
        f = cmp["filter"]
        c, s = cmp["ai"]["confirm"], cmp["ai"]["select"]
        L += ["", f"Does its yes/no separate good setups from bad ones? Asked about {f['asked']} candidates (every ranked pick "
                  f"plus a random sample): it approved {f['approved']} (avg {pct(f['approvedAvgRetPct'])} per trade after costs) "
                  f"and turned down {f['skipped']} (avg {pct(f['skippedAvgRetPct'])}). All asked: {pct(f['allAvgRetPct'])}; "
                  f"every rules signal: {pct(f['everySignalAvgRetPct'])}.", "",
              f"AI calls: confirm {c['calls']} ({c['invalid']} unusable answers, {c['avgSeconds']:.0f} s each on this "
              f"4-core machine) · pick {s['calls']} ({s['invalid']} unusable, {s['avgSeconds']:.0f} s each).", ""]
    L += ["## Caveats", "",
          "* Past results don't predict future ones; the test period is short. Every verdict uses the same gate the app "
          "uses before it would allow real money.",
          "* IEX is a single exchange (a few % of U.S. volume): volumes are relative, and fills in the backtest assume the "
          "next bar's open plus spread and slippage.",
          "* The AI result is for this one local model on this one period. A larger or cloud model could behave "
          "differently; the app keeps measuring it going forward with shadow trades.", ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=40)
    ap.add_argument("--symbols", type=int, default=40)
    ap.add_argument("--out", default=str(ROOT / "results"))
    ap.add_argument("--model", default="")
    ap.add_argument("--ai-sample", type=int, default=100)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--render-only", action="store_true", help="rebuild REPORT.md from saved results + cached bars")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from dkapp.credentials import AlpacaKeys
    from dkapp.data.alpaca_data import AlpacaMarketData
    keys = AlpacaKeys(os.environ["ALPACA_PAPER_KEY"], os.environ["ALPACA_PAPER_SECRET"])
    data = AlpacaMarketData(keys, feed="iex")
    cal = MarketCalendar()
    today = datetime.now(timezone.utc).astimezone(ET).date()
    end = today
    while True:  # last fully finished session before today (never includes today)
        from datetime import timedelta
        end = end - timedelta(days=1)
        if cal.session(end) is not None:
            break
    symbols = DEFAULT_BACKTEST_SYMBOLS[: a.symbols]
    if a.render_only:
        res = json.loads((out / "backtest.json").read_text())
        cmp = json.loads((out / "ai_modes.json").read_text()) if (out / "ai_modes.json").exists() else None

        class CacheOnly:
            def get_intraday_bars(self, *args, **kw):
                raise RuntimeError("render-only uses cached bars")
        from datetime import date as _date
        days = historical_days(CacheOnly(), cal, symbols, _date.fromisoformat(res["lastDay"]), res["days"],
                               cache=HistoryCache(out / "cache"))
        meta = {"first": res["firstDay"], "last": res["lastDay"], "symbols": len(symbols),
                "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
        (out / "REPORT.md").write_text(markdown(res, cmp, meta, gross_stats(days, BTConfig(), list(ALL_STRATEGIES))))
        log.info("wrote %s", out / "REPORT.md")
        return 0
    days = historical_days(data, cal, symbols, end, a.days, cache=HistoryCache(out / "cache"),
                           progress=lambda f, m: log.info("%3.0f%% %s", f * 100, m))
    cfg = BTConfig()
    strategies = list(ALL_STRATEGIES)
    res = run_backtest(days, strategies, cfg, synthetic=False, expected_symbols=symbols)
    meta = {"first": res["firstDay"], "last": res["lastDay"], "symbols": len(symbols),
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    (out / "backtest.json").write_text(json.dumps(res, indent=1, default=float))
    log.info("backtest done: %s", {k: v["verdict"] for k, v in res["strategies"].items()})
    cmp = None
    if a.model:
        from dkapp.ai.engine import AIEngine
        from dkapp.backtest.ai_replay import compare_modes
        from dkapp.config import AppSettings
        from dkapp.storage.db import Database
        s = AppSettings()
        s.ai.decision_mode = "ai_confirm"
        db = Database(out / "ai_replay.db", "paper")
        ai = AIEngine(s, db, LlamaCppProvider(a.model, threads=a.threads))
        rng = random.Random(cfg.seed)
        trades = [t for d in sorted(days, key=lambda d: d.day) for t in run_day(d, strategies, cfg, rng)]
        cmp = compare_modes(days, trades, cfg, ai, sample_for_filter=a.ai_sample, progress=log.info)
        (out / "ai_modes.json").write_text(json.dumps(cmp, indent=1, default=float))
    (out / "REPORT.md").write_text(markdown(res, cmp, meta, gross_stats(days, cfg, strategies)))
    log.info("wrote %s", out / "REPORT.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
