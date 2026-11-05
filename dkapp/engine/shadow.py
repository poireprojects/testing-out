"""Hypothetical ("shadow") outcomes for every signal the AI reviewed.

When the AI approves or skips a setup, we follow that setup on paper with the same
stop / target / time rules and the same cost assumptions. Comparing approved vs.
skipped outcomes answers a question that matters: does the AI's judgment actually
add value over the rules alone? No orders are ever sent for shadow trades.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from ..models import Signal
from ..risk.fees import CostModel
from ..storage.db import Database, from_iso, to_iso


class ShadowTracker:
    def __init__(self, db: Database, costs: Optional[CostModel] = None, max_open: int = 60):
        self.db = db
        self.costs = costs or CostModel()
        self.max_open = max_open

    def open(self, signal: Signal, verdict: str, signal_id: Optional[int], now: datetime, spread_bps: float = 3.0) -> None:
        if self.db.scalar("SELECT COUNT(*) FROM shadow_trades WHERE status='open'", (), 0) >= self.max_open:
            return
        entry = self.costs.fill_price("buy", signal.price, spread_bps)
        self.db.insert("shadow_trades", signal_id=signal_id, symbol=signal.symbol, strategy=signal.strategy,
                       verdict=verdict, entry_time=to_iso(now), entry_price=entry, stop_price=signal.stop_price,
                       target_price=signal.target_price,
                       max_hold_until=to_iso(now + timedelta(minutes=signal.max_hold_minutes)), highest=entry,
                       status="open")

    def update(self, prices: dict[str, float], now: datetime, force_close: bool = False) -> int:
        closed = 0
        for t in self.db.query("SELECT * FROM shadow_trades WHERE status='open'"):
            px = prices.get(t["symbol"])
            if px is None and not force_close:
                continue
            px = px if px is not None else t["highest"]
            reason = None
            if px <= t["stop_price"]:
                reason, exit_px = "stop", t["stop_price"] * (1 - 0.0005)  # assume a little slippage through the stop
            elif px >= t["target_price"]:
                reason, exit_px = "target", t["target_price"]
            elif now >= (from_iso(t["max_hold_until"]) or now):
                reason, exit_px = "time", px
            elif force_close:
                reason, exit_px = "close", px
            if reason is None:
                if px > (t["highest"] or 0):
                    self.db.update("shadow_trades", t["id"], highest=px)
                continue
            exit_px = self.costs.fill_price("sell", exit_px, 3.0)
            ret = (exit_px / t["entry_price"] - 1) * 100
            self.db.update("shadow_trades", t["id"], status="closed", exit_time=to_iso(now), exit_price=exit_px,
                           exit_reason=reason, return_pct=ret)
            closed += 1
        return closed

    def open_symbols(self) -> list[str]:
        return [r["symbol"] for r in self.db.query("SELECT DISTINCT symbol FROM shadow_trades WHERE status='open'")]

    def summary(self) -> dict:
        out = {}
        for verdict in ("approved", "skipped"):
            rows = self.db.query("SELECT return_pct FROM shadow_trades WHERE status='closed' AND verdict=?", (verdict,))
            rets = [r["return_pct"] for r in rows if r["return_pct"] is not None]
            n = len(rets)
            out[verdict] = {
                "count": n,
                "avg_return_pct": sum(rets) / n if n else 0.0,
                "win_rate": sum(1 for r in rets if r > 0) / n if n else 0.0,
            }
        return out
