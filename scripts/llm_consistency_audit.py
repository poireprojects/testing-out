"""Audit the recorded build 20 language-model answers against the app's own numbers.

    python scripts/llm_consistency_audit.py REALHIST_DIR OUT_JSON
"""
from __future__ import annotations

import json
import random
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dkapp.ai.consistency import Facts, check_explanation  # noqa: E402
from dkapp.backtest.ai_replay import PromptBuilder  # noqa: E402
from dkapp.backtest.engine import BTConfig, run_day  # noqa: E402
from dkapp.research.data import load_cached_days  # noqa: E402
from dkapp.strategies import ALL_STRATEGIES  # noqa: E402


def main(src: Path, out: Path) -> int:
    ref = json.loads((ROOT / "docs/results/2026-11-06/ai_modes.json").read_text())
    rows = [json.loads(l) for l in (ROOT / "research/baselines/build20/ai_decisions.jsonl").read_text().splitlines()]
    entries = [r for r in rows if r["kind"] == "entry"]
    log = [x for x in ref["log"] if x["mode"] == "confirm"]
    cfg = BTConfig.baseline_build20()
    days = load_cached_days(src / "cache", date(2026, 11, 5), 40)
    rng = random.Random(cfg.seed)
    trades = {(str(t.day), t.symbol, t.strategy, t.entry_i): t for d in days
              for t in run_day(d, list(ALL_STRATEGIES), cfg, rng) if not t.random_baseline}
    pb = PromptBuilder(days, cfg)
    # recreate aliases in the original order (aliases are assigned on first use)
    results, conf = [], []
    for x, r in zip(log, entries):
        t = trades[tuple(x["trade"])]
        sig, ctx = pb.build(t)
        f = Facts.from_signal(sig, ctx["round_trip_cost_pct"])
        chk = check_explanation(r["reasoning"] or "", f)
        conf.append(r["confidence"] or 0)
        results.append({"trade": x["trade"], "confidence": r["confidence"], "latencyS": r["latency_ms"] / 1000,
                        "problems": chk.problems, "reasoning": (r["reasoning"] or "")[:400],
                        "facts": {"rsi": f.rsi, "stopPct": f.stop_pct, "targetPct": f.target_pct, "rr": f.reward_risk}})
    bad = [r for r in results if r["problems"]]
    lat = sorted(r["latencyS"] for r in results)
    summary = {"answers": len(results), "inconsistent": len(bad), "inconsistentShare": len(bad) / max(1, len(results)),
               "approved": sum(1 for r in entries if r["action"] == "buy"),
               "avgConfidence": sum(conf) / max(1, len(conf)), "medianSeconds": lat[len(lat) // 2] if lat else 0,
               "problemKinds": {}}
    for r in bad:
        for p in r["problems"]:
            k = p.split(" but ")[0][:18]
            summary["problemKinds"][k] = summary["problemKinds"].get(k, 0) + 1
    out.write_text(json.dumps({"summary": summary, "answers": results}, indent=1))
    print(json.dumps(summary, indent=1))
    for r in bad[:6]:
        print("-", r["problems"], "|", r["reasoning"][:160])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
