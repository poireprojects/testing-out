"""Phase 4 evaluation of P4-H1, exactly as research/PREREGISTRATION-2026-11-07-phase4.md specifies.

    python scripts/phase4_test.py DATA_DIR OUT_DIR

Reads plan.json, quotes.json and bars15/ from scripts/phase4_collect.py. Computes every pre-registered
quantity once and writes results.json. Nothing here is tuned.
"""
from __future__ import annotations

import json
import math
import pickle
import sys
from datetime import date, datetime, time as dtime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dkapp.clock import ET  # noqa: E402
from dkapp.quant.model import LogisticL2, Standardizer  # noqa: E402
from dkapp.research.stats import day_block_bootstrap, holm, top_share  # noqa: E402
from dkapp.risk.fees import CostModel  # noqa: E402

SLIP_BPS = 5.0
STOP = 0.03
NOTIONAL = 20.0


def _ceil_cent(x):
    return math.ceil(round(x * 100, 9)) / 100 if x > 0 else 0.0


class Data:
    def __init__(self, d: Path):
        self.d = d
        self.q = json.loads((d / "quotes.json").read_text())
        self._bars = {}

    def bars(self, s: str, day: date) -> pd.DataFrame:
        if s not in self._bars:
            p = self.d / "bars15" / f"{s.replace('.', '_')}.pkl"
            b = pickle.load(open(p, "rb")) if p.exists() else pd.DataFrame()
            if not b.empty:
                b = b.copy()
                b["day"] = b.index.date
            self._bars[s] = b
        b = self._bars[s]
        return b[b["day"] == day] if not b.empty else b

    def quote(self, s: str, day: str, win: str):
        return self.q.get(f"{day}|{win}", {}).get(s)


def trade(data: Data, s: str, dstr: str, cost_mult: float = 1.0) -> dict:
    """One pre-registered trade: enter after 10:00:30 at the NBBO, 3% stop on 15-minute bars, exit 15:45."""
    day = date.fromisoformat(dstr)
    qe, qx = data.quote(s, dstr, "entry"), data.quote(s, dstr, "exit")
    if qe is None:
        return {"filled": False, "why": "no NBBO at entry"}
    hs_e = qe["halfSpreadBps"] * cost_mult
    slip = SLIP_BPS * cost_mult
    entry_mid = qe["mid"]
    entry = entry_mid * (1 + (hs_e + slip) / 1e4)
    stop = entry * (1 - STOP)
    b = data.bars(s, day)
    after = b[(b.index >= datetime.combine(day, dtime(10, 0), ET)) & (b.index < datetime.combine(day, dtime(15, 45), ET))]
    reason, exit_ref, hs_x = "15:45", None, None
    for t, r in after.iterrows():
        if r["low"] <= stop:
            exit_ref, reason, hs_x = min(stop, float(r["open"])), "stop", hs_e
            break
    fallback = False
    if exit_ref is None:
        if qx is not None:
            exit_ref, hs_x = qx["mid"], qx["halfSpreadBps"] * cost_mult
        else:
            last = b[b.index >= datetime.combine(day, dtime(15, 45), ET)]
            if last.empty:
                return {"filled": False, "why": "no exit price"}
            exit_ref, hs_x, fallback = float(last["open"].iloc[0]), hs_e, True
    exit_ = exit_ref * (1 - (hs_x + slip) / 1e4)
    cm = CostModel.on(day)
    qty = NOTIONAL / entry
    sale = NOTIONAL * exit_ / entry
    sb, bb = cm.sale_breakdown(sale, qty), cm.buy_breakdown(qty)
    fees = _ceil_cent(sb.sec) + _ceil_cent(sb.taf) + _ceil_cent(sb.cat + bb.cat)
    gross = (exit_ref / entry_mid - 1) * 100
    net = (exit_ / entry - 1) * 100 - fees / NOTIONAL * 100
    return {"filled": True, "gross": gross, "net": net, "reason": reason, "exitFallback": fallback,
            "entryHalfSpreadBps": qe["halfSpreadBps"], "askSize": qe.get("askSize"), "feesPct": fees / NOTIONAL * 100,
            "spreadSlipPct": (exit_ref / entry_mid - 1) * 100 - (exit_ / entry - 1) * 100}


def summarize(rows: list[dict], key: str = "net") -> dict:
    f = [r for r in rows if r.get("filled")]
    if not f:
        return {"n": 0, "planned": len(rows)}
    v = np.array([r[key] for r in f])
    days = [date.fromisoformat(r["day"]) for r in f]
    bs = day_block_bootstrap(list(v), days)
    return {"planned": len(rows), "n": len(f), "meanPct": float(v.mean()), "ci90": [bs["lo"], bs["hi"]], "p": bs["p"],
            "grossPct": float(np.mean([r["gross"] for r in f])), "medianPct": float(np.median(v)),
            "winRate": float((v > 0).mean()), "p05": float(np.percentile(v, 5)), "p95": float(np.percentile(v, 95)),
            "worst": float(v.min()), "best": float(v.max()), "stops": int(sum(r["reason"] == "stop" for r in f)),
            "top5Share": top_share([x / 100 * NOTIONAL for x in v]),
            "missed": len(rows) - len(f), "avgEntryHalfSpreadBps": float(np.mean([r["entryHalfSpreadBps"] for r in f]))}


def paired(a: list[dict], b: list[dict]) -> dict:
    pairs = [(x["net"] - y["net"], x["day"]) for x, y in zip(a, b) if x.get("filled") and y and y.get("filled")]
    if not pairs:
        return {"n": 0}
    v = [p[0] for p in pairs]
    bs = day_block_bootstrap(v, [date.fromisoformat(p[1]) for p in pairs])
    return {"n": len(v), "meanExcessPct": float(np.mean(v)), "ci90": [bs["lo"], bs["hi"]], "p": bs["p"]}


def half_years(rows):
    f = [r for r in rows if r.get("filled")]
    g = {}
    for r in f:
        d = date.fromisoformat(r["day"])
        g.setdefault(f"{d.year}H{1 if d.month <= 6 else 2}", []).append(r["net"])
    return {k: {"n": len(v), "meanPct": float(np.mean(v))} for k, v in sorted(g.items())}


def capital(rows: list[dict], cap: float) -> dict:
    """$cap account, 2 slots of 20% each (the app's rule), the 2 largest reactions per day, fees per day."""
    byday = {}
    for r in rows:
        if r.get("filled"):
            byday.setdefault(r["day"], []).append(r)
    eq, curve = cap, []
    for d in sorted(byday):
        take = sorted(byday[d], key=lambda r: -r["react"])[:2]
        size = 0.2 * eq
        pnl = sum(size * (r["net"] + r["feesPct"]) / 100 for r in take)      # net without the $20-based fees
        day = date.fromisoformat(d)
        cm = CostModel.on(day)
        sec = taf = cat = 0.0
        for r in take:
            qty = size / 100.0                                             # $100 shares as a round assumption
            sec += size * cm.sec_rate
            taf += min(qty * cm.taf_per_share, cm.taf_max)
            cat += 2 * qty * cm.cat_per_share
        pnl -= _ceil_cent(sec) + _ceil_cent(taf) + _ceil_cent(cat)
        eq += pnl
        curve.append(eq)
    if not curve:
        return {"trades": 0}
    c = np.array([cap] + curve)
    dd = float((c / np.maximum.accumulate(c) - 1).min() * 100)
    return {"start": cap, "end": float(c[-1]), "pnl": float(c[-1] - cap), "tradeDays": len(curve), "maxDrawdownPct": dd}


def main(data_dir: Path, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    plan = json.loads((data_dir / "plan.json").read_text())
    D = Data(data_dir)
    res = {"preregistration": "research/PREREGISTRATION-2026-11-07-phase4.md"}
    for r in plan:
        r["t"] = trade(D, r["symbol"], r["day"]) | {"day": r["day"], "react": r["react"]}
        r["t2"] = trade(D, r["symbol"], r["day"], 2.0) | {"day": r["day"], "react": r["react"]}
        if r["H1"]:
            r["b1"] = trade(D, r["sector"], r["day"]) | {"day": r["day"]}
            r["b3"] = (trade(D, r["B3"]["symbol"], r["day"]) | {"day": r["day"]}) if "B3" in r else None
            r["b4"] = (trade(D, r["symbol"], r["B4"]["day"]) | {"day": r["B4"]["day"]}) if "B4" in r else None
    for fold in ("dev", "eval"):
        P = [r for r in plan if r["fold"] == fold]
        H = [r for r in P if r["H1"]]
        out_f = {"events": len(P), "H1": summarize([r["t"] for r in H]), "H1_2xCosts": summarize([r["t2"] for r in H]),
                 "B2_allEvents": summarize([r["t"] for r in P]),
                 "B1_sectorPaired": paired([r["t"] for r in H], [r["b1"] for r in H]),
                 "B3_priceOnlyPaired": paired([r["t"] for r in H], [r.get("b3") for r in H]),
                 "B4_randomDayPaired": paired([r["t"] for r in H], [r.get("b4") for r in H]),
                 "B1_sector": summarize([r["b1"] for r in H]), "B3_priceOnly": summarize([r["b3"] for r in H if r.get("b3")]),
                 "B4_randomDay": summarize([r["b4"] for r in H if r.get("b4")]),
                 "halfYears": half_years([r["t"] for r in H]),
                 "bySurprise": {f"{a}/{b}": summarize([r["t"] for r in P if r["surprise"].get("eps") == a and r["surprise"].get("sales") == b])
                                for a in ("beat", "miss") for b in ("beat", "miss")},
                 "capital": {f"${c:,}": capital([r["t"] for r in H], c) for c in (100, 1_000, 10_000)}}
        res[fold] = out_f
    # ---------------- ML (one bounded test): train on dev events, apply once to eval
    def feats(r):
        s = r["surprise"]
        return [float(s.get("eps") == "beat"), float(s.get("eps") == "miss"), float(s.get("sales") == "beat"),
                float(s.get("sales") == "miss"), r["react"], r.get("spyReact") or 0.0, r.get("volRatio") or 0.0,
                math.log(max(r["prevClose"], 1e-6)), math.log(max(r["adv20"], 1.0))]
    tr = [r for r in plan if r["fold"] == "dev" and r["t"].get("filled")]
    te = [r for r in plan if r["fold"] == "eval" and r["t"].get("filled")]
    ml = {"trainEvents": len(tr), "testEvents": len(te)}
    if len(tr) > 50 and te:
        X = np.array([feats(r) for r in tr])
        st = Standardizer.fit(X)
        m = LogisticL2(lam=1.0).fit(st.transform(X), np.array([float(r["t"]["net"] > 0) for r in tr]))
        p = m.predict(st.transform(np.array([feats(r) for r in te])))
        chosen = [r["t"] for r, q in zip(te, p) if q > 0.5]
        ml.update({"selected": len(chosen), "result": summarize(chosen) if chosen else {"n": 0},
                   "coef": dict(zip(["epsBeat", "epsMiss", "salesBeat", "salesMiss", "react", "spyReact", "volRatio", "logPrice", "logADV"],
                                    [float(x) for x in m.w]))})
    res["ML"] = ml
    # ---------------- verdict (pre-registered criteria on the evaluation fold)
    ev, dv = res["eval"], res["dev"]
    pv = {"H1 net > 0": ev["H1"].get("p", 1.0), "H1 beats price-only (B3)": ev["B3_priceOnlyPaired"].get("p", 1.0),
          "ML net > 0": (ml.get("result") or {}).get("p", 1.0)}
    adj = holm(pv)
    hy = ev["halfYears"]
    crit = {
        "1. >= 100 filled trades": ev["H1"].get("n", 0) >= 100,
        "2. mean net > 0 (Holm p < 0.05)": ev["H1"].get("meanPct", -1) > 0 and adj["H1 net > 0"] < 0.05,
        "3. beats price-only comparable (Holm p < 0.05)": ev["B3_priceOnlyPaired"].get("meanExcessPct", -1) > 0 and adj["H1 beats price-only (B3)"] < 0.05,
        "4. beats sector ETF (point estimate)": ev["B1_sectorPaired"].get("meanExcessPct", -1) > 0,
        "5. positive in >= 3 of 4 half-years": sum(v["meanPct"] > 0 for v in hy.values()) >= 3,
        "6. positive with 2x costs": ev["H1_2xCosts"].get("meanPct", -1) > 0,
        "7. top-5 trades < 50% of profit": ev["H1"].get("top5Share") is not None and ev["H1"]["top5Share"] < 0.5,
        "8. development fold also > 0": dv["H1"].get("meanPct", -1) > 0}
    mlr = ml.get("result") or {}
    ml_kept = bool(mlr.get("n") and mlr["meanPct"] > 0 and adj["ML net > 0"] < 0.05 and mlr["meanPct"] > ev["B2_allEvents"]["meanPct"]
                   and mlr["meanPct"] > ev["H1"].get("meanPct", 0))
    if ev["H1"].get("n", 0) < 100:
        decision = "INSUFFICIENT EVIDENCE"
    elif all(crit.values()):
        decision = "GO (forward shadow only)"
    else:
        decision = "NO-GO"
    res["verdict"] = {"criteria": crit, "holm": adj, "rawP": pv, "mlKept": ml_kept, "decision": decision}
    (out / "results.json").write_text(json.dumps(res, indent=1, default=str))
    keep = [{k: r[k] for k in ("symbol", "day", "fold", "H1", "react", "surprise", "sector")} | {"net": r["t"].get("net"), "filled": r["t"].get("filled")}
            for r in plan]
    (out / "trades.json").write_text(json.dumps(keep, indent=1, default=str))
    print(json.dumps(res["verdict"], indent=1))
    for fold in ("dev", "eval"):
        h = res[fold]["H1"]
        print(fold, "H1", {k: h.get(k) for k in ("n", "meanPct", "grossPct", "p", "winRate")}, "| B3 paired", res[fold]["B3_priceOnlyPaired"],
              "| B2", {k: res[fold]["B2_allEvents"].get(k) for k in ("n", "meanPct")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
