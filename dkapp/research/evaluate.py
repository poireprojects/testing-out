"""Walk-forward prediction, trade evaluation and the pre-registered go/no-go criteria."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np

from ..backtest.engine import BTConfig, BTTrade, recost, risk_adjusted, summarize, with_daily_rounding
from ..quant.model import QuantModel
from .stats import auc, brier, calibration, day_block_bootstrap, effective_n, log_loss, min_detectable_effect, top_share


# ------------------------------------------------------------------ walk-forward
@dataclass
class WalkForward:
    preds: dict[int, dict]                      # sample index -> {"p_win", "exp_net", "exp_net_sd"}
    test_days: list[date]
    models: list[tuple[date, QuantModel]] = field(default_factory=list)


def walk_forward_predict(X: np.ndarray, ret: np.ndarray, days: list[date], feature_names: list[str],
                         min_train_days: int = 15, embargo: int = 1, lam: float = 1.0, n_boot: int = 20) -> WalkForward:
    """Expanding window by day: the model for day k is trained only on days < k - embargo."""
    uniq = sorted(set(days))
    d_arr = np.array([uniq.index(d) for d in days])
    preds: dict[int, dict] = {}
    models = []
    for k in range(min_train_days + embargo, len(uniq)):
        train = d_arr < k - embargo
        test = d_arr == k
        if train.sum() < 50 or not test.any():
            continue
        m = QuantModel(feature_names, lam).fit(X[train], ret[train], groups=d_arr[train], n_boot=n_boot)
        m.train_days = [str(uniq[0]), str(uniq[k - embargo - 1])]
        p = m.predict(X[test])
        for j, row in enumerate(np.nonzero(test)[0]):
            preds[int(row)] = {kk: float(v[j]) for kk, v in p.items()} | {"base": m.base_rate}
        models.append((uniq[k], m))
    return WalkForward(preds, [d for d in uniq[min_train_days + embargo:]], models)


def model_metrics(preds: dict[int, dict], ret: np.ndarray, train_base_rate: Optional[float] = None) -> dict:
    rows = sorted(preds)
    if not rows:
        return {"n": 0}
    p = np.array([preds[r]["p_win"] for r in rows])
    e = np.array([preds[r]["exp_net"] for r in rows])
    y = (ret[rows] > 0).astype(int)
    actual = ret[rows]
    climo = np.array([preds[r].get("base", train_base_rate or 0.0) for r in rows])  # each fold's training base rate
    corr = float(np.corrcoef(e, actual)[0, 1]) if np.std(e) > 0 and np.std(actual) > 0 else 0.0
    return {"n": len(rows), "baseRate": float(y.mean()), "brier": brier(p, y), "brierClimatology": brier(climo, y),
            "logLoss": log_loss(p, y), "auc": auc(p, y), "aucExpNet": auc(e, y), "corrExpNetActual": corr,
            "calibration": calibration(p, y)}


# ------------------------------------------------------------------ trade evaluation
def evaluate(name: str, trades: list[BTTrade], cfg: BTConfig, day_list: list[date], folds: int = 4,
             stress: Optional[list[float]] = None) -> dict:
    ts = with_daily_rounding(trades, cfg)
    s = summarize(ts)
    rets = [t.ret_pct for t in ts]
    days = [t.day for t in ts]
    bs = day_block_bootstrap(rets, days)
    size = max(1, len(day_list) // folds)
    fold = []
    for k in range(folds):
        chunk = set(day_list[k * size:(k + 1) * size] if k < folds - 1 else day_list[k * size:])
        fr = [t.ret_pct for t in ts if t.day in chunk]
        fold.append({"fold": k + 1, "trades": len(fr), "avgRetPct": float(np.mean(fr)) if fr else 0.0})
    if stress is None:
        stress = [recost(t, cfg, 2.0) for t in trades]
    sd = float(np.std(rets, ddof=1)) if len(rets) > 2 else 0.0
    n_eff = effective_n(rets, days) if rets else 0.0
    return {"name": name, "trades": s["trades"], "winRate": s["winRate"], "avgRetPct": s["avgRetPct"],
            "grossAvgRetPct": float(np.mean([t.gross_ret_pct for t in trades])) if trades else 0.0,
            "totalPnl": s["totalPnl"], "maxDrawdown": s["maxDrawdown"], "profitFactor": s["profitFactor"],
            "ci90": [bs["lo"], bs["hi"]], "p": bs["p"], "tradeDays": bs["days"], "folds": fold,
            "positiveFolds": sum(1 for f in fold if f["trades"] and f["avgRetPct"] > 0),
            "usedFolds": sum(1 for f in fold if f["trades"]),
            "top5Share": top_share([t.pnl for t in ts]), "stress2xAvgRetPct": float(np.mean(stress)) if stress else 0.0,
            "sdPct": sd, "effectiveN": n_eff, "mdePct": min_detectable_effect(sd, n_eff) if sd else None,
            "riskAdjusted": risk_adjusted(ts, day_list)}


# ------------------------------------------------------------------ go / no-go (pre-registered)
MIN_TRADES = 100


def judge(ev: dict, holm_p: Optional[float], best_baseline_avg: Optional[float], model: Optional[dict] = None,
          llm: Optional[dict] = None) -> dict:
    reasons = []
    if ev["trades"] < MIN_TRADES:
        reasons.append(f"only {ev['trades']} trades (need {MIN_TRADES})")
        insufficient = True
    else:
        insufficient = False
    if ev["avgRetPct"] <= 0:
        reasons.append(f"loses {ev['avgRetPct']:+.3f}% per trade after costs")
    if holm_p is not None and holm_p >= 0.05:
        reasons.append(f"could be luck after testing several ideas (Holm-adjusted p={holm_p:.2f})")
    if ev["usedFolds"] and ev["positiveFolds"] / ev["usedFolds"] < 0.6:
        reasons.append(f"profitable in only {ev['positiveFolds']} of {ev['usedFolds']} periods")
    if ev["top5Share"] is not None and ev["top5Share"] > 0.5:
        reasons.append(f"the 5 best trades make {ev['top5Share']:.0%} of the profit")
    if ev["stress2xAvgRetPct"] <= 0:
        reasons.append("negative if slippage doubles")
    if best_baseline_avg is not None and ev["avgRetPct"] <= best_baseline_avg:
        reasons.append(f"not better than the best simple baseline ({best_baseline_avg:+.3f}% per trade)")
    if model:
        if model.get("n", 0) and model["brier"] >= model["brierClimatology"]:
            reasons.append(f"probabilities no better than the base rate (Brier {model['brier']:.4f} vs "
                           f"{model['brierClimatology']:.4f})")
        if model.get("calibration", {}).get("ece", 0) > 0.05:
            reasons.append(f"poorly calibrated (ECE {model['calibration']['ece']:.3f})")
    if llm:
        if llm.get("medianSeconds", 0) > 30:
            reasons.append(f"too slow for intraday use (median {llm['medianSeconds']:.0f} s)")
        if llm.get("inconsistentShare", 0) >= 0.05:
            reasons.append(f"{llm['inconsistentShare']:.0%} of its explanations contradict the numbers")
    if insufficient:
        verdict = "insufficient"          # too few trades to judge either way (other findings still listed)
    elif reasons:
        verdict = "rejected"
    else:
        verdict = "eligible_for_forward_test"
    return {"verdict": verdict, "reasons": reasons}
