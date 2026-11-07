"""Builds and runs the trading backend for a mode (demo / paper / live) on a background thread."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import credentials, paths
from .ai.engine import AIEngine
from .broker.alpaca_broker import AlpacaBroker
from .broker.sim_broker import SimBroker
from .clock import ET, AcceleratedClock, MarketCalendar, RealClock, SessionState
from .config import AppSettings
from .data.alpaca_data import AlpacaMarketData
from .data.demo_data import DemoMarket, DemoMarketData
from .data.universe import build_universe
from .engine.trading_engine import TradingEngine
from .storage.db import Database

log = logging.getLogger(__name__)


class NotConfigured(Exception):
    pass


class DemoTimeline:
    """Keeps the demo simulator's clock moving through sessions and persists its state."""

    def __init__(self, db: Database, broker: SimBroker, clock: AcceleratedClock, calendar: MarketCalendar):
        self.db, self.broker, self.clock, self.calendar = db, broker, clock, calendar
        self._last_persist = 0.0
        self._closed_since: Optional[float] = None

    def __call__(self, engine: TradingEngine, sess: SessionState) -> None:
        now_wall = time.monotonic()
        if now_wall - self._last_persist > 5:
            self._last_persist = now_wall
            self.db.set_state("sim_broker", self.broker.dump_state())
            self.db.set_state("demo_sim_time", self.clock.now().isoformat())
        if sess.is_open:
            self._closed_since = None
            return
        if self._closed_since is None:
            self._closed_since = now_wall
        # after the close summary has been written, skip ahead to the next simulated session
        done = sess.phase != "after_close" or engine._summarized_day == (sess.session.day if sess.session else None)
        if done and now_wall - self._closed_since > 15:
            nxt = self.calendar.next_session(self.clock.now())
            if nxt.open <= self.clock.now():
                nxt = self.calendar.next_session(nxt.close + timedelta(minutes=1))
            self.clock.jump_to(nxt.open - timedelta(minutes=1))
            self._closed_since = None
            engine.emit("info", "system", "demo: skipping ahead to the next simulated session",
                        f"simulated time is now {nxt.day:%a %b %d} 9:29 ET")


def initial_demo_time(calendar: MarketCalendar, saved: Optional[str]) -> datetime:
    if saved:
        try:
            t = datetime.fromisoformat(saved)
            if t.tzinfo:
                return t
        except ValueError:
            pass
    now = datetime.now(timezone.utc)
    d = now.astimezone(ET).date()
    sess = calendar.session(d) or calendar.previous_session(now)
    return sess.open + timedelta(minutes=40)


class Runtime:
    def __init__(self, settings: AppSettings, mode: str):
        self.settings = settings.copy_deep()
        self.mode = mode
        self.engine: Optional[TradingEngine] = None
        self.db: Optional[Database] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.clock = None

    def build(self) -> TradingEngine:
        mode = self.mode
        db = Database(paths.database_file(mode), mode)
        self.db = db
        calendar = MarketCalendar()
        if mode == "demo":
            uni, _ = build_universe(None, self.settings.data.universe_size)
            clock = AcceleratedClock(initial_demo_time(calendar, db.get_state("demo_sim_time")),
                                     self.settings.demo.speed)
            market = DemoMarket(uni, calendar, self.settings.demo.seed)
            data = DemoMarketData(market, clock)
            broker = SimBroker(market.quote, clock, calendar, starting_cash=self.settings.risk.starting_capital,
                               symbols=uni, state=db.get_state("sim_broker"))
            ai = AIEngine(self.settings, db, now=clock.now)
            hook = DemoTimeline(db, broker, clock, calendar)
            # demo timers follow simulated time so faster demo speeds scan proportionally more often
            engine = TradingEngine(self.settings, mode, broker, data, db, calendar, clock, ai, universe=uni,
                                   after_step=hook, monotonic=lambda: clock.now().timestamp())
        else:
            keys = credentials.load_alpaca_keys(mode)
            if keys is None:
                raise NotConfigured(f"no Alpaca {mode} API keys saved — add them in settings")
            clock = RealClock()
            broker = AlpacaBroker(keys, paper=(mode == "paper"))
            data = AlpacaMarketData(keys, self.settings.data.feed, self.settings.data.requests_per_minute)
            ai = AIEngine(self.settings, db, now=clock.now)
            engine = TradingEngine(self.settings, mode, broker, data, db, calendar, clock, ai)
        self.clock = clock
        self.engine = engine
        return engine

    def start(self) -> None:
        if self.engine is None:
            self.build()
        self._thread = threading.Thread(target=self.engine.run, args=(self._stop,), name=f"stockpilot-engine-{self.mode}",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        if self.mode == "demo" and self.engine is not None and self.db is not None:
            try:
                self.db.set_state("sim_broker", self.engine.broker.dump_state())  # type: ignore[attr-defined]
                self.db.set_state("demo_sim_time", self.engine.clock.now().isoformat())
            except Exception:
                pass

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


def test_alpaca_connection(env: str, key_id: str, secret: str) -> tuple[bool, str]:
    """Checks the keys against the right Alpaca environment without saving anything."""
    from .credentials import AlpacaKeys
    try:
        b = AlpacaBroker(AlpacaKeys(key_id.strip(), secret.strip()), paper=(env == "paper"))
        ok, msg = b.verify_environment()
        if not ok:
            return False, msg
        acct = b.get_account()
        return True, f"{msg} · equity ${acct.equity:,.2f} · cash ${acct.cash:,.2f}"
    except Exception as exc:
        text = str(exc)
        if "401" in text or "unauthorized" in text.lower() or "forbidden" in text.lower():
            return False, "Alpaca rejected these keys — double-check them (and that they're for " + env + ")"
        return False, f"couldn't connect: {text[:200]}"
