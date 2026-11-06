"""Rebuild the AI-mode comparison from answers a model already gave (no new AI calls).

    python scripts/ai_modes_from_recorded.py RESULTS_DIR

Replays the same days in the same order through the app's AIEngine, but the "model" just returns the
answers recorded in RESULTS_DIR/ai_replay.db in the order they were given. If the recorded "ai picks"
answers run out before the end, that mode is reported only for the days fully covered, and rules-only is
reported for exactly the same days next to it. Writes RESULTS_DIR/ai_modes.json.
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dkapp.ai.engine import AIEngine  # noqa: E402
from dkapp.ai.providers import AIUnavailable, ProviderResult  # noqa: E402
from dkapp.backtest.ai_replay import AIModes, PromptBuilder, _key  # noqa: E402
from dkapp.backtest.engine import BTConfig, replay_portfolio, risk_adjusted, run_day, summarize  # noqa: E402
from dkapp.backtest.runner import DEFAULT_BACKTEST_SYMBOLS, HistoryCache, historical_days  # noqa: E402
from dkapp.clock import MarketCalendar  # noqa: E402
from dkapp.config import AppSettings  # noqa: E402
from dkapp.storage.db import Database  # noqa: E402
from dkapp.strategies import ALL_STRATEGIES  # noqa: E402


class Recorded:
    name = "llama.cpp (local)"
    is_paid = False

    def __init__(self, db_path: Path):
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = con.execute("SELECT kind, raw, input_tokens, output_tokens, latency_ms, model FROM ai_decisions "
                           "ORDER BY id").fetchall()
        self.model = rows[0][5] if rows else "unknown"
        self.queue = {"entry": [r for r in rows if r[0] == "entry"], "select": [r for r in rows if r[0] == "select"]}
        self.latency = {k: [r[4] for r in v] for k, v in self.queue.items()}
        self.exhausted = None

    def health(self):
        return True, "ok"

    def complete_json(self, system, user, schema, max_tokens=700):
        kind = "select" if "choice" in schema.get("properties", {}) else "entry"
        if not self.queue[kind]:
            self.exhausted = self.exhausted or kind
            raise AIUnavailable("not asked (the recorded run was stopped here)")
        _, raw, tin, tout, lat, model = self.queue[kind].pop(0)
        return ProviderResult(raw, tin, tout, 0.0, lat, self.name, model)


def main(out: Path) -> int:
    res = json.loads((out / "backtest.json").read_text())

    class CacheOnly:
        def get_intraday_bars(self, *a, **k):
            raise RuntimeError("cache only")
    days = historical_days(CacheOnly(), MarketCalendar(), DEFAULT_BACKTEST_SYMBOLS[:40], date.fromisoformat(res["lastDay"]),
                           res["days"], cache=HistoryCache(out / "cache"))
    days = sorted(days, key=lambda d: d.day)
    cfg = BTConfig.baseline_build20()  # the frozen build 20 study's exact rules
    rng = random.Random(cfg.seed)
    trades = [t for d in days for t in run_day(d, list(ALL_STRATEGIES), cfg, rng)]
    real = [t for t in trades if not t.random_baseline]
    spy = {d.day: d.spy_trend for d in days}
    prov = Recorded(out / "ai_replay.db")
    s = AppSettings()
    s.ai.decision_mode = "ai_confirm"
    ai = AIEngine(s, Database(Path(tempfile.mkdtemp()) / "replay.db", "paper"), prov)
    modes = AIModes(ai, PromptBuilder(days, cfg))   # same object order as the original run -> same aliases
    ranked = replay_portfolio(real, cfg, True, spy)
    confirm = replay_portfolio(real, cfg, True, spy, gate=modes.confirm)
    cutoff = {"day": None}

    def chooser(day, minute, shortlist, free):
        if prov.exhausted == "select":
            cutoff["day"] = cutoff["day"] or day
            return []
        picks = modes.choose(day, minute, shortlist, free)
        if prov.exhausted == "select":
            cutoff["day"] = cutoff["day"] or day
        return picks
    select = replay_portfolio(real, cfg, True, spy, chooser=chooser)
    if prov.exhausted == "select":  # the placeholder for the stopped run isn't a real (unusable) answer
        modes.stats_select.calls -= 1
        modes.stats_select.invalid -= 1
    day_list = [d.day for d in days]
    covered = [d for d in day_list if cutoff["day"] is None or d < cutoff["day"]]
    cov = set(covered)

    def report(ts, dl):
        sm = summarize(ts)
        return {k: sm[k] for k in ("trades", "winRate", "avgRetPct", "totalPnl", "profitFactor", "maxDrawdown")} | \
            {"riskAdjusted": risk_adjusted(ts, dl)}

    asked = [t for t in real if _key(t) in modes.confirm_cache]
    yes = [t for t in asked if modes.confirm_cache[_key(t)]]
    no = [t for t in asked if not modes.confirm_cache[_key(t)]]
    avg = lambda ts: (sum(t.ret_pct for t in ts) / len(ts)) if ts else 0.0  # noqa: E731
    lat = {k: (sum(v) / len(v) / 1000 if v else 0.0) for k, v in prov.latency.items()}
    out_obj = {
        "days": len(day_list), "candidates": len(real),
        "modes": {"rules_ranker": report(ranked, day_list), "ai_confirm": report(confirm, day_list),
                  "ai_select": report([t for t in select if t.day in cov], covered)},
        "selectCoverage": {"days": len(covered), "first": str(covered[0]) if covered else None,
                           "last": str(covered[-1]) if covered else None,
                           "rulesRankerSameDays": report([t for t in ranked if t.day in cov], covered),
                           "aiConfirmSameDays": report([t for t in confirm if t.day in cov], covered)},
        "filter": {"asked": len(asked), "approved": len(yes), "skipped": len(no),
                   "approvedAvgRetPct": avg(yes), "skippedAvgRetPct": avg(no), "allAvgRetPct": avg(asked),
                   "everySignalAvgRetPct": summarize(real)["avgRetPct"]},
        "ai": {"confirm": modes.stats_confirm.to_dict() | {"avgSeconds": lat["entry"]},
               "select": modes.stats_select.to_dict() | {"avgSeconds": lat["select"]},
               "provider": prov.name, "model": prov.model, "costUsd": 0.0,
               "note": "rebuilt from recorded answers; the run was stopped before the extra random sample"},
        "log": modes.log,
    }
    (out / "ai_modes.json").write_text(json.dumps(out_obj, indent=1, default=float))
    print(json.dumps({k: {kk: out_obj["modes"][k][kk] for kk in ("trades", "avgRetPct", "totalPnl")} for k in out_obj["modes"]}))
    print("select covered", out_obj["selectCoverage"]["days"], "days", out_obj["selectCoverage"]["first"], "->",
          out_obj["selectCoverage"]["last"], "| rules same days:",
          {k: out_obj["selectCoverage"]["rulesRankerSameDays"][k] for k in ("trades", "avgRetPct", "totalPnl")})
    print("filter", out_obj["filter"], "select answers", modes.stats_select.to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
