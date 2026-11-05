"""Shared test harness: a full engine wired to the demo market, sim broker and a manual clock."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from dkapp.ai.engine import AIEngine
from dkapp.ai.providers import AIUnavailable, ProviderResult
from dkapp.broker.sim_broker import SimBroker
from dkapp.clock import ET, ManualClock, MarketCalendar
from dkapp.config import AppSettings
from dkapp.data.demo_data import DemoMarket, DemoMarketData
from dkapp.data.universe import build_universe
from dkapp.engine.trading_engine import TradingEngine
from dkapp.storage.db import Database


class ScriptedAI:
    """Test double for an AI provider: returns a fixed, schema-valid JSON answer."""
    name = "scripted"
    model = "test-double"
    is_paid = False

    def __init__(self, action="buy", confidence=0.8, quality="moderate", fail: Optional[str] = None, raw: Optional[str] = None):
        self.action, self.confidence, self.quality, self.fail, self.raw = action, confidence, quality, fail, raw
        self.calls = 0

    def health(self):
        return (self.fail is None), (self.fail or "ok")

    def complete_json(self, system, user, schema, max_tokens=1200):
        self.calls += 1
        if self.fail:
            raise AIUnavailable(self.fail)
        if self.raw is not None:
            text = self.raw
        elif "evidence_quality" not in schema.get("properties", {}):
            text = json.dumps({"action": "hold", "confidence": 0.5, "reasoning": "still trending"})
        else:
            text = json.dumps({"action": self.action, "confidence": self.confidence, "evidence_quality": self.quality,
                               "stop_loss_pct": 1.0, "take_profit_pct": 2.0, "max_hold_minutes": 45,
                               "reasoning": "volume and trend agree; risk is defined.", "key_risks": ["could fade"]})
        return ProviderResult(text, 100, 50, 0.0, 5, self.name, self.model)


@dataclass
class Rig:
    settings: AppSettings
    clock: ManualClock
    calendar: MarketCalendar
    market: DemoMarket
    data: DemoMarketData
    broker: SimBroker
    db: Database
    ai: AIEngine
    engine: TradingEngine

    def run_until(self, t: datetime, step_seconds: float = 10.0) -> None:
        while self.clock.now() < t:
            self.clock.advance(step_seconds)
            self.engine.step()
            # let background AI calls finish deterministically
            for p in list(self.engine.pending_ai.values()):
                p.future.result(timeout=10)
            for f in list(self.engine.pending_reviews.values()):
                f.result(timeout=10)


def make_rig(tmp_path, *, day: date = date(2026, 11, 4), start_offset_min: float = -5, universe_size: int = 80,
             decision_mode: str = "rules", provider=None, settings: Optional[AppSettings] = None,
             starting_cash: float = 100.0, mode: str = "demo", seed: int = 7) -> Rig:
    s = settings or AppSettings()
    s.ai.decision_mode = decision_mode
    s.data.universe_size = universe_size
    s.data.hotlist_size = 10
    s.data.full_scan_seconds = 60
    s.data.hot_scan_seconds = 30
    cal = MarketCalendar()
    sess = cal.session(day)
    clock = ManualClock(sess.open + timedelta(minutes=start_offset_min))
    uni, _ = build_universe(None, universe_size)
    market = DemoMarket(uni, cal, seed)
    data = DemoMarketData(market, clock)
    broker = SimBroker(market.quote, clock, cal, starting_cash=starting_cash, symbols=uni)
    db = Database(tmp_path / f"{mode}.db", mode)
    ai = AIEngine(s, db, provider or ScriptedAI(), now=clock.now)
    eng = TradingEngine(s, mode, broker, data, db, cal, clock, ai, universe=uni,
                        monotonic=lambda: clock.now().timestamp())
    eng.initialize()
    return Rig(s, clock, cal, market, data, broker, db, ai, eng)


def override_price(rig: "Rig", symbol: str, price: float, spread_bps: float = 4.0) -> None:
    """Force the demo market's live quote for one symbol (for both the broker and the data feed)."""
    from dkapp.models import Quote
    original = getattr(rig.market, "_orig_quote", None) or rig.market.quote
    rig.market._orig_quote = original
    overrides = getattr(rig.market, "_overrides", {})
    overrides[symbol] = (price, spread_bps)
    rig.market._overrides = overrides

    def quote(sym, t):
        if sym in rig.market._overrides:
            px, sp = rig.market._overrides[sym]
            half = px * sp / 2 / 10_000
            return Quote(px - half, px + half, 100.0, 100.0, t)
        return original(sym, t)

    rig.market.quote = quote
    rig.broker.quote_fn = quote


def make_signal(rig: "Rig", symbol: str, strategy: str = "orb", stop_pct: float = 1.0, target_pct: float = 2.0,
                max_hold: int = 60):
    from dkapp.models import Signal
    now = rig.clock.now()
    q = rig.market.quote(symbol, now)
    px = q.mid
    return Signal(symbol, strategy, now, px, px * (1 - stop_pct / 100), px * (1 + target_pct / 100), 0.8,
                  ["test signal"], max_hold, {})


def enter(rig: "Rig", symbol: str, **kw):
    eng = rig.engine
    sig = make_signal(rig, symbol, **kw)
    sid = rig.db.insert("signals", time=rig.clock.now().isoformat(), symbol=symbol, strategy=sig.strategy, outcome="test")
    eng._enter(sig, sid, None, rig.clock.now(), eng.session)
    return rig.db.query_one("SELECT * FROM trades WHERE signal_id=?", (sid,))
