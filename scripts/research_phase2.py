"""Phase 2 — exactly what research/PREREGISTRATION-2026-11-07-phase2.md specifies.

    python scripts/research_phase2.py dev     DATA_DIR OUT_DIR
    python scripts/research_phase2.py holdout DATA_DIR OUT_DIR      # ONE-TIME (marker research/HOLDOUT_P2_USED.json)

DATA_DIR holds: daily/ (IEX daily bars), min_dev/ (40-symbol 1-min, development), min_hold/ (40-symbol 1-min,
holdout + warm-up), gap_dev/ and gap_hold/ (1-min bars of gap candidates). Development code never reads
the holdout caches or holdout rows of the daily bars.
"""
from __future__ import annotations

import json
import os
import pickle
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from dkapp.backtest.runner import HistoryCache  # noqa: E402
from dkapp.clock import MarketCalendar  # noqa: E402
from dkapp.research.data import load_range, sessions_between  # noqa: E402
from dkapp.research.dataset import build_frames, slot_replay  # noqa: E402
from dkapp.research.evaluate import evaluate, judge  # noqa: E402
from dkapp.research.phase2 import (cfg_standard, cfg_wide, daily_stats, gap_context, h1_candidates,  # noqa: E402
                                   h1_random, h2_candidates, h3_from, h4_overnight, h5_trend, net_pct, profile)
from dkapp.research.stats import day_block_bootstrap, holm, top_share  # noqa: E402

DEV = {"h1": (date(2025, 9, 16), date(2026, 11, 5)), "h2": (date(2025, 9, 22), date(2026, 11, 5)),
       "daily": (date(2025, 9, 2), date(2026, 11, 5))}
HOLD = {"h1": (date(2023, 9, 1), date(2025, 8, 29)), "h2": (date(2023, 9, 1), date(2025, 8, 29)),
        "daily": (date(2022, 11, 1), date(2025, 8, 29))}
EVAL_DAILY = {"dev": (date(2025, 9, 2), date(2026, 11, 5)), "holdout": (date(2023, 9, 1), date(2025, 8, 29))}
MARKER = ROOT / "research" / "HOLDOUT_P2_USED.json"
CFG = cfg_standard()
WIDE = cfg_wide()


def load_daily(d: Path, lo: date, hi: date) -> dict[str, pd.DataFrame]:
    out = {}
    for p in sorted((d / "daily").glob("*.pkl")):
        with open(p, "rb") as f:
            df = pickle.load(f)
        df = df[[(lo <= x <= hi) for x in df.index]]
        out[p.stem.replace("_", ".")] = df
    return out


def h1_day(day):
    df = build_frames(day)
    rng = random.Random(day.day.toordinal() * 17 + 3)
    c1 = h1_candidates(df, CFG)
    return {"h1": c1, "rand": h1_random(df, c1, CFG, rng), "h3": [h3_from(df, c, CFG) for c in c1],
            "spy": None}


def run(mode: str, data: Path, out: Path) -> dict:
    per = DEV if mode == "dev" else HOLD
    t0 = time.monotonic()
    # ---------------- H1 / H3 (40 symbols)
    cache = data / ("min_dev" if mode == "dev" else "min_hold")
    days = load_range(cache, *per["h1"], purpose="development" if mode == "dev" else "holdout", marker=MARKER,
                      allow_reuse=mode == "dev")
    with ProcessPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as ex:
        res = list(ex.map(h1_day, days, chunksize=2))
    day_list = [d.day for d in days]
    h1 = [c for r in res for c in r["h1"]]
    rnd = [c for r in res for c in r["rand"]]
    h3 = [c for r in res for c in r["h3"]]
    print(f"H1 {len(h1)} candidates on {len(day_list)} days ({time.monotonic() - t0:.0f}s)", flush=True)

    # ---------------- H2 (gap stocks in play)
    daily = load_daily(data, *per["daily"])
    gcache = HistoryCache(data / ("gap_dev" if mode == "dev" else "gap_hold"))
    cal = MarketCalendar()
    h2, h2b, h2_days = [], [], []
    for day in sessions_between(*per["h2"]):
        sess = cal.session(day)
        if sess is None:
            continue
        h2_days.append(day)
        ctx = gap_context(daily, day)
        bars = {}
        for s, g in ctx.items():
            if s in daily and day in daily[s].index and daily[s].loc[day, "open"] / g.prev_close - 1 >= 0.03:
                b = gcache.get(s, day)
                if b is not None and not b.empty:
                    bars[s] = b
        if not bars:
            continue
        tr, bs = h2_candidates(day, bars, daily, ctx, pd.Timestamp(sess.open).tz_convert("UTC"), WIDE)
        h2 += tr
        h2b += bs
    print(f"H2 {len(h2)} ORB trades, {len(h2b)} baseline on {len(h2_days)} days ({time.monotonic() - t0:.0f}s)",
          flush=True)

    # ---------------- H4 / H5 (daily, research only)
    eval_days = sessions_between(*EVAL_DAILY[mode])
    h4 = h4_overnight(daily, eval_days, CFG)
    h4t = h4_overnight(daily, eval_days, CFG, tight=True)
    h5 = h5_trend(daily, eval_days, CFG)
    h5s = h5_trend(daily, eval_days, cfg_standard().__class__(costs=CFG.costs.with_slippage(10.0)))

    # ---------------- account replays and evaluations
    def replay(cands, cfg):
        trades = slot_replay([c.trade for c in cands], cfg)
        ids = {id(c.trade): c for c in cands}
        return trades, [ids[id(t)] for t in trades]

    r1, r1c = replay(h1, CFG)
    rr, rrc = replay(rnd, CFG)
    r3, r3c = replay([c for c in h3 if c.filled], CFG)
    r2, r2c = replay(h2, WIDE)
    r2b, _ = replay(h2b, WIDE)
    ev = {"P2-H1": evaluate("H1 momentum held to the close", r1, CFG, day_list),
          "H1-random": evaluate("random entry times, same exits", rr, CFG, day_list),
          "P2-H3": evaluate("H1 with a passive limit entry", r3, CFG, day_list,
                            stress=[net_pct(c.day, c.entry_ref, c.trade.ref_exit, CFG, 2.0, buy_is_filled_price=True)[0]
                                    for c in r3c]),
          "P2-H2": evaluate("gap stocks in play, opening-range breakout", r2, WIDE, h2_days),
          "H2-at-9:35": evaluate("same gap stocks bought at 9:35", r2b, WIDE, h2_days)}
    ev["P2-H2"]["stressSpread20AvgRetPct"] = float(np.mean(
        [net_pct(c.day, c.entry_ref, c.trade.ref_exit, WIDE, spread_bps=20.0)[0] for c in r2c])) if r2c else 0.0

    # H4: per-trade (one per ETF per night)
    def daily_eval(rows, key="net"):
        vals = [r[key] for r in rows]
        dd = [r["day"] for r in rows]
        bs = day_block_bootstrap(vals, dd)
        uniq = sorted(set(dd))
        size = max(1, len(uniq) // 4)
        folds = []
        for k in range(4):
            ch = set(uniq[k * size:(k + 1) * size] if k < 3 else uniq[k * size:])
            fv = [r[key] for r in rows if r["day"] in ch]
            folds.append(float(np.mean(fv)) if fv else 0.0)
        pnl = np.cumsum([v / 100 * 20 for v in vals])
        peak = np.maximum.accumulate(np.concatenate([[0.0], pnl]))
        return {"trades": len(vals), "avgRetPct": float(np.mean(vals)) if vals else 0.0,
                "grossAvgRetPct": float(np.mean([r["gross"] for r in rows])) if rows else 0.0,
                "ci90": [bs["lo"], bs["hi"]], "p": bs["p"], "folds": [{"avgRetPct": f} for f in folds],
                "positiveFolds": sum(1 for f in folds if f > 0), "usedFolds": 4,
                "top5Share": top_share([v / 100 * 20 for v in vals]), "totalPnl": float(pnl[-1]) if len(pnl) else 0.0,
                "maxDrawdown": float((peak[1:] - pnl).max()) if len(pnl) else 0.0}
    ev["P2-H4"] = daily_eval(h4)
    stress4 = [r["net"] - 2 * 0.05 for r in h4]   # 2x slippage: +5 bps per side
    ev["P2-H4"]["stress2xAvgRetPct"] = float(np.mean(stress4)) if stress4 else 0.0
    ev["P2-H4"]["tightCostAvgRetPct"] = float(np.mean([r["net"] for r in h4t])) if h4t else 0.0
    g = np.array([r["gross"] for r in h4])
    ev["P2-H4"]["gapRisk"] = {"p01Pct": float(np.percentile(g, 1)) if len(g) else 0, "worstPct": float(g.min()) if len(g) else 0,
                              "nightsBelowMinus2Pct": int((g <= -2).sum()), "nights": int(len(g))}
    s5, b5 = daily_stats(h5["strategy"]), daily_stats(h5["buyhold"])
    bs5 = day_block_bootstrap([v for _, v in h5["strategy"]], [d for d, _ in h5["strategy"]])
    ev["P2-H5"] = {"trades": s5["days"], "avgRetPct": s5["meanDailyPct"], "ci90": [bs5["lo"], bs5["hi"]], "p": bs5["p"],
                   "strategy": s5, "buyHold": b5, "switches": h5["switches"],
                   "stress2xAvgRetPct": daily_stats(h5s["strategy"])["meanDailyPct"],
                   "positiveFolds": 0, "usedFolds": 4, "top5Share": None}
    st = [v for _, v in h5["strategy"]]
    q = max(1, len(st) // 4)
    f5 = [float(np.mean(st[k * q:(k + 1) * q] if k < 3 else st[k * q:])) for k in range(4)] if st else [0] * 4
    ev["P2-H5"]["positiveFolds"] = sum(1 for f in f5 if f > 0)
    ev["P2-H5"]["folds"] = [{"avgRetPct": f} for f in f5]

    # ---------------- verdicts (pre-registered)
    adj = holm({h: ev[h]["p"] for h in ("P2-H1", "P2-H2", "P2-H3", "P2-H4", "P2-H5")})
    base = {"P2-H1": max(0.0, ev["H1-random"]["avgRetPct"]), "P2-H2": max(0.0, ev["H2-at-9:35"]["avgRetPct"]),
            "P2-H3": max(0.0, ev["P2-H1"]["avgRetPct"]), "P2-H4": 0.0, "P2-H5": 0.0}
    verdicts = {}
    for h in adj:
        e = dict(ev[h])
        if h == "P2-H5":
            e["trades"] = max(e["trades"], 0)
            v = judge(e | {"trades": e["trades"] if e["trades"] >= 250 else 0}, adj[h], base[h])
            v["reasons"] = [r for r in v["reasons"] if not r.startswith("only 0 trades")]
            if e["trades"] < 250:
                v["verdict"], v["reasons"] = "insufficient", [f"only {e['trades']} days (need 250)"] + v["reasons"]
            if s5["sharpe"] < b5["sharpe"]:
                v["reasons"].append(f"Sharpe {s5['sharpe']:.2f} below buy-and-hold {b5['sharpe']:.2f}")
            if s5["maxDrawdownPct"] > b5["maxDrawdownPct"]:
                v["reasons"].append(f"deeper drawdown than buy-and-hold ({s5['maxDrawdownPct']:.1f}% vs {b5['maxDrawdownPct']:.1f}%)")
            if v["reasons"] and v["verdict"] == "eligible_for_forward_test":
                v["verdict"] = "rejected"
        else:
            v = judge(e, adj[h], base[h])
            if h == "P2-H2" and ev[h].get("stressSpread20AvgRetPct", 1) <= 0 and v["verdict"] != "insufficient":
                v["reasons"].append("negative with a 20 bps spread")
                v["verdict"] = "rejected"
        verdicts[h] = v | {"holmP": adj[h]}

    # ---------------- opportunity profiles
    profiles = {"P2-H1": profile(h1, CFG), "H1-random": profile(rnd, CFG),
                "P2-H3": profile([c for c in h3 if c.filled], CFG), "P2-H2": profile(h2, WIDE),
                "H2-at-9:35": profile(h2b, WIDE)}
    filled = [c for c in h3 if c.filled]
    unfilled_ids = {(c.day, c.symbol) for c in h3 if not c.filled}
    profiles["P2-H3"]["fillRate"] = len(filled) / len(h3) if h3 else 0.0
    profiles["P2-H3"]["h1NetWhenFilled"] = float(np.mean([c.trade.ret_pct for c in h1 if (c.day, c.symbol) not in unfilled_ids])) if h1 else 0
    profiles["P2-H3"]["h1NetWhenUnfilled"] = float(np.mean([c.trade.ret_pct for c in h1 if (c.day, c.symbol) in unfilled_ids])) if unfilled_ids else None
    # regime breakdown (labels from SPY's whole day: reporting only)
    spy = daily.get("SPY")
    lab = {}
    if spy is not None and not spy.empty:
        rng_ = ((spy["high"] - spy["low"]) / spy["open"] * 100)
        med = float(rng_.median())
        for dday in spy.index:
            lab[dday] = ("up" if spy.loc[dday, "close"] >= spy.loc[dday, "open"] else "down") + \
                        (" volatile" if rng_.loc[dday] > 1.5 * med else " calm")
    regimes = {}
    for h, cs in (("P2-H1", h1), ("P2-H2", h2)):
        by = {}
        for c in cs:
            by.setdefault(lab.get(c.day, "?"), []).append(c.trade.ret_pct)
        regimes[h] = {k: {"n": len(v), "netPct": float(np.mean(v))} for k, v in sorted(by.items())}
    out_obj = {"mode": mode, "periods": {k: [str(a), str(b)] for k, (a, b) in per.items()},
               "evals": ev, "verdicts": verdicts, "profiles": profiles, "regimes": regimes,
               "h3": {"candidates": len(h3), "filled": len(filled)},
               "coverage": {"h1Days": len(day_list), "h2Days": len(h2_days), "h4Nights": len(h4),
                            "h5Days": s5["days"]},
               "runSeconds": time.monotonic() - t0}
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{mode}.json").write_text(json.dumps(out_obj, indent=1, default=str))
    # compact samples for the ML step / later checks (development only)
    if mode == "dev":
        with open(out / "dev_candidates.pkl", "wb") as f:
            pickle.dump({"h1": h1, "h2": h2}, f)
    for h, v in verdicts.items():
        e = ev[h]
        print(f"{h}: {v['verdict']:26s} n={e['trades']:5d} net={e['avgRetPct']:+.3f}% "
              f"gross={e.get('grossAvgRetPct', float('nan')):+.3f}% p={e['p']:.3f} holm={v['holmP']:.3f} | "
              + "; ".join(v["reasons"][:3]), flush=True)
    for k in ("H1-random", "H2-at-9:35"):
        e = ev[k]
        print(f"  {k}: n={e['trades']} net={e['avgRetPct']:+.3f}% gross={e['grossAvgRetPct']:+.3f}%")
    return out_obj


if __name__ == "__main__":
    run(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
