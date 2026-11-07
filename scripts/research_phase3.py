"""Phase 3: exactly what research/PREREGISTRATION-2026-11-07-phase3.md specifies.

    python scripts/research_phase3.py dev     P3_DATA_DIR OUT_DIR [P2_DAILY_DIR]
    python scripts/research_phase3.py holdout P3_DATA_DIR OUT_DIR --only H1,H2   # ONE-TIME (marker)

dev: 1926-07 .. 2005-12 (the pass criteria) and 2022-11 .. 2026-09 (reported), plus the tradable ETF
replication from Alpaca daily bars when P2_DAILY_DIR is given.
holdout: 2006-01 .. 2020-07, only for the hypotheses that passed development. The momentum look-back for
early 2006 uses the last development year (warm-up only, as phase 2 did).
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockpilot.research.academic import load_series, market_total_return, price_index, segments  # noqa: E402
from stockpilot.research.phase3 import (H1_VERSION, H2_VERSION, block_signs, decade_signs, equal_weight_targets,  # noqa: E402
                                   h1_targets, h2_targets, random_topk_percentiles, random_window_baseline,
                                   tom_day_flags, top_months_share, window_returns)
from stockpilot.research.portfolio import SimCosts, monthly_returns, perf, simulate  # noqa: E402
from stockpilot.research.stats import day_block_bootstrap, deflated_sharpe, holm, moving_block_bootstrap  # noqa: E402

CAPS = [100, 500, 1_000, 5_000, 10_000]
PRIMARY_CAP = 10_000
STD = SimCosts()
REVIEW_TRIALS = 36                                   # research/runs/2026-11-07-phase3/review.json
VARIANTS: list[tuple[str, float, int]] = []          # (name, per-period Sharpe, periods) of everything reported


def boot_month(monthly: pd.Series) -> dict:
    days = [p.to_timestamp().date() for p in monthly.index]
    b = day_block_bootstrap(list(monthly.values), days)
    mb = moving_block_bootstrap(list(monthly.values), 12)
    return {"meanPct": b["mean"] * 100, "ci90Pct": [b["lo"] * 100, b["hi"] * 100], "p": b["p"],
            "p12moBlocks": mb["p"]}


def sharpe_pp(x: pd.Series) -> float:
    x = x.dropna()
    return float(x.mean() / x.std(ddof=1)) if len(x) > 2 and x.std(ddof=1) > 0 else 0.0


def regimes(monthly_excess: pd.Series, mkt_daily: pd.Series) -> dict:
    mm = monthly_returns(mkt_daily)
    yr = mm.groupby(mm.index.year).apply(lambda s: float((1 + s).prod() - 1))
    up = {y for y, v in yr.items() if v > 0}
    vol = mkt_daily.groupby([mkt_daily.index.year, mkt_daily.index.month]).std()
    vol.index = pd.PeriodIndex([pd.Period(year=y, month=m, freq="M") for y, m in vol.index])
    terc = pd.qcut(vol.reindex(monthly_excess.index), 3, labels=["calm", "normal", "volatile"])
    out = {"up years": float(monthly_excess[[p.year in up for p in monthly_excess.index]].mean() * 100),
           "down years": float(monthly_excess[[p.year not in up for p in monthly_excess.index]].mean() * 100)}
    for k in ("calm", "normal", "volatile"):
        out[f"{k} months"] = float(monthly_excess[terc == k].mean() * 100)
    return out


# ================================================================== P3-H1
def run_h1(ind12: pd.DataFrame, mkt: pd.Series, rf: pd.Series, label: str, first_decision=None,
           record_variants: bool = True) -> dict:
    p = price_index(ind12)
    targets, log = h1_targets(p, first_decision=first_decision)
    if len(log) < 13:
        return {"label": label, "months": len(log), "note": "too short"}
    ex = [e["execution"] for e in log]
    R = ind12.loc[ex[0]:]
    ew = equal_weight_targets(p, ex)
    sims = {}
    for name, t in (("H1", targets), ("EW", ew)):
        sims[name] = simulate(R, t, PRIMARY_CAP, STD)
    mh, me = monthly_returns(sims["H1"].ret), monthly_returns(sims["EW"].ret)
    exc = (mh - me).dropna()
    mk = mkt.loc[R.index]
    res = {"label": label, "from": str(R.index[0].date()), "to": str(R.index[-1].date()), "months": int(len(exc)),
           "primary": boot_month(exc),
           "perf": {"H1": perf(sims["H1"].ret, rf, sims["H1"].daily["exposure"], sims["H1"].daily["turnover"]),
                    "equalWeight": perf(sims["EW"].ret, rf, sims["EW"].daily["exposure"], sims["EW"].daily["turnover"]),
                    "market": perf(mk.iloc[1:], rf), "cash": perf(rf.loc[R.index].iloc[1:], None)},
           "grossVsNet": {"H1GrossCagrPct": perf(sims["H1"].daily["gross_ret"])["cagrPct"],
                          "H1CostPerYearPct": float((sims["H1"].daily["gross_ret"] - sims["H1"].ret).sum() / (len(R) / 252) * 100)},
           "decades": decade_signs(exc), "blocks3y": block_signs(exc), "top5Share": top_months_share(exc),
           "random": random_topk_percentiles(p, log), "regimes": regimes(exc, mk),
           "excessVsMarket": boot_month((mh - monthly_returns(mk.iloc[1:])).dropna())}
    # 2x costs
    s2 = {n: simulate(R, t, PRIMARY_CAP, STD.scaled(2.0)) for n, t in (("H1", targets), ("EW", ew))}
    res["stress2x"] = boot_month((monthly_returns(s2["H1"].ret) - monthly_returns(s2["EW"].ret)).dropna())
    # capital scenarios (+ T+1 settlement)
    caps = {}
    for c in CAPS:
        s = simulate(R, targets, c, STD)
        caps[f"${c:,}"] = {"cagrPct": perf(s.ret)["cagrPct"], "costPerYearPct": float((s.daily["gross_ret"] - s.ret).sum() / (len(R) / 252) * 100),
                           "trades": s.trades}
    for c in (100, 10_000):
        s = simulate(R, targets, c, STD, buy_delay=1)
        caps[f"${c:,} T+1"] = {"cagrPct": perf(s.ret)["cagrPct"], "costPerYearPct": float((s.daily["gross_ret"] - s.ret).sum() / (len(R) / 252) * 100)}
    res["capital"] = caps
    VARIANTS.append((f"H1 {label}", sharpe_pp(exc), len(exc)))
    if record_variants:
        var = {}
        for name, kw in (("top2", {"top_k": 2}), ("top4", {"top_k": 4}), ("6-1", {"lookback": 126}),
                         ("12-0", {"skip": 0})):
            t, lg = h1_targets(p, first_decision=first_decision, **kw)
            exv = [e["execution"] for e in lg]
            sv = simulate(ind12.loc[exv[0]:], t, PRIMARY_CAP, STD)
            sw = simulate(ind12.loc[exv[0]:], equal_weight_targets(p, exv), PRIMARY_CAP, STD)
            e2 = (monthly_returns(sv.ret) - monthly_returns(sw.ret)).dropna()
            var[name] = {"meanExcessPct": float(e2.mean() * 100), "p": boot_month(e2)["p"]}
            VARIANTS.append((f"H1 {label} {name}", sharpe_pp(e2), len(e2)))
        res["variants"] = var
    res["_excess"] = exc
    return res


# ================================================================== P3-H2
def run_h2(mkt: pd.Series, rf: pd.Series, label: str) -> dict:
    idx = mkt.index
    targets, wins = h2_targets(idx)
    if len(wins) < 12:
        return {"label": label, "windows": len(wins), "note": "too short"}
    R = mkt.to_frame("market")

    def per_window(sim):
        eq = sim.daily["equity"]
        prev = eq.shift(1)
        return pd.Series({a: float(eq.loc[b] / prev.loc[a] - 1.0) for a, b in wins if not np.isnan(prev.loc[a])})

    s = simulate(R, targets, PRIMARY_CAP, STD)
    net = per_window(s)
    net_m = pd.Series(net.values, index=pd.PeriodIndex([pd.Period(d, "M") for d in net.index]))
    ex_daily = mkt - rf
    flags = tom_day_flags(idx, wins)
    a, b = ex_daily[flags], ex_daily[~flags]
    # per-month difference (TOM-day mean minus other-day mean), bootstrapped by month
    g = pd.DataFrame({"x": ex_daily, "f": flags})
    g["m"] = [pd.Period(d, "M") for d in g.index]
    dm = g.groupby("m").apply(lambda d: d.x[d.f].mean() - d.x[~d.f].mean() if d.f.any() and (~d.f).any() else np.nan).dropna()
    gross_ex = window_returns(ex_daily, wins)
    s_rf = simulate(R, targets, PRIMARY_CAP, STD, cash_returns=rf)
    s2 = simulate(R, targets, PRIMARY_CAP, STD.scaled(2.0))
    s_spy = simulate(R, targets, PRIMARY_CAP, SimCosts(0.5, 1.0))
    res = {"label": label, "from": str(idx[0].date()), "to": str(idx[-1].date()), "windows": len(net),
           "primary": boot_month(net_m),
           "tomVsOther": {"tomMeanBps": float(a.mean() * 1e4), "otherMeanBps": float(b.mean() * 1e4),
                          "monthlyDiff": boot_month(dm), "tomDays": int(flags.sum())},
           "grossWindowExcessPct": float(gross_ex.mean() * 100),
           "random4day": random_window_baseline(ex_daily, wins),
           "perf": {"H2 (cash 0%)": perf(s.ret, rf, s.daily["exposure"], s.daily["turnover"]),
                    "H2 (cash at T-bill)": perf(s_rf.ret, rf, s_rf.daily["exposure"], s_rf.daily["turnover"]),
                    "market": perf(mkt.iloc[1:], rf), "cash": perf(rf.iloc[1:], None)},
           "costPerYearPct": float((s.daily["gross_ret"] - s.ret).sum() / (len(idx) / 252) * 100),
           "stress2x": boot_month(pd.Series(per_window(s2).values, index=net_m.index[:len(per_window(s2))])),
           "spyRealisticCosts": boot_month(pd.Series(per_window(s_spy).values, index=net_m.index[:len(per_window(s_spy))])),
           "decades": decade_signs(net_m), "blocks3y": block_signs(net_m), "top5Share": top_months_share(net_m),
           "regimes": regimes(net_m, mkt)}
    caps = {}
    for c in CAPS:
        sc = simulate(R, targets, c, STD)
        caps[f"${c:,}"] = {"cagrPct": perf(sc.ret)["cagrPct"], "costPerYearPct": float((sc.daily["gross_ret"] - sc.ret).sum() / (len(idx) / 252) * 100),
                           "trades": sc.trades}
    res["capital"] = caps
    VARIANTS.append((f"H2 {label}", sharpe_pp(net_m), len(net_m)))
    res["_excess"] = net_m
    return res


# ================================================================== tradable replication (Alpaca ETF bars)
def etf_prices(daily_dir: Path, syms: list[str]) -> pd.DataFrame:
    out = {}
    for s in syms:
        d = pickle.loads((Path(daily_dir) / f"{s}.pkl").read_bytes())
        d.index = pd.to_datetime(d.index)
        r = d["close"].astype(float).pct_change()
        split = (1 + r).between(0.48, 0.52)               # 2-for-1 splits (2025-12-05)
        r[split] = (1 + r[split]) * 2 - 1
        out[s] = r
    return pd.DataFrame(out).sort_index().iloc[1:]


def replication(daily_dir: Path) -> dict:
    sectors = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]
    r = etf_prices(daily_dir, sectors + ["SPY"])
    rs = r[sectors]
    p = price_index(rs)
    t, log = h1_targets(p)
    ex = [e["execution"] for e in log]
    R = rs.loc[ex[0]:]
    raw_px = {s: pickle.loads((Path(daily_dir) / f"{s}.pkl").read_bytes()) for s in sectors + ["SPY"]}
    px = pd.DataFrame({s: pd.Series(v["close"].values, index=pd.to_datetime(v.index)) for s, v in raw_px.items()}).reindex(r.index)
    h1 = simulate(R, t, 1_000, STD, prices=px[sectors].loc[R.index])
    ew = simulate(R, equal_weight_targets(p, ex), 1_000, STD, prices=px[sectors].loc[R.index])
    spy = r["SPY"].loc[R.index].iloc[1:]
    exc = (monthly_returns(h1.ret) - monthly_returns(ew.ret)).dropna()
    t2, w2 = h2_targets(r.index, asset="SPY")
    h2 = simulate(r[["SPY"]], t2, 1_000, STD, prices=px[["SPY"]])
    eq = h2.daily["equity"]
    prev = eq.shift(1)
    w = pd.Series({a: float(eq.loc[b] / prev.loc[a] - 1) for a, b in w2 if not np.isnan(prev.loc[a])})
    return {"note": "Alpaca IEX closes, price only (no dividends), 2-for-1 splits of 2025-12-05 corrected; $1,000",
            "H1 sectors": {"from": str(R.index[0].date()), "months": int(len(exc)), "excessVsEW": boot_month(exc),
                           "perf": perf(h1.ret), "equalWeight": perf(ew.ret), "SPY": perf(spy),
                           "picksLast6": [{"decision": str(e["decision"].date()), "picks": e["picks"]} for e in log[-6:]]},
            "H2 SPY": {"windows": int(len(w)), "meanNetWindowPct": float(w.mean() * 100),
                       "boot": boot_month(pd.Series(w.values, index=pd.PeriodIndex([pd.Period(d, "M") for d in w.index]))),
                       "perf": perf(h2.ret), "SPY": perf(r["SPY"].iloc[1:])}}


# ================================================================== verdicts
def dev_verdict(h: str, r: dict, dsr: dict) -> dict:
    reasons = []
    if not (r["primary"]["meanPct"] > 0 and r["primary"]["p"] < 0.05):
        reasons.append(f"primary {r['primary']['meanPct']:+.3f}%/month, p={r['primary']['p']:.3f}")
    if r["decades"]["positive"] < 6:
        reasons.append(f"positive in only {r['decades']['positive']} of {r['decades']['of']} decades")
    if r["stress2x"]["meanPct"] <= 0:
        reasons.append("negative with 2x costs")
    if r["top5Share"] is None or r["top5Share"] >= 0.5:
        reasons.append("the 5 best months make half the result")
    if dsr["dsr"] <= 0.95:
        reasons.append(f"deflated Sharpe {dsr['dsr']:.2f} <= 0.95")
    return {"pass": not reasons, "reasons": reasons}


def holdout_verdict(h: str, r: dict, holm_p: float) -> dict:
    reasons = []
    if not (r["primary"]["meanPct"] > 0 and holm_p < 0.05):
        reasons.append(f"primary {r['primary']['meanPct']:+.3f}%/month, Holm p={holm_p:.3f}")
    if r["blocks3y"]["positive"] < 3:
        reasons.append(f"positive in only {r['blocks3y']['positive']} of {r['blocks3y']['of']} three-year blocks")
    if r["stress2x"]["meanPct"] <= 0:
        reasons.append("negative with 2x costs")
    if h == "P3-H2":
        sh, bh = r["perf"]["H2 (cash 0%)"]["sharpe"], r["perf"]["market"]["sharpe"]
        if sh < 0.5 * bh:
            reasons.append(f"Sharpe {sh:.2f} below half of buy-and-hold's {bh:.2f}")
        if r["perf"]["H2 (cash 0%)"]["cagrPct"] <= 0:
            reasons.append("no absolute profit")
    return {"verdict": "eligible_for_forward_shadow" if not reasons else "rejected", "reasons": reasons, "holmP": holm_p}


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items() if not k.startswith("_")}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


def main(mode: str, data: Path, out: Path, p2daily: Path | None, only: list[str]) -> int:
    out.mkdir(parents=True, exist_ok=True)
    result = {"mode": mode, "versions": {"P3-H1": H1_VERSION, "P3-H2": H2_VERSION}}
    if mode == "dev":
        ff3, ind12 = load_series(data, "ff3_daily"), load_series(data, "ind12_daily")
        mkt, rf = market_total_return(ff3), ff3["RF"]
        h1, h2 = {}, {}
        for a, b in segments(ind12.index):
            lab = f"{a.year}-{b.year}"
            h1[lab] = run_h1(ind12.loc[a:b], mkt, rf, lab, record_variants=lab.startswith("1926"))
            h2[lab] = run_h2(mkt.loc[a:b], rf.loc[a:b], lab)
            print("done", lab, flush=True)
        n_trials = REVIEW_TRIALS + len(VARIANTS)
        srs = np.array([v[1] for v in VARIANTS])
        verdicts = {}
        for h, res in (("P3-H1", h1["1926-2005"]), ("P3-H2", h2["1926-2005"])):
            var = max(float(srs.var(ddof=1)) if len(srs) > 2 else 0.0, 1.0 / max(1, res["months"] if h == "P3-H1" else res["windows"]))
            dsr = deflated_sharpe(list(res["_excess"].values), n_trials, var)
            res["deflatedSharpe"] = dsr
            verdicts[h] = dev_verdict(h, res, dsr)
        result.update({"H1": h1, "H2": h2, "trials": {"review": REVIEW_TRIALS, "strategyVariants": [v[0] for v in VARIANTS],
                                                       "total": n_trials}, "devVerdicts": verdicts})
        if p2daily:
            result["replication"] = replication(p2daily)
    else:
        dev_ff3, dev_ind = load_series(data, "ff3_daily"), load_series(data, "ind12_daily")
        ff3 = load_series(data, "ff3_daily", purpose="holdout")
        ind12 = load_series(data, "ind12_daily", purpose="holdout", allow_reuse=True)   # same one-time use
        mkt, rf = market_total_return(ff3), ff3["RF"]
        res, pvals = {}, {}
        if "H1" in only:
            warm = dev_ind.loc[:"2005-12-30"].iloc[-300:]
            both = pd.concat([warm, ind12])
            r1 = run_h1(both, pd.concat([market_total_return(dev_ff3).loc[warm.index], mkt]),
                        pd.concat([dev_ff3["RF"].loc[warm.index], rf]), "2006-2020",
                        first_decision=ind12.index[0], record_variants=False)
            res["P3-H1"], pvals["P3-H1"] = r1, r1["primary"]["p"]
        if "H2" in only:
            r2 = run_h2(mkt, rf, "2006-2020")
            res["P3-H2"], pvals["P3-H2"] = r2, r2["primary"]["p"]
        adj = holm(pvals)
        result.update({"results": res, "verdicts": {h: holdout_verdict(h, res[h], adj[h]) for h in res}})
    (out / f"{mode}.json").write_text(json.dumps(clean(result), indent=1, default=str))
    print(json.dumps(clean(result.get("devVerdicts", result.get("verdicts"))), indent=1))
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    only = ["H1", "H2"]
    if "--only" in args:
        only = args[args.index("--only") + 1].split(",")
        args = args[:args.index("--only")]
    raise SystemExit(main(args[0], Path(args[1]), Path(args[2]), Path(args[3]) if len(args) > 3 else None, only))
