"""Check that the frozen build 20 study still reproduces exactly from cached bars.

    python scripts/reproduce_baseline.py REALHIST_DIR

Re-runs the backtest with BTConfig.baseline_build20() and compares every strategy's numbers and the
test-period approaches with docs/results/2026-11-06/backtest.json. Exit code 0 only if all match.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dkapp.backtest.engine import BTConfig, run_backtest  # noqa: E402
from dkapp.backtest.runner import DEFAULT_BACKTEST_SYMBOLS  # noqa: E402
from dkapp.research.data import load_cached_days  # noqa: E402
from dkapp.strategies import ALL_STRATEGIES  # noqa: E402


def main(src: Path) -> int:
    ref = json.loads((ROOT / "docs/results/2026-11-06/backtest.json").read_text())
    days = load_cached_days(src / "cache", date.fromisoformat(ref["lastDay"]), ref["days"])
    res = run_backtest(days, list(ALL_STRATEGIES), BTConfig.baseline_build20(), synthetic=False,
                       expected_symbols=DEFAULT_BACKTEST_SYMBOLS[:40])
    bad = []

    def cmp(path, a, b):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9):
                bad.append(f"{path}: {a} != {b}")

    for sid, r in ref["strategies"].items():
        for part in ("train", "validation", "test", "all", "randomBaseline"):
            for k in ("trades", "avgRetPct", "totalPnl", "winRate"):
                cmp(f"{sid}.{part}.{k}", res["strategies"][sid][part][k], r[part][k])
        cmp(f"{sid}.stress", res["strategies"][sid]["stressAvgRetPct"], r["stressAvgRetPct"])
        if res["strategies"][sid]["verdict"] != r["verdict"]:
            bad.append(f"{sid}.verdict")
    for k, v in ref["approaches"].items():
        for kk in ("trades", "totalPnl", "avgRetPct"):
            cmp(f"approach.{k}.{kk}", res["approaches"][k][kk], v[kk])
    if bad:
        print("MISMATCH\n" + "\n".join(bad[:40]))
        return 1
    print(f"build 20 baseline reproduced exactly: {len(ref['strategies'])} strategies x 5 parts, "
          f"{len(ref['approaches'])} approaches, {ref['days']} days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
