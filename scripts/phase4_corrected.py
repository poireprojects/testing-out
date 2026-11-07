"""Phase 4 integrity corrections (closeout, 2026-11-07). Re-runs the frozen P4-H1 evaluation with three
corrections and writes to a separate folder. The original results (research/runs/2026-11-07-phase4/results.json,
trades.json) and scripts/phase4_test.py are left exactly as they were.

    ALPACA_PAPER_KEY=PK... python scripts/phase4_corrected.py fetch DATA_DIR   # 1-minute bars, only where needed
    python scripts/phase4_corrected.py run DATA_DIR OUT_DIR

The corrected numbers are computed on data that was already examined. They are NOT independent evidence and
cannot turn the NO-GO into anything else; they only show what the frozen rule measures without the defects.

C1  Stop timing. Entry is at 10:00:30, but the first 15-minute bar checked for the stop starts at 10:00:00, so a
    low printed in the 30 seconds before entry could stop the trade. Corrected: in the 10:00-10:15 window the stop
    is checked on 1-minute bars from 10:01 (fully after entry); from 10:15 on, 15-minute bars as before. The 10:00
    minute straddles entry: it is excluded in the main run and included (filled at the stop, never at the
    pre-entry open) in a sensitivity run. If the 15-minute bar never reached the stop, nothing changes.
C2  Fees. Each $20 trade was charged cent-rounded SEC, TAF and CAT fees as if it were the only trade that day.
    Corrected: each fee type is summed over the day's trades in the same set, the daily total is rounded up to a
    cent, and it is allocated back to that day's trades in proportion to their unrounded amounts.
C3  ML acceptance. The original flag "mlKept" was True because 2 selected trades both won (bootstrap p = 0).
    The pre-registration's decision rule calls fewer than 100 trades insufficient evidence; the ML test now
    needs the same minimum before it can count, and an unevaluable ML test enters Holm with p = 1.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import phase4_test as orig  # noqa: E402  (the frozen evaluation; imported, never modified)
from dkapp.clock import ET  # noqa: E402
from dkapp.quant.model import LogisticL2, Standardizer  # noqa: E402
from dkapp.research.stats import holm  # noqa: E402
from dkapp.risk.fees import CostModel  # noqa: E402

MIN_TRADES = 100                       # pre-registered criterion 1 and the INSUFFICIENT EVIDENCE threshold
MINUTE_FILE = "minute_entry_window.json"
_ceil_cent = orig._ceil_cent


# ------------------------------------------------------------------ data
class Data(orig.Data):
    """The original data plus 1-minute bars for 10:00-10:14 where the stop question arises."""

    def __init__(self, d: Path):
        super().__init__(d)
        p = d / MINUTE_FILE
        self.minute = json.loads(p.read_text()) if p.exists() else {}

    def minute_bars(self, s: str, day: str):
        """list of {"t": "HH:MM", "open", "low"} or None when never fetched (None = unknown, [] = no trades)."""
        return self.minute.get(f"{s}|{day}")


# ------------------------------------------------------------------ C1 + C2: one trade
def _stop_in_entry_window(minutes: list[dict], stop: float, include_entry_minute: bool):
    """first post-entry minute that reaches the stop -> fill price, else None."""
    for m in minutes:
        if m["t"] == "10:00":
            if include_entry_minute and m["low"] <= stop:
                return stop                    # the minute's open is before entry: never fill there
            continue
        if m["low"] <= stop:
            return min(stop, m["open"])
    return None


def trade(data: Data, s: str, dstr: str, cost_mult: float = 1.0, include_entry_minute: bool = False) -> dict:
    """The pre-registered trade with C1 (stop only after entry). Fees are left unrounded (feeParts) for C2."""
    day = date.fromisoformat(dstr)
    qe, qx = data.quote(s, dstr, "entry"), data.quote(s, dstr, "exit")
    if qe is None:
        return {"filled": False, "why": "no NBBO at entry"}
    hs_e = qe["halfSpreadBps"] * cost_mult
    slip = orig.SLIP_BPS * cost_mult
    entry_mid = qe["mid"]
    entry = entry_mid * (1 + (hs_e + slip) / 1e4)
    stop = entry * (1 - orig.STOP)
    b = data.bars(s, day)
    t10, t1015, t1545 = (datetime.combine(day, dtime(h, m), ET) for h, m in ((10, 0), (10, 15), (15, 45)))
    after = b[(b.index >= t10) & (b.index < t1545)] if not b.empty else b
    reason, exit_ref, hs_x, resolution = "15:45", None, None, "none needed"
    for t, r in after.iterrows():
        if r["low"] > stop:
            continue
        if t == t10:                           # the bar that starts 30 s before entry
            mins = data.minute_bars(s, dstr)
            if mins is None:                   # not fetched: keep the original 15-minute behaviour, flagged
                exit_ref, resolution = min(stop, float(r["open"])), "15min fallback"
            else:
                exit_ref, resolution = _stop_in_entry_window(mins, stop, include_entry_minute), "1min"
                if exit_ref is None:
                    continue                   # only a pre-entry print reached the stop: the trade lives on
        else:
            exit_ref = min(stop, float(r["open"]))
        reason, hs_x = "stop", hs_e
        break
    fallback = False
    if exit_ref is None:
        if qx is not None:
            exit_ref, hs_x = qx["mid"], qx["halfSpreadBps"] * cost_mult
        else:
            last = b[b.index >= t1545] if not b.empty else b
            if last.empty:
                return {"filled": False, "why": "no exit price"}
            exit_ref, hs_x, fallback = float(last["open"].iloc[0]), hs_e, True
    exit_ = exit_ref * (1 - (hs_x + slip) / 1e4)
    cm = CostModel.on(day)
    qty = orig.NOTIONAL / entry
    sale = orig.NOTIONAL * exit_ / entry
    sb, bb = cm.sale_breakdown(sale, qty), cm.buy_breakdown(qty)
    parts = {"sec": sb.sec, "taf": sb.taf, "cat": sb.cat + bb.cat}
    lone_fee = sum(_ceil_cent(v) for v in parts.values())          # what the original charged
    before = (exit_ / entry - 1) * 100
    return {"filled": True, "gross": (exit_ref / entry_mid - 1) * 100, "netBeforeFees": before,
            "net": before - lone_fee / orig.NOTIONAL * 100, "feesPct": lone_fee / orig.NOTIONAL * 100,
            "feeParts": parts, "feeBasis": "lone trade", "reason": reason, "stopResolution": resolution,
            "exitFallback": fallback, "entryHalfSpreadBps": qe["halfSpreadBps"], "askSize": qe.get("askSize"),
            "spreadSlipPct": (exit_ref / entry_mid - 1) * 100 - before}


def with_daily_fees(rows: list[dict | None]) -> list[dict | None]:
    """C2: copies of the trades with each fee type summed per day across this set, rounded up once per day, and
    allocated back in proportion to each trade's unrounded amount. Order and unfilled rows are preserved."""
    days: dict[str, dict[str, float]] = {}
    for r in rows:
        if r and r.get("filled"):
            tot = days.setdefault(r["day"], {"sec": 0.0, "taf": 0.0, "cat": 0.0})
            for k, v in r["feeParts"].items():
                tot[k] += v
    out = []
    for r in rows:
        if not (r and r.get("filled")):
            out.append(r)
            continue
        tot = days[r["day"]]
        fee = sum(_ceil_cent(tot[k]) * (r["feeParts"][k] / tot[k]) for k in tot if tot[k] > 0)
        out.append(r | {"net": r["netBeforeFees"] - fee / orig.NOTIONAL * 100, "feesPct": fee / orig.NOTIONAL * 100,
                        "feeBasis": "daily aggregate"})
    return out


# ------------------------------------------------------------------ C3: ML acceptance
def ml_accept(mlr: dict, holm_p: float, b2_mean: float, h1_mean: float, min_trades: int = MIN_TRADES) -> bool:
    """The pre-registered keep rule, plus the minimum trade count the original omitted."""
    n = mlr.get("n") or 0
    if n < min_trades:
        return False
    return mlr["meanPct"] > 0 and holm_p < 0.05 and mlr["meanPct"] > b2_mean and mlr["meanPct"] > h1_mean


# ------------------------------------------------------------------ fetch (C1 data, only where needed)
def needed_minutes(data: Data, plan: list[dict]) -> dict[str, set]:
    """(day -> symbols) whose 10:00 15-minute bar reaches the stop under 2x costs (the highest stop computed)."""
    need: dict[str, set] = {}

    def check(s, dstr):
        qe = data.quote(s, dstr, "entry")
        if qe is None:
            return
        day = date.fromisoformat(dstr)
        b = data.bars(s, day)
        if b.empty:
            return
        stop = qe["mid"] * (1 + (2 * qe["halfSpreadBps"] + 2 * orig.SLIP_BPS) / 1e4) * (1 - orig.STOP)
        first = b[b.index == datetime.combine(day, dtime(10, 0), ET)]
        if not first.empty and first["low"].iloc[0] <= stop:
            need.setdefault(dstr, set()).add(s)

    for r in plan:
        check(r["symbol"], r["day"])
        if r["H1"]:
            check(r["sector"], r["day"])
            if "B3" in r:
                check(r["B3"]["symbol"], r["day"])
            if "B4" in r:
                check(r["symbol"], r["B4"]["day"])
    return need


def fetch(data_dir: Path) -> None:
    import phase4_collect as col
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    hc, _ = col.clients()
    data = Data(data_dir)
    plan = json.loads((data_dir / "plan.json").read_text())
    need = needed_minutes(data, plan)
    path = data_dir / MINUTE_FILE
    got = json.loads(path.read_text()) if path.exists() else {}
    print(f"minute bars needed: {sum(map(len, need.values()))} symbol-days on {len(need)} days", flush=True)
    for k, dstr in enumerate(sorted(need)):
        syms = sorted(s for s in need[dstr] if f"{s}|{dstr}" not in got)
        if not syms:
            continue
        d = date.fromisoformat(dstr)
        col.guard(d)
        t0 = datetime(d.year, d.month, d.day, 10, 0, tzinfo=ET)
        col.pace()
        df = hc.get_stock_bars(StockBarsRequest(symbol_or_symbols=syms, start=t0, end=t0 + timedelta(minutes=15),
                                                timeframe=TimeFrame.Minute, feed=DataFeed.SIP)).df
        for s in syms:
            rows = []
            if df is not None and not df.empty and s in df.index.get_level_values(0):
                g = df.xs(s, level=0)
                for ts, r in g.iterrows():
                    ts = ts.tz_convert(ET)
                    if t0 <= ts < t0 + timedelta(minutes=15):
                        rows.append({"t": ts.strftime("%H:%M"), "open": float(r["open"]), "low": float(r["low"])})
            got[f"{s}|{dstr}"] = rows
        if k % 50 == 0:
            path.write_text(json.dumps(got))
            print(f"  {k + 1}/{len(need)} {dstr}", flush=True)
    path.write_text(json.dumps(got))
    print("fetched", len(got), "symbol-days", flush=True)


# ------------------------------------------------------------------ run
def evaluate(data: Data, plan: list[dict], include_entry_minute: bool = False, fee_fix: bool = True) -> dict:
    """Every pre-registered quantity, with C1 and (optionally) C2 applied. Each set is fee-aggregated on its own."""
    fx = with_daily_fees if fee_fix else (lambda rows: rows)
    T = lambda s, d, m=1.0: trade(data, s, d, m, include_entry_minute)  # noqa: E731
    for r in plan:
        r["t"] = T(r["symbol"], r["day"]) | {"day": r["day"], "react": r["react"]}
        r["t2"] = T(r["symbol"], r["day"], 2.0) | {"day": r["day"], "react": r["react"]}
        if r["H1"]:
            r["b1"] = T(r["sector"], r["day"]) | {"day": r["day"]}
            r["b3"] = (T(r["B3"]["symbol"], r["day"]) | {"day": r["day"]}) if "B3" in r else None
            r["b4"] = (T(r["symbol"], r["B4"]["day"]) | {"day": r["B4"]["day"]}) if "B4" in r else None
    res = {}
    for fold in ("dev", "eval"):
        P = [r for r in plan if r["fold"] == fold]
        H = [r for r in P if r["H1"]]
        h1, h1x2 = fx([r["t"] for r in H]), fx([r["t2"] for r in H])
        b2 = fx([r["t"] for r in P])
        b1, b3, b4 = fx([r["b1"] for r in H]), fx([r.get("b3") for r in H]), fx([r.get("b4") for r in H])
        b2_by_id = {id(r["t"]): x for r, x in zip(P, b2)}
        res[fold] = {
            "events": len(P), "H1": orig.summarize(h1), "H1_2xCosts": orig.summarize(h1x2),
            "B2_allEvents": orig.summarize(b2),
            "B1_sectorPaired": orig.paired(h1, b1), "B3_priceOnlyPaired": orig.paired(h1, b3),
            "B4_randomDayPaired": orig.paired(h1, b4),
            "B1_sector": orig.summarize(b1), "B3_priceOnly": orig.summarize([x for x in b3 if x]),
            "B4_randomDay": orig.summarize([x for x in b4 if x]),
            "halfYears": orig.half_years(h1),
            "bySurprise": {f"{a}/{b}": orig.summarize([b2_by_id[id(r["t"])] for r in P
                                                       if r["surprise"].get("eps") == a and r["surprise"].get("sales") == b])
                           for a in ("beat", "miss") for b in ("beat", "miss")},
            "capital": {f"${c:,}": orig.capital(h1, c) for c in (100, 1_000, 10_000)},
            "stopResolution": _count([r["t"] for r in H], "stopResolution"),
        }
        res[fold]["_h1rows"] = h1
        res[fold]["_b2rows"] = b2
    return res


def _count(rows, key):
    out: dict[str, int] = {}
    for r in rows:
        if r and r.get("filled") and r.get("reason") == "stop":
            out[r[key]] = out.get(r[key], 0) + 1
    return out


def run_ml(plan: list[dict], res: dict, fee_fix: bool = True) -> dict:
    """The same pre-registered model and features, trained on corrected development labels, applied once."""
    fx = with_daily_fees if fee_fix else (lambda rows: rows)
    lab = {id(r): x for fold in ("dev", "eval") for r, x in zip([p for p in plan if p["fold"] == fold], res[fold]["_b2rows"])}

    def feats(r):
        s = r["surprise"]
        return [float(s.get("eps") == "beat"), float(s.get("eps") == "miss"), float(s.get("sales") == "beat"),
                float(s.get("sales") == "miss"), r["react"], r.get("spyReact") or 0.0, r.get("volRatio") or 0.0,
                math.log(max(r["prevClose"], 1e-6)), math.log(max(r["adv20"], 1.0))]

    tr = [r for r in plan if r["fold"] == "dev" and r["t"].get("filled")]
    te = [r for r in plan if r["fold"] == "eval" and r["t"].get("filled")]
    ml = {"trainEvents": len(tr), "testEvents": len(te)}
    X = np.array([feats(r) for r in tr])
    st = Standardizer.fit(X)
    m = LogisticL2(lam=1.0).fit(st.transform(X), np.array([float(lab[id(r)]["net"] > 0) for r in tr]))
    p = m.predict(st.transform(np.array([feats(r) for r in te])))
    chosen = fx([r["t"] for r, q in zip(te, p) if q > 0.5])
    ml.update({"selected": len(chosen), "maxProbability": float(p.max()), "result": orig.summarize(chosen) if chosen else {"n": 0}})
    return ml


def verdict(res: dict, ml: dict) -> dict:
    ev, dv = res["eval"], res["dev"]
    mlr = ml.get("result") or {}
    ml_evaluable = (mlr.get("n") or 0) >= MIN_TRADES
    pv = {"H1 net > 0": ev["H1"].get("p", 1.0), "H1 beats price-only (B3)": ev["B3_priceOnlyPaired"].get("p", 1.0),
          "ML net > 0": mlr.get("p", 1.0) if ml_evaluable else 1.0}
    adj = holm(pv)
    hy = ev["halfYears"]
    crit = {
        "1. >= 100 filled trades": ev["H1"].get("n", 0) >= MIN_TRADES,
        "2. mean net > 0 (Holm p < 0.05)": ev["H1"].get("meanPct", -1) > 0 and adj["H1 net > 0"] < 0.05,
        "3. beats price-only comparable (Holm p < 0.05)": ev["B3_priceOnlyPaired"].get("meanExcessPct", -1) > 0 and adj["H1 beats price-only (B3)"] < 0.05,
        "4. beats sector ETF (point estimate)": ev["B1_sectorPaired"].get("meanExcessPct", -1) > 0,
        "5. positive in >= 3 of 4 half-years": sum(v["meanPct"] > 0 for v in hy.values()) >= 3,
        "6. positive with 2x costs": ev["H1_2xCosts"].get("meanPct", -1) > 0,
        "7. top-5 trades < 50% of profit": ev["H1"].get("top5Share") is not None and ev["H1"]["top5Share"] < 0.5,
        "8. development fold also > 0": dv["H1"].get("meanPct", -1) > 0}
    kept = ml_accept(mlr, adj["ML net > 0"], ev["B2_allEvents"]["meanPct"], ev["H1"].get("meanPct", 0))
    if ev["H1"].get("n", 0) < MIN_TRADES:
        decision = "INSUFFICIENT EVIDENCE"
    elif all(crit.values()):
        decision = "GO (forward shadow only)"
    else:
        decision = "NO-GO"
    return {"criteria": crit, "passed": sum(crit.values()), "holm": adj, "rawP": pv, "mlEvaluable": ml_evaluable,
            "mlKept": kept, "decision": decision}


def _strip(res):
    return {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} if isinstance(v, dict) else v for k, v in res.items()}


def main(data_dir: Path, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    data = Data(data_dir)
    load = lambda: json.loads((data_dir / "plan.json").read_text())  # noqa: E731
    original = json.loads((ROOT / "research/runs/2026-11-07-phase4/results.json").read_text())

    plan = load()
    res = evaluate(data, plan)
    ml = run_ml(plan, res)
    v = verdict(res, ml)
    # one correction at a time, for attribution (H1 mean net per trade)
    steps = {"original": {f: original[f]["H1"]["meanPct"] for f in ("dev", "eval")}}
    p2 = load()
    r_c1 = evaluate(data, p2, fee_fix=False)
    steps["C1 stop timing only"] = {f: r_c1[f]["H1"]["meanPct"] for f in ("dev", "eval")}
    steps["C1 + C2 (corrected)"] = {f: res[f]["H1"]["meanPct"] for f in ("dev", "eval")}
    p3 = load()
    r_s = evaluate(data, p3, include_entry_minute=True)
    sens = {f: {k: r_s[f]["H1"].get(k) for k in ("n", "meanPct", "ci90", "p", "stops")} for f in ("dev", "eval")}

    result = {
        "about": "Phase 4 P4-H1 with integrity corrections C1-C3 (see scripts/phase4_corrected.py). Previously "
                 "examined data: NOT independent evidence. The original results.json is unchanged.",
        "preregistration": "research/PREREGISTRATION-2026-11-07-phase4.md",
        "corrections": {
            "C1": "stop checked only on prices after the 10:00:30 entry (1-minute bars 10:01-10:14, then 15-minute bars)",
            "C2": "SEC/TAF/CAT fees summed per day within each trade set, each daily total rounded up to a cent once",
            "C3": f"ML acceptance requires >= {MIN_TRADES} selected trades; an unevaluable ML test enters Holm with p = 1"},
        "independentEvidence": False,
        "attribution_H1_meanNetPct": steps,
        "sensitivity_C1_includeEntryMinute": sens,
        **_strip(res), "ML": ml, "verdict": v,
        "originalVerdict": {"mlKept": original["verdict"]["mlKept"], "decision": original["verdict"]["decision"],
                            "passed": sum(original["verdict"]["criteria"].values())},
    }
    (out / "results_corrected.json").write_text(json.dumps(result, indent=1, default=str))
    b2net, h1net = {}, {}
    for f in ("dev", "eval"):
        P = [p for p in plan if p["fold"] == f]
        b2net.update({id(r): x.get("net") for r, x in zip(P, res[f]["_b2rows"])})
        h1net.update({id(r): x.get("net") for r, x in zip([p for p in P if p["H1"]], res[f]["_h1rows"])})
    keep = [{k: r[k] for k in ("symbol", "day", "fold", "H1", "react", "surprise", "sector")}
            | {"netH1Set": h1net.get(id(r)), "netAllEventsSet": b2net.get(id(r)), "filled": r["t"].get("filled"),
               "reason": r["t"].get("reason"), "stopResolution": r["t"].get("stopResolution")} for r in plan]
    (out / "trades_corrected.json").write_text(json.dumps(keep, indent=1, default=str))
    print(json.dumps({"attribution": steps, "sensitivity": sens, "verdict": v}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "fetch":
        fetch(Path(sys.argv[2]))
    else:
        raise SystemExit(main(Path(sys.argv[2]), Path(sys.argv[3])))
