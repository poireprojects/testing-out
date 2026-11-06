"""Research round 1 — runs exactly what research/PREREGISTRATION-2026-11-06.md specifies.

    python scripts/research_round1.py dev     CACHE_DIR OUT_DIR   # development (walk-forward) only
    python scripts/research_round1.py holdout CACHE_DIR OUT_DIR   # ONE-TIME reserved holdout evaluation

Development: 2025-09-16..2026-04-30 plus the examined 2026-09-11..2026-11-05 (the reserved holdout is
refused by the loader). Holdout: models trained on development days before 2026-05-01 only; every
pre-registered hypothesis is evaluated on 2026-05-01..2026-08-26, once.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from dkapp.backtest.engine import BTConfig, replay_portfolio, run_day  # noqa: E402
from dkapp.quant.features import FEATURES, STRATEGY_FEATURES  # noqa: E402
from dkapp.quant.model import QuantModel  # noqa: E402
from dkapp.research.data import load_range  # noqa: E402
from dkapp.research.dataset import (build_frames, dense_samples, h1_mean_reversion, h2_relative_strength,  # noqa: E402
                                    h3_market_intraday_momentum, random_select, slot_replay, strategy_samples)
from dkapp.research.diagnose import HORIZONS, diagnose, fwd_gross, key, trade_paths  # noqa: E402
from dkapp.research.economics import economics  # noqa: E402
from dkapp.research.evaluate import evaluate, judge, model_metrics, walk_forward_predict  # noqa: E402
from dkapp.research.ledger import DataLedger  # noqa: E402
from dkapp.research.stats import holm  # noqa: E402
from dkapp.strategies import ALL_STRATEGIES  # noqa: E402

DEV_RANGES = [(date(2025, 9, 16), date(2026, 4, 30)), (date(2026, 9, 11), date(2026, 11, 5))]
HOLDOUT = (date(2026, 5, 1), date(2026, 8, 26))
F_NAMES = list(FEATURES)
S_NAMES = F_NAMES + STRATEGY_FEATURES
CFG = BTConfig()


def process_day(day):
    df = build_frames(day)
    rng = random.Random(day.day.toordinal() * 31 + CFG.seed)
    real = [t for t in run_day(day, list(ALL_STRATEGIES), CFG, rng) if not t.random_baseline]
    dense = dense_samples(df, CFG)
    spy = day.bars.get("SPY")
    reg = None
    if spy is not None and not spy.empty:
        o = float(spy["open"].iat[0])
        reg = {"up": float(spy["close"].iat[-1]) >= o, "range": (float(spy["high"].max()) - float(spy["low"].min())) / o * 100}
    return {"day": day.day, "real": real, "paths": {key(t): trade_paths(df, t) for t in real},
            "dense": dense, "dpaths": {key(s.trade): {f"fwd{h}": fwd_gross(df, s.symbol, s.trade.entry_i, h)
                                                     for h in HORIZONS} for s in dense},
            "strat": strategy_samples(df, real), "h1": h1_mean_reversion(df, CFG), "h2": h2_relative_strength(df, CFG),
            "h3": h3_market_intraday_momentum(df, CFG), "spy_trend": day.spy_trend, "regime": reg,
            "symbols": len(df.syms)}


def collect(cache: Path, ranges, purpose: str) -> dict:
    days = []
    for a, b in ranges:
        days += load_range(cache, a, b, purpose=purpose)
    t0 = time.monotonic()
    with ProcessPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as ex:
        res = list(ex.map(process_day, days, chunksize=2))
    print(f"processed {len(res)} days in {time.monotonic() - t0:.0f}s", flush=True)
    res.sort(key=lambda r: r["day"])
    ranges_ = [r["regime"]["range"] for r in res if r["regime"]]
    med = float(np.median(ranges_)) if ranges_ else 0.0
    regimes = {r["day"]: ("up day" if r["regime"]["up"] else "down day") +
               (", volatile" if r["regime"]["range"] > 1.5 * med else ", calm") for r in res if r["regime"]}
    out = {"days": [r["day"] for r in res], "regimes": regimes, "spy": {r["day"]: r["spy_trend"] for r in res},
           "coverage": {"days": len(res), "symbolDays": sum(r["symbols"] for r in res), "expected": 40 * len(res)}}
    for k in ("real", "dense", "strat", "h1", "h2", "h3"):
        out[k] = [x for r in res for x in r[k]]
    out["paths"] = {k: v for r in res for k, v in r["paths"].items()}
    out["dpaths"] = {k: v for r in res for k, v in r["dpaths"].items()}
    return out


# ------------------------------------------------------------------ decision rules (pre-registered)
def takes(p: dict) -> bool:
    return p["exp_net"] > 0 and p["p_win"] > 0.5


def model_select(pred_by_key):
    def pick(day, minute, cands, free):
        ok = [t for t in cands if key(t) in pred_by_key and takes(pred_by_key[key(t)])]
        return sorted(ok, key=lambda t: pred_by_key[key(t)]["exp_net"], reverse=True)[:free]
    return pick


def matched(pred_by_key, how: str, seed: int = 9):
    rng = random.Random(seed)

    def pick(day, minute, cands, free):
        k = min(free, sum(1 for t in cands if key(t) in pred_by_key and takes(pred_by_key[key(t)])))
        if k <= 0:
            return []
        if how == "momentum":
            return sorted(cands, key=lambda t: t.score, reverse=True)[:k]
        c = list(cands)
        rng.shuffle(c)
        return c[:k]
    return pick


def dense_trades(dense, preds):
    out, pk = [], {}
    for i, s in enumerate(dense):
        if i in preds:
            t = s.trade
            t.score = s.extra.get("mom30", 0.0)  # for the momentum baseline
            out.append(t)
            pk[key(t)] = preds[i]
    return out, pk


def strat_preds(strat, preds):
    return {key(s.trade): preds[i] for i, s in enumerate(strat) if i in preds}


def evaluate_all(data: dict, h4_preds: dict, h5_preds: dict, day_list: list, label: str) -> dict:
    cfg = CFG
    spy = data["spy"]
    real = data["real"]
    ev = {}
    # baselines: the app's existing rules
    ev["rules_ranker"] = evaluate("rules + ranker (build 20 app logic)", replay_portfolio(real, cfg, True, spy), cfg, day_list)
    ev["rules_first_come"] = evaluate("rules, first come", replay_portfolio(real, cfg, False, spy), cfg, day_list)
    strategies = {sid: evaluate(ALL_STRATEGIES[sid].name + " (every signal)", [t for t in real if t.strategy == sid],
                                cfg, day_list) for sid in ALL_STRATEGIES}
    # dense-sample baselines on the same slots
    d_all = [s.trade for s in data["dense"]]
    ev["dense_random"] = evaluate("random pick (dense sample)", slot_replay(d_all, cfg, random_select(5)), cfg, day_list)
    # H1-H3
    for h in ("h1", "h2", "h3"):
        ev[h] = evaluate(h, slot_replay(data[h], cfg), cfg, day_list)
    # H4: statistical model on the dense sample (test days only)
    d_tr, pk = dense_trades(data["dense"], h4_preds)
    h4_days = sorted({t.day for t in d_tr})
    ev["h4"] = evaluate("H4 model (dense)", slot_replay(d_tr, cfg, model_select(pk)), cfg, h4_days)
    ev["h4_random_matched"] = evaluate("random, same count", slot_replay(d_tr, cfg, matched(pk, "random")), cfg, h4_days)
    ev["h4_momentum_matched"] = evaluate("highest 30-min return, same count", slot_replay(d_tr, cfg, matched(pk, "momentum")),
                                         cfg, h4_days)
    # exploratory (NOT a pre-registered test): expected net > 0 alone
    ev["h4_explore_expnet_only"] = evaluate("H4 exploratory: expected net > 0 only", slot_replay(
        d_tr, cfg, lambda d, m, c, f: sorted([t for t in c if pk[key(t)]["exp_net"] > 0],
                                             key=lambda t: pk[key(t)]["exp_net"], reverse=True)[:f]), cfg, h4_days)
    # H5: statistical filter on the rules' signals, through the app's own ranker and slots (test days only)
    sp = strat_preds(data["strat"], h5_preds)
    h5_days = sorted({k[0] for k in sp})
    real5 = [t for t in real if t.day in set(h5_days)]
    ev["h5"] = evaluate("H5 rules + model filter", replay_portfolio(real5, cfg, True, spy,
                                                                    gate=lambda t: key(t) in sp and takes(sp[key(t)])),
                        cfg, h5_days)
    ev["h5_rules_same_days"] = evaluate("rules + ranker, same days", replay_portfolio(real5, cfg, True, spy), cfg, h5_days)
    rate = np.mean([takes(v) for v in sp.values()]) if sp else 0.0
    rr = random.Random(13)
    rnd_gate = {k: rr.random() < rate for k in sp}
    ev["h5_random_gate"] = evaluate("rules + random filter (same rate)", replay_portfolio(
        real5, cfg, True, spy, gate=lambda t: rnd_gate.get(key(t), False)), cfg, h5_days)
    return {"label": label, "evals": ev, "strategies": strategies, "h4Days": [str(h4_days[0]), str(h4_days[-1])] if h4_days else [],
            "h5Days": [str(h5_days[0]), str(h5_days[-1])] if h5_days else [], "h5TakeRate": float(rate)}


def verdicts(res: dict, m4: dict, m5: dict) -> dict:
    ev = res["evals"]
    adj = holm({h: ev[h]["p"] for h in ("h1", "h2", "h3", "h4", "h5")})
    base = {"h1": max(0.0, ev["dense_random"]["avgRetPct"]), "h2": max(0.0, ev["dense_random"]["avgRetPct"]),
            "h3": max(0.0, ev["dense_random"]["avgRetPct"]),
            "h4": max(0.0, ev["h4_random_matched"]["avgRetPct"], ev["h4_momentum_matched"]["avgRetPct"]),
            "h5": max(0.0, ev["h5_rules_same_days"]["avgRetPct"], ev["h5_random_gate"]["avgRetPct"])}
    return {h: judge(ev[h], adj[h], base[h], model=(m4 if h == "h4" else m5 if h == "h5" else None)) | {"holmP": adj[h]}
            for h in adj}


def arrays(samples):
    X = np.array([s.x for s in samples], dtype=float)
    y = np.array([s.trade.ret_pct for s in samples], dtype=float)
    d = [s.day for s in samples]
    return X, y, d


def strip(obj):
    """json-safe (dates -> str)."""
    if isinstance(obj, dict):
        return {str(k): strip(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [strip(v) for v in obj]
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, date):
        return str(obj)
    return obj


def run_dev(cache: Path, out: Path) -> int:
    data = collect(cache, DEV_RANGES, "development")
    day_list = data["days"]
    X4, y4, d4 = arrays(data["dense"])
    wf4 = walk_forward_predict(X4, y4, d4, F_NAMES)
    X5, y5, d5 = arrays(data["strat"])
    wf5 = walk_forward_predict(X5, y5, d5, S_NAMES)
    m4, m5 = model_metrics(wf4.preds, y4), model_metrics(wf5.preds, y5)
    res = evaluate_all(data, wf4.preds, wf5.preds, day_list, "development (walk-forward; includes examined days)")
    res["verdicts"] = verdicts(res, m4, m5)
    res["models"] = {"h4": m4, "h5": m5,
                     "h4LastModel": wf4.models[-1][1].to_dict() if wf4.models else None,
                     "h5LastModel": wf5.models[-1][1].to_dict() if wf5.models else None,
                     "h4Samples": len(y4), "h5Samples": len(y5)}
    res["diagnosis"] = diagnose(data["real"], data["paths"], data["dense"], data["dpaths"], CFG, data["regimes"], data["spy"])
    res["economics"] = economics(CFG.costs, CFG.spread_bps, per_trade_sd_pct=res["evals"]["rules_ranker"]["sdPct"] or 0.6)
    res["coverage"] = data["coverage"]
    res["ledger"] = DataLedger.load().label(day_list)
    res["config"] = {"notional": CFG.notional, "spreadBps": CFG.spread_bps, "slippageBps": CFG.costs.slippage_bps,
                     "rounding": CFG.costs.rounding, "limitThroughBps": CFG.limit_through_bps,
                     "maxPositions": CFG.max_positions, "ranges": [[str(a), str(b)] for a, b in DEV_RANGES]}
    out.mkdir(parents=True, exist_ok=True)
    (out / "dev.json").write_text(json.dumps(strip(res), indent=1, default=float))
    for h, v in res["verdicts"].items():
        e = res["evals"][h]
        print(f"{h}: {v['verdict']:28s} trades={e['trades']:5d} net={e['avgRetPct']:+.3f}% gross={e['grossAvgRetPct']:+.3f}% "
              f"p={e['p']:.3f} holm={v['holmP']:.3f} | {'; '.join(v['reasons'][:3])}")
    for k in ("rules_ranker", "rules_first_come", "dense_random", "h4_random_matched", "h4_momentum_matched",
              "h4_explore_expnet_only", "h5_rules_same_days", "h5_random_gate"):
        e = res["evals"][k]
        print(f"  {k}: trades={e['trades']} net={e['avgRetPct']:+.3f}% gross={e['grossAvgRetPct']:+.3f}% pnl=${e['totalPnl']:.2f}")
    print("models", {k: {kk: m[kk] for kk in ("n", "baseRate", "brier", "brierClimatology", "auc", "corrExpNetActual")}
                     for k, m in (("h4", m4), ("h5", m5)) if m.get("n")})
    return 0


def run_holdout(cache: Path, out: Path) -> int:
    dev = collect(cache, [DEV_RANGES[0]], "development")       # train only on development days before the holdout
    hold = collect(cache, [HOLDOUT], "holdout")                 # one-time use; leaves research/HOLDOUT_USED.json
    X4, y4, d4 = arrays(dev["dense"])
    X5, y5, d5 = arrays(dev["strat"])
    last = max(d4)
    keep4 = np.array([d < last for d in d4])                   # 1-day embargo before the holdout
    keep5 = np.array([d < last for d in d5])
    g4 = np.array([d.toordinal() for d in d4])
    g5 = np.array([d.toordinal() for d in d5])
    m4 = QuantModel(F_NAMES).fit(X4[keep4], y4[keep4], groups=g4[keep4])
    m5 = QuantModel(S_NAMES).fit(X5[keep5], y5[keep5], groups=g5[keep5])
    hX4, hy4, _ = arrays(hold["dense"])
    hX5, hy5, _ = arrays(hold["strat"])
    p4, p5 = m4.predict(hX4), m5.predict(hX5)
    pr4 = {i: {k: float(v[i]) for k, v in p4.items()} | {"base": m4.base_rate} for i in range(len(hy4))}
    pr5 = {i: {k: float(v[i]) for k, v in p5.items()} | {"base": m5.base_rate} for i in range(len(hy5))}
    mm4, mm5 = model_metrics(pr4, hy4), model_metrics(pr5, hy5)
    res = evaluate_all(hold, pr4, pr5, hold["days"], "RESERVED HOLDOUT (first and only use)")
    res["verdicts"] = verdicts(res, mm4, mm5)
    res["models"] = {"h4": mm4, "h5": mm5, "h4Model": m4.to_dict(), "h5Model": m5.to_dict(),
                     "h4Fingerprint": m4.fingerprint(), "h5Fingerprint": m5.fingerprint()}
    res["diagnosis"] = diagnose(hold["real"], hold["paths"], hold["dense"], hold["dpaths"], CFG, hold["regimes"], hold["spy"])
    res["coverage"] = hold["coverage"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "holdout.json").write_text(json.dumps(strip(res), indent=1, default=float))
    for h, v in res["verdicts"].items():
        e = res["evals"][h]
        print(f"{h}: {v['verdict']:28s} trades={e['trades']:5d} net={e['avgRetPct']:+.3f}% gross={e['grossAvgRetPct']:+.3f}% "
              f"p={e['p']:.3f} holm={v['holmP']:.3f} | {'; '.join(v['reasons'][:3])}")
    for k in ("rules_ranker", "dense_random", "h4_random_matched", "h4_momentum_matched", "h5_rules_same_days"):
        e = res["evals"][k]
        print(f"  {k}: trades={e['trades']} net={e['avgRetPct']:+.3f}% pnl=${e['totalPnl']:.2f}")
    return 0


if __name__ == "__main__":
    mode, cache, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    raise SystemExit(run_dev(cache, out) if mode == "dev" else run_holdout(cache, out))
