"""Train the statistical strategy-filter model that ships with the app (SHADOW stage: it records, never trades).

    python scripts/train_shadow_model.py CACHE_DIR

Trained on every available day (the round-1 holdout has been used, so it is ordinary data now). Its card
carries the round-1 development and holdout evidence. It is NOT paper-eligible: on the holdout it found
no candidate with a positive expected result after costs.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402

import research_round1 as r1  # noqa: E402
from dkapp.quant.features import FEATURE_VERSION  # noqa: E402
from dkapp.quant.model import QuantModel  # noqa: E402
from dkapp.quant.registry import ModelCard  # noqa: E402


def main(cache: Path) -> int:
    ranges = [r1.DEV_RANGES[0], r1.HOLDOUT, r1.DEV_RANGES[1]]
    data = r1.collect(cache, ranges, "development")
    X, y, d = r1.arrays(data["strat"])
    g = np.array([x.toordinal() for x in d])
    m = QuantModel(r1.S_NAMES).fit(X, y, groups=g)
    m.train_days = [str(min(d)), str(max(d))]
    m.label = "net result of the strategy's own trade after costs (% of position)"
    dev = json.loads((ROOT / "research/runs/2026-11-06-round1/dev.json").read_text())
    hold = json.loads((ROOT / "research/runs/2026-11-06-round1/holdout.json").read_text())

    def ev(res, k):
        e = res["evals"][k]
        return {"trades": e["trades"], "netPct": round(e["avgRetPct"], 4), "grossPct": round(e["grossAvgRetPct"], 4)}
    mm = hold["models"]["h5"]
    card = ModelCard(
        id="strategy-filter-v1", kind="strategy_filter", feature_version=FEATURE_VERSION, stage="shadow",
        trained_on=m.train_days,
        evidence={"holdout": {"days": "2026-05-01..2026-08-26", "candidates": mm["n"], "auc": round(mm["auc"], 3),
                              "brier": round(mm["brier"], 4), "brierBaseRate": round(mm["brierClimatology"], 4),
                              "ece": round(mm["calibration"]["ece"], 4),
                              "expectedNetSkillAuc": round(mm["aucExpNet"], 3),
                              "tradesTaken": hold["evals"]["h5"]["trades"],
                              "rulesSameDays": ev(hold, "h5_rules_same_days"),
                              "verdict": hold["verdicts"]["h5"]["verdict"]},
                  "development": {"days": "2025-09-16..2026-04-30 + 2026-09-11..2026-11-05",
                                  "auc": round(dev["models"]["h5"]["auc"], 3), "tradesTaken": dev["evals"]["h5"]["trades"],
                                  "verdict": dev["verdicts"]["h5"]["verdict"]}},
        decision_rule="take only if expected net > 0 and P(win) > 0.5 (pre-registered)",
        paper_eligible=False,
        notes=("Calibrated win probabilities (better than the base rate), but no skill at predicting the size of "
               "the result: on the unseen holdout it found no candidate with a positive expected result after "
               "costs and took no trades. Shown for information; it can't gate trades."))
    out = ROOT / "dkapp" / "assets" / "models"
    out.mkdir(parents=True, exist_ok=True)
    card.fingerprint = m.fingerprint()
    from datetime import datetime, timezone
    card.created = datetime.now(timezone.utc).isoformat()
    (out / f"{card.id}.json").write_text(json.dumps({"card": card.to_dict(), "model": m.to_dict()}))
    print("wrote", out / f"{card.id}.json", "rows", len(y), "fingerprint", card.fingerprint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
