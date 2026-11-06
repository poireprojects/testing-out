"""Forward research ledger: every eligible candidate the app sees on NEW market data — taken or rejected —
recorded at decision time, and its outcome computed later from bars that came after the decision.

Auditability:
* `forward_observations` is append-only (SQLite triggers refuse UPDATE and DELETE) and hash-chained:
  each row's hash covers its full payload and the previous row's hash, so editing any past prediction
  breaks every later hash (`verify()`).
* Outcomes live in a separate append-only table (`forward_outcomes`); computing an outcome never touches
  the observation, and only bars that START after the decision time are used.
* No fills are invented: outcomes are HYPOTHETICAL — a marketable entry at the open of the first bar after
  the decision, the plan's own exits, and the cost model's spread/slippage/fees. Broker paper fills live in
  `trades`; this ledger is never mixed with them.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Mapping, Optional

import numpy as np
import pandas as pd

from ..risk.fees import CostModel
from .stats import day_block_bootstrap

GENESIS = "0" * 64
FLATTEN_MIN = 380      # 15:50 ET
HORIZONS = (30, 60, 120)

SCHEMA = """
CREATE TABLE IF NOT EXISTS forward_observations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  time TEXT NOT NULL,              -- decision time (UTC)
  session_date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  hypothesis TEXT NOT NULL,        -- strategy id or research hypothesis id
  decision TEXT NOT NULL,          -- taken | rejected | observed
  payload TEXT NOT NULL,           -- canonical JSON: everything known at decision time
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_fwd_obs_day ON forward_observations(session_date);
CREATE TRIGGER IF NOT EXISTS fwd_obs_no_update BEFORE UPDATE ON forward_observations
BEGIN SELECT RAISE(ABORT, 'forward observations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS fwd_obs_no_delete BEFORE DELETE ON forward_observations
BEGIN SELECT RAISE(ABORT, 'forward observations are append-only'); END;
CREATE TABLE IF NOT EXISTS forward_outcomes(
  obs_id INTEGER NOT NULL REFERENCES forward_observations(id),
  plan TEXT NOT NULL,              -- plan | h30 | h60 | h120 | close | orb (hypothesis-specific)
  computed_at TEXT NOT NULL,
  filled INTEGER NOT NULL,         -- 0: the plan's entry condition never happened (no trade)
  entry_ref REAL, exit_ref REAL, exit_reason TEXT,
  gross_pct REAL, net_pct REAL, mfe_pct REAL, mae_pct REAL,
  PRIMARY KEY(obs_id, plan)
);
CREATE TRIGGER IF NOT EXISTS fwd_out_no_update BEFORE UPDATE ON forward_outcomes
BEGIN SELECT RAISE(ABORT, 'forward outcomes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS fwd_out_no_delete BEFORE DELETE ON forward_outcomes
BEGIN SELECT RAISE(ABORT, 'forward outcomes are append-only'); END;
"""


def _canon(obj) -> str:
    def clean(v):
        if isinstance(v, float):
            return None if not math.isfinite(v) else round(v, 8)
        if isinstance(v, dict):
            return {str(k): clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [clean(x) for x in v]
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        if isinstance(v, (np.floating, np.integer)):
            return clean(v.item())
        return v
    return json.dumps(clean(obj), sort_keys=True, separators=(",", ":"))


def _hash(prev: str, payload: str) -> str:
    return hashlib.sha256((prev + "|" + payload).encode()).hexdigest()


@dataclass
class Bars:
    """One symbol's 1-minute bars for a session, as arrays (minute = minutes after the open when the bar STARTS)."""
    start_min: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray

    @classmethod
    def from_df(cls, df: pd.DataFrame, session_open: datetime) -> "Bars":
        start = ((df.index - pd.Timestamp(session_open).tz_convert("UTC")).total_seconds() / 60.0).to_numpy()
        return cls(start, *(df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close")))


class ForwardLedger:
    def __init__(self, db):
        self.db = db
        self.db._conn.executescript(SCHEMA)

    # ------------------------------------------------------------ recording (decision time)
    def record(self, *, time: datetime, session_date: date, symbol: str, hypothesis: str, decision: str,
               payload: Mapping) -> int:
        """payload: everything known at decision time — reason, market, features, plan, prediction, costs,
        model/strategy versions. Nothing about the future may be in here."""
        body = _canon({"time": time, "session_date": session_date, "symbol": symbol, "hypothesis": hypothesis,
                       "decision": decision, **payload})
        with self.db._lock:
            row = self.db.query_one("SELECT hash FROM forward_observations ORDER BY id DESC LIMIT 1")
            prev = row["hash"] if row else GENESIS
            return self.db.insert("forward_observations", time=time.isoformat(), session_date=session_date.isoformat(),
                                  symbol=symbol, hypothesis=hypothesis, decision=decision, payload=body,
                                  prev_hash=prev, hash=_hash(prev, body))

    def verify(self) -> tuple[bool, Optional[int]]:
        """Recompute the chain. (True, None) if intact; else (False, first broken row id)."""
        prev = GENESIS
        for r in self.db.query("SELECT id, payload, prev_hash, hash FROM forward_observations ORDER BY id"):
            if r["prev_hash"] != prev or _hash(prev, r["payload"]) != r["hash"]:
                return False, r["id"]
            prev = r["hash"]
        return True, None

    # ------------------------------------------------------------ outcomes (after the fact)
    def days_needing_outcomes(self, before: date) -> list[date]:
        rows = self.db.query("SELECT DISTINCT o.session_date FROM forward_observations o LEFT JOIN forward_outcomes x "
                             "ON x.obs_id = o.id WHERE x.obs_id IS NULL AND o.session_date < ? ORDER BY 1",
                             (before.isoformat(),))
        return [date.fromisoformat(r["session_date"]) for r in rows]

    def observations_without_outcomes(self, day: date) -> list[dict]:
        rows = self.db.query("SELECT o.* FROM forward_observations o LEFT JOIN forward_outcomes x ON x.obs_id = o.id "
                             "WHERE x.obs_id IS NULL AND o.session_date = ? ORDER BY o.id", (day.isoformat(),))
        for r in rows:
            r["p"] = json.loads(r["payload"])
        return rows

    def compute_outcomes(self, day: date, bars: Mapping[str, Bars], session_open: datetime, costs: CostModel,
                         now: datetime) -> int:
        n = 0
        for r in self.observations_without_outcomes(day):
            b = bars.get(r["symbol"])
            if b is None or len(b.o) == 0:
                continue
            decided = (datetime.fromisoformat(r["time"]) - session_open).total_seconds() / 60.0
            for plan, res in outcomes_for(r["p"], b, decided, costs, day).items():
                self.db.insert("forward_outcomes", obs_id=r["id"], plan=plan, computed_at=now.isoformat(),
                               filled=int(res["filled"]), entry_ref=res.get("entry"), exit_ref=res.get("exit"),
                               exit_reason=res.get("reason"), gross_pct=res.get("gross"), net_pct=res.get("net"),
                               mfe_pct=res.get("mfe"), mae_pct=res.get("mae"))
            n += 1
        return n

    # ------------------------------------------------------------ reporting
    def summary(self, plan: str = "plan") -> dict:
        rows = self.db.query("SELECT o.id, o.session_date, o.hypothesis, o.decision, o.payload, x.net_pct, x.gross_pct, "
                             "x.filled FROM forward_observations o LEFT JOIN forward_outcomes x "
                             "ON x.obs_id = o.id AND x.plan = ? ORDER BY o.id", (plan,))
        groups: dict[tuple, list] = {}
        for r in rows:
            groups.setdefault((r["hypothesis"], r["decision"]), []).append(r)
        out = []
        for (hyp, dec), rs in sorted(groups.items()):
            done = [r for r in rs if r["net_pct"] is not None and r["filled"]]
            nets = [r["net_pct"] for r in done]
            bs = day_block_bootstrap(nets, [r["session_date"] for r in done]) if nets else None
            preds = []
            for r in done:
                pr = json.loads(r["payload"]).get("predicted") or {}
                if pr.get("expNetPct") is not None:
                    preds.append((pr["expNetPct"], r["net_pct"]))
            corr = None
            if len(preds) >= 10:
                a = np.array(preds)
                if a[:, 0].std() > 0 and a[:, 1].std() > 0:
                    corr = float(np.corrcoef(a[:, 0], a[:, 1])[0, 1])
            out.append({"hypothesis": hyp, "decision": dec, "observations": len(rs), "withOutcome": len(done),
                        "days": len({r["session_date"] for r in rs}),
                        "netPct": float(np.mean(nets)) if nets else None,
                        "grossPct": float(np.mean([r["gross_pct"] for r in done])) if done else None,
                        "ci90": [bs["lo"], bs["hi"]] if bs else None, "winRate": float(np.mean([v > 0 for v in nets])) if nets else None,
                        "predictionCorr": corr})
        ok, bad = self.verify()
        first = self.db.query_one("SELECT MIN(session_date) d, MAX(session_date) e, COUNT(*) n FROM forward_observations")
        return {"groups": out, "chainIntact": ok, "brokenAt": bad, "plan": plan,
                "from": first["d"] if first else None, "to": first["e"] if first else None,
                "observations": first["n"] if first else 0}


# ------------------------------------------------------------------ outcome rules (pure, testable)
def _fill(costs: CostModel, day: date, buy_ref: float, sell_ref: float, spread_bps: float, notional: float = 20.0) -> float:
    c = CostModel.on(day, slippage_bps=costs.slippage_bps, rounding=costs.rounding,
                     expected_sales_per_day=costs.expected_sales_per_day)
    buy, sell = c.fill_price("buy", buy_ref, spread_bps), c.fill_price("sell", sell_ref, spread_bps)
    qty = notional / buy
    fees = c.sell_fees(qty * sell, qty) + c.buy_fees(qty) + c.rounding_allowance()
    return (qty * (sell - buy) - fees) / notional * 100


def _walk(b: Bars, k: int, entry: float, stop: Optional[float], target: Optional[float], end_min: float) -> dict:
    """Hold from bar k (entry at its open): stop first inside a bar, gaps at the open, target only when traded
    through by 1 bp, exit at the open of the first bar at/after end_min."""
    mfe = mae = 0.0
    for j in range(k, len(b.o)):
        if b.start_min[j] >= end_min:
            return {"exit": float(b.o[j]), "reason": "time", "mfe": mfe, "mae": mae}
        if stop is not None and b.o[j] <= stop:
            return {"exit": float(b.o[j]), "reason": "stop (gap)", "mfe": mfe, "mae": min(mae, (b.o[j] / entry - 1) * 100)}
        if stop is not None and b.l[j] <= stop:
            return {"exit": float(stop), "reason": "stop", "mfe": mfe, "mae": min(mae, (stop / entry - 1) * 100)}
        if target is not None and b.h[j] >= target * 1.0001:
            return {"exit": float(target), "reason": "target", "mfe": max(mfe, (target / entry - 1) * 100), "mae": mae}
        mfe = max(mfe, (b.h[j] / entry - 1) * 100)
        mae = min(mae, (b.l[j] / entry - 1) * 100)
    return {"exit": float(b.c[-1]), "reason": "data end", "mfe": mfe, "mae": mae}


def outcomes_for(p: Mapping, b: Bars, decided_min: float, costs: CostModel, day: date) -> dict[str, dict]:
    """Outcomes under the observation's own plan and fixed horizons. Only bars that start after the decision."""
    plan = p.get("plan") or {}
    spread = float(plan.get("spreadBps", 3.0))
    k = int(np.searchsorted(b.start_min, decided_min, side="right"))   # first bar that STARTS after the decision
    out: dict[str, dict] = {}
    if k >= len(b.o) or b.start_min[k] >= FLATTEN_MIN:
        return {"plan": {"filled": False, "reason": "no bar after the decision before the cutoff"}}
    entry = float(b.o[k])
    kind = plan.get("kind", "marketable")
    if kind == "orb":   # buy-stop at the opening-range high, valid until a deadline
        trig, dl = float(plan["triggerPrice"]), float(plan.get("deadlineMin", 90))
        jj = None
        for j in range(k, len(b.o)):
            if b.start_min[j] >= dl:
                break
            if b.h[j] >= trig * 1.0001:
                jj = j
                break
        if jj is None:
            return {"plan": {"filled": False, "reason": "breakout never happened"}}
        k, entry = jj, max(trig, float(b.o[jj]))
    stop = plan.get("stopPrice")
    target = plan.get("targetPrice")
    max_hold = plan.get("maxHoldMin")
    end = FLATTEN_MIN if not max_hold else min(FLATTEN_MIN, b.start_min[k] + float(max_hold))
    w = _walk(b, k, entry, float(stop) if stop else None, float(target) if target else None, end)
    out["plan"] = {"filled": True, "entry": entry, **w, "gross": (w["exit"] / entry - 1) * 100,
                   "net": _fill(costs, day, entry, w["exit"], spread)}
    for hz in HORIZONS:
        wh = _walk(b, k, entry, None, None, min(FLATTEN_MIN, b.start_min[k] + hz))
        out[f"h{hz}"] = {"filled": True, "entry": entry, **wh, "gross": (wh["exit"] / entry - 1) * 100,
                         "net": _fill(costs, day, entry, wh["exit"], spread)}
    wc = _walk(b, k, entry, None, None, FLATTEN_MIN)
    out["close"] = {"filled": True, "entry": entry, **wc, "gross": (wc["exit"] / entry - 1) * 100,
                    "net": _fill(costs, day, entry, wc["exit"], spread)}
    return out
