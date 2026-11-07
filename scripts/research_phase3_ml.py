"""Phase 3 pre-registered ML step (H2-ML) — DEVELOPMENT DATA ONLY (1926-2005), walk-forward.

    python scripts/research_phase3_ml.py P3_DATA_DIR OUT_DIR

Question: does a small, interpretable model that decides WHEN to take the turn-of-the-month window add net
value over taking every window? Logistic regression (the app's L2 model) of "window beats cash after costs"
on three features known before entry (all measured at the close of trading day -3, the day before entry):
  * market trend: 12-month market return in excess of T-bills > 0
  * volatility tercile of the market's 63-day volatility (terciles from the TRAINING years only)
  * breadth: share of the 49 industries above their 200-day average
Refit every January on all earlier windows (expanding), first test year 1946. Hold the window when P > 0.5.
Kept only if the paired monthly improvement in net return has a bootstrap p < 0.05 (pre-registered).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dkapp.quant.model import LogisticL2, Standardizer  # noqa: E402
from dkapp.research.academic import load_series, market_total_return, price_index  # noqa: E402
from dkapp.research.factors import breadth  # noqa: E402
from dkapp.research.phase3 import tom_windows  # noqa: E402
from dkapp.research.portfolio import SimCosts  # noqa: E402
from dkapp.research.stats import auc, brier, day_block_bootstrap, moving_block_bootstrap  # noqa: E402


def main(data: Path, out: Path) -> int:
    ff3, ind49 = load_series(data, "ff3_daily"), load_series(data, "ind49_daily")
    ff3, ind49 = ff3.loc[:"2005-12-30"], ind49.loc[:"2005-12-30"]
    mkt, rf = market_total_return(ff3), ff3["RF"]
    idx = mkt.index
    pos = {d: i for i, d in enumerate(idx)}
    p = price_index(mkt.to_frame("m"))["m"]
    trend = p / p.shift(252) - 1.0 - rf.rolling(252).sum()
    vol = mkt.rolling(63).std() * np.sqrt(252)
    br = breadth(price_index(ind49), 200)
    cost = 2 * SimCosts().per_side                              # round trip per window (fees ~0 at $10k)
    rows = []
    for a, b in tom_windows(idx):
        i = pos[a]
        if i < 260:
            continue
        f = idx[i - 1]                                          # features known at the close of day -3
        gross = float((1 + mkt.iloc[i + 1: pos[b] + 1]).prod() - 1)
        rows.append({"entry": a, "year": a.year, "trend": float(trend.loc[f] > 0), "vol": float(vol.loc[f]),
                     "breadth": float(br.loc[f]), "net": gross - cost})
    df = pd.DataFrame(rows).dropna()
    preds = []
    for y in range(1946, 2006):
        tr, te = df[df.year < y], df[df.year == y]
        if te.empty:
            continue
        cuts = np.quantile(tr.vol, [1 / 3, 2 / 3])
        def X(d):
            v = np.digitize(d.vol.values, cuts)                 # 0 calm, 1 normal, 2 volatile (training cut-offs)
            return np.column_stack([d.trend.values, (v == 1).astype(float), (v == 2).astype(float), d.breadth.values])
        st = Standardizer.fit(X(tr))
        m = LogisticL2(lam=1.0).fit(st.transform(X(tr)), (tr.net > 0).astype(float).values)
        pr = m.predict(st.transform(X(te)))
        for (_, r), q in zip(te.iterrows(), pr):
            preds.append({"entry": r.entry, "p": float(q), "net": r.net, "win": int(r.net > 0)})
    pdf = pd.DataFrame(preds)
    take_all = pdf.net.values
    take_ml = np.where(pdf.p > 0.5, pdf.net.values, 0.0)
    diff = take_ml - take_all
    days = [d.date() for d in pdf.entry]
    base_rate = float(pdf.win.mean())
    res = {"testWindows": int(len(pdf)), "years": "1946-2005 (walk-forward, refit every January)",
           "heldShare": float((pdf.p > 0.5).mean()),
           "netPerMonthPct": {"allWindows": float(take_all.mean() * 100), "mlFiltered": float(take_ml.mean() * 100)},
           "sharpePerMonth": {"allWindows": float(take_all.mean() / take_all.std(ddof=1)),
                              "mlFiltered": float(take_ml.mean() / take_ml.std(ddof=1))},
           "improvement": day_block_bootstrap(list(diff), days) | {"p12moBlocks": moving_block_bootstrap(list(diff), 12)["p"]},
           "directional": {"auc": auc(list(pdf.p), list(pdf.win)), "brier": brier(list(pdf.p), list(pdf.win)),
                           "brierBaseRate": brier([base_rate] * len(pdf), list(pdf.win)), "baseRate": base_rate},
           "skippedWindowsNetPct": float(pdf.net[pdf.p <= 0.5].mean() * 100) if (pdf.p <= 0.5).any() else None}
    res["kept"] = bool(res["improvement"]["mean"] > 0 and res["improvement"]["p"] < 0.05)
    (out / "dev_ml.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
