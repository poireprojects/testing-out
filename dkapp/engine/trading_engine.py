"""The autonomous trading engine.

Runs on its own thread. One `step()` per tick (~2 s):

  reconcile with broker → process order fills → session housekeeping →
  scan (full universe ~60 s, hot list ~15 s) → strategy signals →
  AI review (background) → deterministic risk check → orders →
  manage open positions (stops, targets, trailing, time, strategy exits, AI review) →
  end-of-day flatten → snapshot for the UI.

Safety properties:
* New positions are only opened when every risk rule passes. Exits are always allowed.
* Each position gets a broker-side stop order where the broker accepts one, so it stays
  protected if this app, the network or the laptop stops. Otherwise the app watches
  the stop itself and the UI says so.
* On restart, local state is reconciled against the broker before anything else.
"""
from __future__ import annotations

import json
import logging
import math
import queue
import threading
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from ..ai.engine import AIEngine, AIResult
from ..broker.base import Broker, BrokerError, BrokerUnavailable
from ..clock import ET, Clock, MarketCalendar, SessionState, session_state
from ..config import AppSettings, live_authorization_status
from ..data.base import DataUnavailable, MarketData
from ..data.universe import CONTEXT_SYMBOLS, build_universe
from ..execution.order_manager import FillDelta, OrderManager, TERMINAL
from ..models import AccountInfo, AssetInfo, BrokerPosition, Quote, Signal
from ..risk.engine import EntryProposal, OpenRisk, RiskContext, RiskEngine, floor_qty, round_price
from ..risk.fees import CostModel
from ..risk.settlement import SettlementLedger
from ..risk.sizing import scaled_limits
from ..scanner import Scanner, load_baselines
from ..analysis.features import Baseline
from ..storage.db import Database, from_iso, to_iso, utcnow_iso
from ..decision import Candidate, Decision, Evidence, OpportunityRanker, Regime, breadth_pct, classify_regime
from ..strategies import ALL_STRATEGIES
from .shadow import ShadowTracker

log = logging.getLogger(__name__)
EPS = 1e-7


@dataclass
class Health:
    ok: Optional[bool] = None
    message: str = "starting…"
    last_ok: Optional[datetime] = None
    failures: int = 0

    def good(self, msg: str, now: datetime) -> None:
        self.ok, self.message, self.last_ok, self.failures = True, msg, now, 0

    def bad(self, msg: str) -> None:
        self.ok, self.message, self.failures = False, msg, self.failures + 1

    def to_dict(self) -> dict:
        return {"ok": self.ok, "message": self.message, "lastOk": to_iso(self.last_ok)}


@dataclass
class Timers:
    reconcile: float = 0.0
    full_scan: float = 0.0
    hot_scan: float = 0.0
    quotes: float = 0.0
    equity: float = 0.0
    ai_health: float = 0.0
    persist: float = 0.0


@dataclass
class PendingAI:
    future: Future
    signal: Signal
    signal_id: int
    spread_bps: float


class TradingEngine:
    TICK_SECONDS = 2.0

    def __init__(self, settings: AppSettings, mode: str, broker: Broker, data: MarketData, db: Database,
                 calendar: MarketCalendar, clock: Clock, ai: AIEngine, *, cost_model: Optional[CostModel] = None,
                 universe: Optional[list[str]] = None, after_step: Optional[Callable[["TradingEngine", SessionState], None]] = None,
                 monotonic: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.mode = mode
        self.broker = broker
        self.data = data
        self.db = db
        self.calendar = calendar
        self.clock = clock
        self.ai = ai
        self.costs = cost_model or CostModel()
        self.risk = RiskEngine(settings, self.costs)
        self.orders = OrderManager(db, broker, clock.now, mode)
        self.ledger = SettlementLedger(db, calendar)
        self.shadow = ShadowTracker(db, self.costs)
        self.ranker = OpportunityRanker(self.costs)
        self.regime: Regime = classify_regime(None, None, None, None)
        self.last_decision: Optional[Decision] = None
        self._last_journal: tuple = ()
        self._last_pass_note: Optional[datetime] = None
        self._shadowed: dict[tuple[str, str, str], datetime] = {}
        self.pending_select: Optional[tuple[Future, dict[str, Candidate], datetime]] = None
        self.after_step = after_step
        self._mono = monotonic

        self.universe: list[str] = list(universe or [])
        self.assets: dict[str, AssetInfo] = {}
        self.scanner = Scanner(data, settings, self.universe)
        self.health = {k: Health() for k in ("broker", "data", "ai", "risk")}
        self.timers = Timers()
        self.account: Optional[AccountInfo] = None
        self.broker_positions: dict[str, BrokerPosition] = {}
        self.external_positions: list[BrokerPosition] = []
        self.quotes: dict[str, tuple[Quote, float, datetime]] = {}
        self.pending_ai: dict[str, PendingAI] = {}
        self.pending_reviews: dict[int, Future] = {}
        self.signals_today = 0
        self.initialized = False
        self.init_error: Optional[str] = None
        self._session_day: Optional[date] = None
        self._summarized_day: Optional[date] = None
        self._daily_limit_day: Optional[date] = None
        self._software_stop_warned: set[int] = set()
        self._stop_synced: dict[int, float] = {}
        self._last_signal_logged: dict[tuple[str, str], datetime] = {}
        self._last_error_at = 0.0
        self._events: deque = deque(maxlen=500)
        self._event_seq = 0
        self._commands: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        self._snap_lock = threading.Lock()
        self._snapshot: dict = {}
        self.emergency_stop = bool(db.get_state("emergency_stop", False))
        self.session: Optional[SessionState] = None

    # ================================================================ events
    def emit(self, level: str, category: str, title: str, message: str = "", symbol: Optional[str] = None,
             notify: bool = False, ref_id: Optional[int] = None) -> None:
        now = self.clock.now()
        eid = self.db.log_event(level, category, title, message, symbol, ref_id, time=now)
        self._event_seq += 1
        self._events.append({"id": eid, "seq": self._event_seq, "time": to_iso(now), "level": level,
                             "category": category, "title": title, "message": message, "symbol": symbol or "",
                             "notify": notify})
        (log.warning if level in ("warning", "error") else log.info)("[%s] %s %s", category, title, message)

    def drain_events(self, after_seq: int) -> list[dict]:
        return [e for e in list(self._events) if e["seq"] > after_seq]

    # ================================================================ commands (thread-safe)
    def command(self, name: str, **kwargs) -> None:
        self._commands.put((name, kwargs))

    def _drain_commands(self) -> None:
        while True:
            try:
                name, kw = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                if name == "emergency_stop":
                    self._do_emergency_stop()
                elif name == "reset_emergency":
                    self.emergency_stop = False
                    self.db.set_state("emergency_stop", False)
                    self.emit("info", "risk", "emergency stop reset", "trading can resume (if autopilot is on)")
                elif name == "settings":
                    self.apply_settings(kw["settings"])
                elif name == "close_position":
                    t = self.db.trade(int(kw["trade_id"]))
                    if t and t["status"] in ("open", "pending_entry"):
                        self._begin_exit(t, "closed by you from the app")
                elif name == "reconcile":
                    self.timers.reconcile = 0.0
                elif name == "check_ai":
                    self.timers.ai_health = 0.0
                elif name == "call":
                    kw["fn"](self)
            except Exception:
                log.exception("command %s failed", name)

    def apply_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        self.risk.settings = settings
        self.scanner.settings = settings
        self.ai.settings = settings

    # ================================================================ lifecycle
    def initialize(self) -> None:
        now = self.clock.now()
        today = now.astimezone(ET).date()
        try:
            days = self.broker.get_calendar(today - timedelta(days=10), today + timedelta(days=45))
            if days:
                self.calendar.load(days, today - timedelta(days=10), today + timedelta(days=45))
            assets = None
            if not self.data.is_demo:
                assets = self.broker.get_assets()
                self.assets = {a.symbol: a for a in assets}
            if not self.universe:
                extra = self._most_active_symbols()
                self.universe, rejected = build_universe(assets, self.settings.data.universe_size, extra)
                if rejected:
                    log.info("universe: %d symbols excluded (%s…)", len(rejected), list(rejected.items())[:5])
            self.scanner.universe = self.universe
            self._load_baselines(now)
            self.health["broker"].good(f"connected · {self.broker.name}", now)
        except BrokerUnavailable as exc:
            self.health["broker"].bad(str(exc))
            self.init_error = str(exc)
            raise
        self.recover(now)
        self.initialized = True
        self.init_error = None
        self.emit("info", "system", "engine started",
                  f"{self.mode} mode · scanning {len(self.universe)} symbols · data: {self.data.feed_label}")

    def _most_active_symbols(self) -> list[str]:
        return []  # optional hook; the curated universe already covers the most liquid names

    def _load_baselines(self, now: datetime) -> None:
        today = now.astimezone(ET).date().isoformat()
        rows = self.db.query("SELECT * FROM volume_baseline WHERE as_of=?", (today,))
        if len(rows) >= len(self.universe) * 0.8:
            self.scanner.baselines = {r["symbol"]: Baseline(r["avg_daily_volume"], r["prev_close"], r["avg_range_pct"])
                                      for r in rows}
            return
        bl = load_baselines(self.data, self.universe, now)
        if bl:
            with self.db.transaction() as db:
                for sym, b in bl.items():
                    db._conn.execute(
                        "INSERT OR REPLACE INTO volume_baseline(symbol, avg_daily_volume, prev_close, avg_range_pct, as_of)"
                        " VALUES(?,?,?,?,?)", (sym, b.avg_daily_volume, b.prev_close, b.avg_range_pct, today))
            self.scanner.baselines = bl
        self.health["data"].good(f"{len(bl)} symbols with volume history", now)

    def recover(self, now: datetime) -> None:
        """Restart recovery: resolve unconfirmed orders, then reconcile positions."""
        unresolved = [o for o in self.db.active_orders()]
        for o in unresolved:
            try:
                _, delta = self.orders.sync(o)
                if delta:
                    self._on_fill(delta, now)
            except BrokerUnavailable:
                pass
        if unresolved:
            self.emit("info", "system", "recovered order state",
                      f"checked {len(unresolved)} order(s) that were open when the app last stopped")
        self.reconcile(now, force=True)

    # ================================================================ main loop
    def run(self, stop: threading.Event) -> None:
        backoff = 5.0
        while not stop.is_set() and not self.initialized:
            try:
                self.initialize()
            except Exception as exc:
                log.warning("engine init failed: %s", exc)
                self.init_error = str(exc)
                self._publish_snapshot(self.clock.now())
                stop.wait(backoff)
                backoff = min(60.0, backoff * 2)
        while not stop.is_set():
            t0 = self._mono()
            try:
                self.step()
            except Exception as exc:  # keep running; never die silently
                log.exception("engine step failed")
                if self._mono() - self._last_error_at > 120:
                    self._last_error_at = self._mono()
                    self.emit("error", "system", "something went wrong", str(exc)[:300], notify=True)
            stop.wait(max(0.05, self.TICK_SECONDS - (self._mono() - t0)))
        self.shutdown()

    def shutdown(self) -> None:
        self.ai.shutdown()
        self.emit("info", "system", "engine stopped", "open positions keep their broker-side stop orders (if any)")

    def _due(self, name: str, seconds: float) -> bool:
        now = self._mono()
        if now - getattr(self.timers, name) >= seconds:
            setattr(self.timers, name, now)
            return True
        return False

    def step(self) -> None:
        self._drain_commands()
        now = self.clock.now()
        r = self.settings.risk
        sess = session_state(self.calendar, now, no_entries_first_minutes=r.no_entries_first_minutes,
                             last_entry_minutes_before_close=r.last_entry_minutes_before_close,
                             flatten_minutes_before_close=r.flatten_minutes_before_close)
        self.session = sess
        self.broker.tick(now)

        active_orders = bool(self.db.active_orders())
        if self._due("reconcile", 4.0 if active_orders else 15.0):
            self.reconcile(now)
        self.process_orders(now)

        if sess.is_open and sess.session is not None:
            if self._session_day != sess.session.day:
                self._on_new_session(now, sess)
            if self._due("quotes", 5.0):
                self._refresh_quotes(now)
            if self._due("full_scan", self.settings.data.full_scan_seconds):
                self._full_scan(now, sess)
            if self._due("hot_scan", self.settings.data.hot_scan_seconds):
                self._hot_scan_and_signals(now, sess)
            self._collect_ai(now, sess)
            self._collect_selection(now, sess)
            self._manage_positions(now, sess)
            self._check_daily_limit(now)
            if self._due("equity", 60.0):
                self._snapshot_equity(now, sess)
        else:
            if sess.session is not None and sess.phase == "after_close" and self._summarized_day != sess.session.day:
                self._on_session_end(now, sess)
        if self._due("ai_health", 60.0):
            self._check_ai_health(now)
        self._update_risk_health(now)
        self._publish_snapshot(now)
        if self.after_step:
            self.after_step(self, sess)

    # ================================================================ broker sync
    def reconcile(self, now: datetime, force: bool = False) -> None:
        try:
            acct = self.broker.get_account()
            positions = self.broker.get_positions()
        except BrokerUnavailable as exc:
            self.health["broker"].bad(f"can't reach the brokerage: {exc}")
            return
        except BrokerError as exc:
            self.health["broker"].bad(str(exc))
            return
        self.health["broker"].good(f"connected · {self.broker.name}", now)
        self.account = acct
        self.broker_positions = {p.symbol: p for p in positions}
        managed = self.db.open_trades()
        managed_syms = {t["symbol"] for t in managed}
        self.external_positions = [p for p in positions if p.symbol not in managed_syms]

        for t in managed:
            if t["status"] not in ("open", "closing"):
                continue
            remaining = t["entry_qty"] - t["exit_qty"]
            bp = self.broker_positions.get(t["symbol"])
            bq = bp.qty if bp else 0.0
            if bq + 1e-6 < remaining:
                self._sync_trade_orders(t["id"], now)  # maybe a stop/exit filled
                t = self.db.trade(t["id"])
                if t["status"] == "closed":
                    continue
                remaining = t["entry_qty"] - t["exit_qty"]
                if bq + 1e-6 < remaining:
                    missing = remaining - bq
                    px = bp.current_price if bp else self._price(t["symbol"]) or t["entry_price"]
                    self.emit("warning", "trade", f"{t['symbol']} was sold outside the app",
                              f"{missing:.6f} shares are no longer at the broker; recording the exit at about ${px:.2f}",
                              t["symbol"], notify=True, ref_id=t["id"])
                    self._record_exit_fill(t, missing, px, now, "closed outside the app (or by the broker)")

    def _sync_trade_orders(self, trade_id: int, now: datetime) -> None:
        for o in self.db.orders_for_trade(trade_id):
            if o["status"] in TERMINAL and o["status"] != "pending_submit":
                continue
            try:
                _, delta = self.orders.sync(o)
            except BrokerUnavailable:
                return
            if delta:
                self._on_fill(delta, now)

    STOP_SYNC_SECONDS = 10.0

    def process_orders(self, now: datetime) -> None:
        closing = {t["id"] for t in self.db.open_trades() if t["status"] == "closing"}
        for o in self.db.active_orders():
            # Resting protective stops rarely change: check them every 10 s (a fill is also caught by
            # reconciliation), but entries/exits and anything on a closing trade every tick.
            if o["purpose"] == "stop" and o["trade_id"] not in closing and o["status"] != "pending_submit":
                last = self._stop_synced.get(o["id"], -1e18)
                if self._mono() - last < self.STOP_SYNC_SECONDS:
                    continue
                self._stop_synced[o["id"]] = self._mono()
            try:
                _, delta = self.orders.sync(o)
            except BrokerUnavailable as exc:
                self.health["broker"].bad(f"can't reach the brokerage: {exc}")
                return
            if delta:
                self._on_fill(delta, now)
        for t in self.db.open_trades():
            self._advance_trade(t, now)

    # ================================================================ fills & trade state
    def _on_fill(self, d: FillDelta, now: datetime) -> None:
        if d.trade_id is None:
            return
        t = self.db.trade(d.trade_id)
        if t is None:
            return
        if d.purpose == "entry":
            qty = t["entry_qty"] + d.qty
            px = (t["entry_price"] or 0.0) * t["entry_qty"] / qty + d.price * d.qty / qty
            self.db.update("trades", t["id"], entry_qty=qty, entry_price=px, entry_notional=qty * px,
                           entry_time=t["entry_time"] or to_iso(d.time), highest_price=max(px, t["highest_price"] or 0))
            self.emit("success", "trade", f"bought {t['symbol']}",
                      f"{d.qty:.6f} shares at ${d.price:.2f} (${d.qty * d.price:.2f}) · {t['strategy']}",
                      t["symbol"], notify=True, ref_id=t["id"])
        else:
            reason = None
            if d.purpose == "stop" and not t["exit_reason"]:
                reason = "stop-loss hit (broker stop order)"
            self._record_exit_fill(t, d.qty, d.price, d.time, reason)

    def _record_exit_fill(self, t: dict, qty: float, price: float, when: datetime, reason: Optional[str]) -> None:
        proceeds = qty * price
        fees = self.costs.sell_fees(proceeds, qty)
        self.ledger.record_sale(t["id"], proceeds - fees, self.clock.now().astimezone(ET).date())
        exit_qty = t["exit_qty"] + qty
        exit_px = ((t["exit_price"] or 0.0) * t["exit_qty"] + proceeds) / exit_qty
        fields = dict(exit_qty=exit_qty, exit_price=exit_px, fees=(t["fees"] or 0.0) + fees, exit_time=to_iso(when))
        if reason and not t["exit_reason"]:
            fields["exit_reason"] = reason
        self.db.update("trades", t["id"], **fields)
        t = self.db.trade(t["id"])
        if t["entry_qty"] - t["exit_qty"] <= EPS:
            pnl = (t["exit_price"] - t["entry_price"]) * t["exit_qty"] - t["fees"]
            self.db.update("trades", t["id"], status="closed", realized_pnl=pnl)
            # cancel anything left for this trade (e.g. the protective stop after a manual exit)
            for o in self.orders.active(trade_id=t["id"]):
                try:
                    self.orders.cancel(o)
                except BrokerError:
                    pass
            sign = "+" if pnl >= 0 else "−"
            self.emit("success" if pnl >= 0 else "warning", "trade", f"sold {t['symbol']} · {sign}${abs(pnl):.2f}",
                      f"{t['exit_reason'] or 'closed'} · entry ${t['entry_price']:.2f} → exit ${t['exit_price']:.2f}",
                      t["symbol"], notify=True, ref_id=t["id"])

    def _advance_trade(self, t: dict, now: datetime) -> None:
        tid = t["id"]
        active = self.orders.active(trade_id=tid)
        if t["status"] == "pending_entry":
            entry_active = [o for o in active if o["purpose"] == "entry"]
            if entry_active:
                o = entry_active[0]
                created = from_iso(o["created_at"]) or now
                if (now - created).total_seconds() > self.settings.risk.entry_timeout_seconds \
                        and o["status"] != "pending_cancel":
                    try:
                        self.orders.cancel(o)
                    except BrokerError:
                        pass
                return
            t = self.db.trade(tid)
            if t["entry_qty"] > EPS:
                self.db.update("trades", tid, status="open")
                self._ensure_protection(self.db.trade(tid), now)
            else:
                last = self.db.query_one("SELECT * FROM orders WHERE trade_id=? AND purpose='entry' ORDER BY id DESC", (tid,))
                why = (last or {}).get("reject_reason") or (last or {}).get("status") or "not filled"
                self.db.update("trades", tid, status="canceled", exit_reason=f"entry not filled: {why}")
                level = "warning" if (last or {}).get("status") == "rejected" else "info"
                self.emit(level, "trade", f"{t['symbol']} entry not filled", str(why)[:300], t["symbol"],
                          notify=level == "warning", ref_id=tid)
            return
        if t["status"] == "open":
            self._ensure_protection(t, now)
            return
        if t["status"] == "closing":
            self._advance_closing(t, now)

    def _ensure_protection(self, t: dict, now: datetime) -> None:
        if t["software_stop"] or t["status"] != "open":
            return
        remaining = t["entry_qty"] - t["exit_qty"]
        if remaining <= EPS:
            return
        if self.orders.active(trade_id=t["id"], purpose="stop"):
            return
        if not (self.session and self.session.is_open):
            return
        bp = self.broker_positions.get(t["symbol"])
        qty = floor_qty(min(remaining, bp.qty_available if bp else remaining), 9)
        if qty <= EPS:
            return
        row = self.orders.place(t["id"], "stop", dict(symbol=t["symbol"], side="sell", type="stop", qty=qty,
                                                      stop_price=round_price(t["stop_price"])))
        if row["status"] == "rejected":
            self.db.update("trades", t["id"], software_stop=1)
            if t["id"] not in self._software_stop_warned:
                self._software_stop_warned.add(t["id"])
                self.emit("warning", "risk", f"{t['symbol']}: broker stop order not accepted",
                          f"the app will watch the stop at ${t['stop_price']:.2f} itself. If the app or computer "
                          f"stops, this position is not protected. ({row.get('reject_reason') or ''})"[:400],
                          t["symbol"], notify=True, ref_id=t["id"])

    def _begin_exit(self, t: dict, reason: str) -> None:
        if t["status"] == "pending_entry":
            for o in self.orders.active(trade_id=t["id"], purpose="entry"):
                try:
                    self.orders.cancel(o)
                except BrokerError:
                    pass
            if not t["exit_reason"]:
                self.db.update("trades", t["id"], exit_reason=reason)
            return
        if t["status"] not in ("open", "closing"):
            return
        self.db.update("trades", t["id"], status="closing", exit_reason=t["exit_reason"] or reason)
        self.emit("info", "decision", f"selling {t['symbol']}", reason, t["symbol"], ref_id=t["id"])
        self._advance_closing(self.db.trade(t["id"]), self.clock.now())

    def _advance_closing(self, t: dict, now: datetime) -> None:
        if t["status"] != "closing":
            return
        remaining = t["entry_qty"] - t["exit_qty"]
        if remaining <= EPS:
            return
        stops = self.orders.active(trade_id=t["id"], purpose="stop")
        for o in stops:
            try:
                self.orders.cancel(o)
                _, delta = self.orders.sync(o)
                if delta:
                    self._on_fill(delta, now)
            except BrokerError:
                return
        if self.orders.active(trade_id=t["id"], purpose="stop"):
            return  # waiting for the cancel to complete
        t = self.db.trade(t["id"])
        remaining = t["entry_qty"] - t["exit_qty"]
        if remaining <= EPS:
            return
        exits = self.orders.active(trade_id=t["id"], purpose="exit")
        if exits:
            o = exits[0]
            created = from_iso(o["created_at"]) or now
            if (now - created).total_seconds() > 60 and o["status"] not in ("pending_cancel",):
                try:
                    self.orders.cancel(o)
                except BrokerError:
                    pass
            return
        if not (self.session and self.session.is_open):
            return  # market orders only during the session; retried at the next open
        attempts = int(self.db.scalar("SELECT COUNT(*) FROM orders WHERE trade_id=? AND purpose='exit'", (t["id"],), 0))
        if attempts >= 6:
            if attempts == 6:
                self.emit("error", "trade", f"can't sell {t['symbol']}",
                          "6 exit attempts failed — please check the position at your broker", t["symbol"],
                          notify=True, ref_id=t["id"])
                self.db.insert("orders", trade_id=t["id"], purpose="exit", client_order_id=f"giveup-{t['id']}",
                               symbol=t["symbol"], side="sell", type="market", status="abandoned",
                               created_at=utcnow_iso())
            return
        bp = self.broker_positions.get(t["symbol"])
        qty = remaining if bp is None else min(remaining, bp.qty_available if bp.qty_available > 0 else bp.qty)
        qty = floor_qty(qty, 9)
        if qty <= EPS:
            self.timers.reconcile = 0.0
            return
        row = self.orders.place(t["id"], "exit", dict(symbol=t["symbol"], side="sell", type="market", qty=qty))
        if row["status"] == "rejected":
            self.emit("warning", "trade", f"sell order for {t['symbol']} rejected", row.get("reject_reason") or "",
                      t["symbol"], ref_id=t["id"])
            self.timers.reconcile = 0.0
        else:
            _, delta = self.orders.sync(row)
            if delta:
                self._on_fill(delta, now)

    def _move_stop(self, t: dict, new_stop: float, now: datetime) -> None:
        new_stop = round_price(new_stop)
        if new_stop <= (t["stop_price"] or 0) * 1.0015:
            return  # only meaningful raises; stops never move down
        self.db.update("trades", t["id"], stop_price=new_stop)
        if t["software_stop"]:
            return
        for o in self.orders.active(trade_id=t["id"], purpose="stop"):
            try:
                self.orders.cancel(o)
                _, delta = self.orders.sync(o)
                if delta:
                    self._on_fill(delta, now)
            except BrokerError:
                return
        self._ensure_protection(self.db.trade(t["id"]), now)

    # ================================================================ session housekeeping
    def _on_new_session(self, now: datetime, sess: SessionState) -> None:
        day = sess.session.day
        self._session_day = day
        self.signals_today = 0
        self._load_baselines(now)
        start_eq = self.account.equity if self.account else None
        if self.account and self.account.last_equity > 0 and not self.data.is_demo:
            start_eq = self.account.last_equity
        self.db.execute("INSERT OR IGNORE INTO daily_summary(session_date, start_equity) VALUES(?, ?)",
                        (day.isoformat(), start_eq))
        stale = [t for t in self.db.open_trades() if t["session_date"] < day.isoformat()]
        for t in stale:
            if t["status"] == "pending_entry":
                self.db.update("trades", t["id"], status="canceled", exit_reason="entry order expired overnight")
        self.emit("info", "system", "market open", f"{sess.describe()} · scanning {len(self.universe)} symbols")

    def _on_session_end(self, now: datetime, sess: SessionState) -> None:
        day = sess.session.day
        self._summarized_day = day
        self.shadow.update({}, now, force_close=True)
        rows = self.db.query("SELECT * FROM trades WHERE session_date=? AND status='closed'", (day.isoformat(),))
        realized = sum(r["realized_pnl"] or 0 for r in rows)
        fees = sum(r["fees"] or 0 for r in rows)
        wins = sum(1 for r in rows if (r["realized_pnl"] or 0) > 0)
        losses = sum(1 for r in rows if (r["realized_pnl"] or 0) <= 0)
        ai_cost = float(self.db.scalar("SELECT SUM(cost_usd) FROM ai_decisions WHERE time >= ? AND time < ?",
                                       (to_iso(sess.session.open - timedelta(hours=10)),
                                        to_iso(sess.session.close + timedelta(hours=8))), 0.0))
        end_eq = self.account.equity if self.account else None
        bench = None
        try:
            snap = self.data.get_snapshots(["SPY"]).get("SPY")
            bench = snap.price if snap else None
        except DataUnavailable:
            pass
        self.db.execute("INSERT OR IGNORE INTO daily_summary(session_date) VALUES(?)", (day.isoformat(),))
        self.db.execute("UPDATE daily_summary SET end_equity=?, realized=?, fees=?, trades=?, wins=?, losses=?, "
                        "ai_cost=?, benchmark_close=? WHERE session_date=?",
                        (end_eq, realized, fees, len(rows), wins, losses, ai_cost, bench, day.isoformat()))
        if end_eq is not None:
            self.db.execute("INSERT OR REPLACE INTO equity_snapshots(time, session_date, equity, cash, settled_cash, "
                            "buying_power, realized_today, unrealized, positions_value) VALUES(?,?,?,?,?,?,?,?,?)",
                            (to_iso(now), day.isoformat(), end_eq, self.account.cash, self._settled_cash(now),
                             self.account.buying_power, realized, 0.0, self.account.long_market_value))
        sign = "+" if realized >= 0 else "−"
        self.emit("info", "system", "market closed",
                  f"today: {len(rows)} trade(s), {sign}${abs(realized):.2f} realized after ${fees:.2f} fees", notify=True)

    # ================================================================ scanning & signals
    def _refresh_quotes(self, now: datetime) -> None:
        syms = sorted({t["symbol"] for t in self.db.open_trades()} | set(CONTEXT_SYMBOLS[:1]))
        if not syms:
            return
        try:
            snaps = self.data.get_snapshots(syms)
        except DataUnavailable as exc:
            self.health["data"].bad(str(exc))
            return
        for s, snap in snaps.items():
            if snap.quote is not None:
                self.quotes[s] = (snap.quote, snap.price, snap.timestamp)
            else:
                self.quotes[s] = (Quote(snap.price, snap.price, 0, 0, snap.timestamp), snap.price, snap.timestamp)

    def _price(self, symbol: str) -> Optional[float]:
        q = self.quotes.get(symbol)
        if q:
            return q[1]
        row = self.scanner.rows.get(symbol)
        return row.price if row else None

    def _full_scan(self, now: datetime, sess: SessionState) -> None:
        held = [t["symbol"] for t in self.db.open_trades()]
        try:
            self.scanner.full_scan(now, sess.session, always=[*CONTEXT_SYMBOLS[:1], *held])
        except DataUnavailable as exc:
            self.health["data"].bad(str(exc))
            return
        rows = list(self.scanner.rows.values())
        fresh = [r for r in rows if r.data_age_s <= self.settings.risk.max_data_age_seconds]
        self.health["data"].good(f"{len(rows)} symbols scanned · {len(fresh)} with fresh prices", now)
        self.shadow.update({r.symbol: r.price for r in rows}, now)

    def _hot_scan_and_signals(self, now: datetime, sess: SessionState) -> None:
        if not self.scanner.hotlist:
            return
        try:
            frames = self.scanner.hot_scan(now, sess.session)
        except DataUnavailable as exc:
            self.health["data"].bad(str(exc))
            return
        held = {t["symbol"] for t in self.db.open_trades()} | {p.symbol for p in self.external_positions}
        enabled = [k for k, v in self.settings.strategies.enabled.items() if v]
        sigs = self.scanner.signals(now, frames, enabled, exclude=held | set(self.pending_ai) | set(CONTEXT_SYMBOLS))
        self.regime = self._read_regime(frames)
        if not sess.can_enter or self.emergency_stop:
            return
        min_score = self.settings.strategies.min_signal_score
        sigs = [s for s in sigs if s.score >= min_score][:12]
        decision = self._rank(sigs, now)
        self.last_decision = decision
        self._journal(decision, now)
        for c in decision.candidates:  # what every candidate would have done, for fair comparisons
            self._shadow_once(c.signal, "rules", now)
            if c.take:
                self._shadow_once(c.signal, "ranked", now)
        if self.settings.ai.decision_mode == "ai_select":
            self._ask_ai_to_choose(decision, now, sess)
            return
        for c in decision.chosen:
            self._consider(c.signal, now, sess)

    # ================================================================ comparing opportunities
    def _read_regime(self, frames) -> Regime:
        spy = frames.get("SPY") if frames else None
        above = self.scanner.market_trend_up
        row = self.scanner.rows.get("SPY")
        vr = None
        if spy is not None and not spy.empty:
            b = self.scanner.baselines.get("SPY")
            lo = float(spy["low"].min())
            if b is not None and b.avg_range_pct and lo > 0:
                vr = ((float(spy["high"].max()) - lo) / lo * 100) / b.avg_range_pct
        return classify_regime(above, row.change_pct if row else None, breadth_pct(self.scanner.rows.values()), vr)

    def _evidence(self, sid: str) -> Evidence:
        row = self.db.query_one("SELECT * FROM strategy_evidence WHERE strategy=?", (sid,))
        ev = Evidence()
        if row and not row["source"].startswith("synthetic"):
            ev.verdict, ev.source = row["verdict"], row["source"]
            try:
                ev.avg_return_pct = json.loads(row["details"] or "{}").get("oos", {}).get("avgRetPct")
            except ValueError:
                pass
        if ev.verdict == "untested" and not self.data.is_demo:  # paper forward test (real prices only)
            rets = [r["return_pct"] for r in self.db.query(
                "SELECT return_pct FROM shadow_trades WHERE status='closed' AND verdict='rules' AND strategy=?", (sid,))]
            if rets:
                ev.trades, ev.avg_return_pct, ev.source = len(rets), sum(rets) / len(rets), "paper forward test"
        return ev

    def _rank(self, sigs: list[Signal], now: datetime) -> Decision:
        open_n = len(self.db.open_trades()) + len(self.pending_ai) + (1 if self.pending_select else 0)
        free = max(0, self.settings.risk.max_positions - open_n)
        notional = scaled_limits(self.settings, self.account.equity if self.account else 100)["max_position_usd"]
        rows = [r for r in self.scanner.rows.values() if r.eligible]
        dv = sorted(r.day_volume * r.price for r in rows) or [0.0]
        cands = []
        evidence = {}
        self.ranker.max_stop_pct = self.settings.risk.max_stop_pct
        self.ranker.max_spread_bps = self.settings.risk.max_spread_bps
        for s in sigs:
            row = self.scanner.rows.get(s.symbol)
            spread = row.spread_bps if row and math.isfinite(row.spread_bps) else 10.0
            rank = (sum(1 for x in dv if x <= row.day_volume * row.price) / len(dv)) if row else 0.5
            ev = evidence.setdefault(s.strategy, self._evidence(s.strategy))
            cands.append(self.ranker.score(s, self.regime, ev, notional, spread, rank))
        return self.ranker.decide(cands, self.regime, free)

    def _journal(self, d: Decision, now: datetime) -> None:
        key = (tuple(sorted(c.signal.symbol for c in d.candidates)), tuple(c.signal.symbol for c in d.chosen),
               d.regime.label)
        if not d.candidates or key == self._last_journal:
            return
        self._last_journal = key
        self.db.insert("decision_cycles", time=to_iso(now), regime=d.regime.label, summary=d.summary,
                       mode=self.settings.ai.decision_mode, data=json.dumps(d.to_dict()))
        # the full journal is in decision_cycles; the activity log only gets picks, plus a
        # "still not trading" note at most every few minutes so it doesn't drown everything else
        if d.chosen or self._last_pass_note is None or now - self._last_pass_note >= timedelta(minutes=5):
            if not d.chosen:
                self._last_pass_note = now
            self.emit("info", "decision", d.summary, d.regime.text)

    def _shadow_once(self, sig: Signal, verdict: str, now: datetime, signal_id: Optional[int] = None) -> None:
        key = (sig.symbol, sig.strategy, verdict)
        last = self._shadowed.get(key)
        if last and now - last < timedelta(minutes=self.settings.ai.recheck_cooldown_minutes):
            return
        self._shadowed[key] = now
        row = self.scanner.rows.get(sig.symbol)
        spread = row.spread_bps if row and math.isfinite(row.spread_bps) else 3.0
        self.shadow.open(sig, verdict, signal_id, now, spread)

    def _ask_ai_to_choose(self, d: Decision, now: datetime, sess: SessionState) -> None:
        options = [c for c in d.candidates if not c.blockers or c.blockers == ["a better opportunity took the last free slot"]][:3]
        if not options or d.free_slots <= 0 or self.pending_select is not None:
            return  # nothing worth the AI's time (and money): not trading is the decision
        if all(self.ai.recently_asked(c.signal.symbol, c.signal.strategy) for c in options):
            return
        ok, why = self.ai.can_call()
        if not ok:
            for c in options:
                self._set_row_verdict(c.signal.symbol, "ai offline")
            return
        payload = []
        for c in options:
            o = c.to_dict()
            o["indicators"] = c.signal.features
            o["why_it_triggered"] = c.signal.reasons
            payload.append(o)
        ctx = {"market": d.regime.to_dict(), "time_et": now.astimezone(ET).strftime("%H:%M"),
               "minutes_to_close": round(sess.minutes_to_close), "data_note": self.data.coverage_note}
        fut = self.ai.submit_selection(payload, ctx)
        self.pending_select = (fut, {c.signal.symbol: c for c in options}, now)
        for c in options:
            self._set_row_verdict(c.signal.symbol, "asking ai…")
        self.emit("info", "decision", f"asking the AI to choose between {', '.join(c.signal.symbol for c in options)}",
                  d.summary)

    def _collect_selection(self, now: datetime, sess: SessionState) -> None:
        if self.pending_select is None or not self.pending_select[0].done():
            return
        fut, options, asked = self.pending_select
        self.pending_select = None
        try:
            res: AIResult = fut.result()
        except Exception:
            log.exception("AI selection failed")
            return
        if not res.ok:
            self.emit("warning", "ai", "no trade: AI answer unusable", res.error or "", ref_id=res.decision_id)
            for sym in options:
                self._set_row_verdict(sym, "ai error")
            return
        choice = res.selection.choice
        for sym, c in options.items():
            self._shadow_once(c.signal, "approved" if sym == choice else "skipped", now)
            self._set_row_verdict(sym, "ai: buy" if sym == choice else "ai: skip")
        if choice == "none":
            self.emit("info", "ai", "AI chose not to trade", res.selection.reasoning, ref_id=res.decision_id)
            return
        sig = options[choice].signal
        sid = self.db.insert("signals", time=to_iso(now), symbol=sig.symbol, strategy=sig.strategy, score=sig.score,
                             price=sig.price, stop_price=sig.stop_price, target_price=sig.target_price,
                             reasons=json.dumps(sig.reasons), features=json.dumps(sig.features), outcome="ai_selected")
        px = self._latest_price(sig.symbol)
        drift = abs(px / sig.price - 1) * 100 if px else 0.0
        age = (now - asked).total_seconds()
        if age > self.settings.ai.max_signal_age_seconds or drift > self.settings.ai.max_price_drift_pct or not sess.can_enter:
            self.db.update("signals", sid, outcome="stale_after_ai", outcome_detail=f"{age:.0f}s, moved {drift:.2f}%")
            self.emit("info", "decision", f"skipped {sig.symbol}: the moment passed",
                      f"the AI picked it, but the answer took {age:.0f}s and the price moved {drift:.2f}%", sig.symbol)
            return
        self.emit("success", "ai", f"AI picked {sig.symbol}", res.selection.reasoning, sig.symbol,
                  notify=self.settings.automation.notify_decisions, ref_id=res.decision_id)
        self._enter(sig, sid, res, now, sess)

    def _consider(self, sig: Signal, now: datetime, sess: SessionState) -> None:
        key = (sig.symbol, sig.strategy)
        last = self._last_signal_logged.get(key)
        if last and now - last < timedelta(minutes=self.settings.ai.recheck_cooldown_minutes):
            return
        self._last_signal_logged[key] = now
        self.signals_today += 1
        sid = self.db.insert("signals", time=to_iso(now), symbol=sig.symbol, strategy=sig.strategy, score=sig.score,
                             price=sig.price, stop_price=sig.stop_price, target_price=sig.target_price,
                             reasons=json.dumps(sig.reasons), features=json.dumps(sig.features), outcome="pending")
        pre = self._risk_check(sig, now, sess)
        blocking = [r for r in pre.reasons if not r.startswith("quote is") and "spread" not in r]
        if blocking:
            self.db.update("signals", sid, outcome="risk_blocked", outcome_detail="; ".join(pre.reasons)[:500])
            self._set_row_verdict(sig.symbol, "blocked")
            return
        if self.settings.ai.decision_mode == "rules":
            self.db.update("signals", sid, outcome="rules_entry")
            self._enter(sig, sid, None, now, sess)
            return
        ok, why = self.ai.can_call()
        if not ok:
            self.db.update("signals", sid, outcome="ai_unavailable", outcome_detail=why)
            self._set_row_verdict(sig.symbol, "ai offline")
            return
        if sig.symbol in self.pending_ai:
            return
        ctx = self._ai_context(sig, sid, now, sess)
        self.pending_ai[sig.symbol] = PendingAI(self.ai.submit_entry(sig, ctx), sig, sid, ctx.get("spread_bps", 3.0))
        self._set_row_verdict(sig.symbol, "asking ai…")
        self.emit("info", "decision", f"asking the AI about {sig.symbol}",
                  f"{ALL_STRATEGIES[sig.strategy].name}: " + "; ".join(sig.reasons[:2]), sig.symbol, ref_id=sid)

    def _set_row_verdict(self, symbol: str, verdict: str) -> None:
        row = self.scanner.rows.get(symbol)
        if row is not None:
            row.ai_verdict = verdict

    def _ai_context(self, sig: Signal, sid: int, now: datetime, sess: SessionState) -> dict:
        f = self.scanner.frames.get(sig.symbol)
        bars = []
        if f is not None and not f.empty:
            for ts, r in f.iloc[-15:].iterrows():
                bars.append({"t": ts.tz_convert(ET).strftime("%H:%M"), "o": round(r["open"], 4), "h": round(r["high"], 4),
                             "l": round(r["low"], 4), "c": round(r["close"], 4), "v": int(r["volume"])})
        spy = self.scanner.rows.get("SPY")
        row = self.scanner.rows.get(sig.symbol)
        spread = row.spread_bps if row and math.isfinite(row.spread_bps) else 3.0
        notional = scaled_limits(self.settings, self.account.equity if self.account else 100)["max_position_usd"]
        return {
            "signal_id": sid,
            "spread_bps": spread,
            "recent_bars": bars,
            "round_trip_cost_pct": round(self.costs.round_trip_cost_pct(notional, sig.price, spread), 3),
            "market": {"spy_change_pct": round(spy.change_pct, 2) if spy else None,
                       "spy_above_vwap": self.scanner.market_trend_up,
                       "minutes_to_close": round(sess.minutes_to_close)},
            "account": {"open_positions": len(self.db.open_trades()),
                        "today_pnl_usd": round(self._pnl_today(now)[0] + self._pnl_today(now)[1], 2)},
            "data_note": self.data.coverage_note,
        }

    def _collect_ai(self, now: datetime, sess: SessionState) -> None:
        for sym, p in list(self.pending_ai.items()):
            if not p.future.done():
                continue
            del self.pending_ai[sym]
            try:
                res: AIResult = p.future.result()
            except Exception as exc:
                log.exception("AI future failed")
                self.db.update("signals", p.signal_id, outcome="ai_error", outcome_detail=str(exc)[:300])
                continue
            if not res.ok:
                self.db.update("signals", p.signal_id, outcome="ai_invalid", outcome_detail=(res.error or "")[:400])
                self._set_row_verdict(sym, "ai error")
                self.emit("warning", "ai", f"no trade in {sym}: AI answer unusable", res.error or "", sym,
                          ref_id=res.decision_id)
                continue
            d = res.decision
            verdict = "approved" if res.approved else "skipped"
            self.shadow.open(p.signal, verdict, p.signal_id, now, p.spread_bps)
            self._set_row_verdict(sym, "ai: buy" if res.approved else "ai: skip")
            if not res.approved:
                self.db.update("signals", p.signal_id, outcome="ai_skipped", outcome_detail=res.verdict_text[:300])
                self.emit("info", "ai", f"AI passed on {sym} ({d.confidence:.0%} confident)", d.reasoning, sym,
                          ref_id=res.decision_id)
                continue
            age = (now - (res.requested_at or now)).total_seconds()
            px = self._latest_price(sym)
            drift = abs(px / p.signal.price - 1) * 100 if px else 0.0
            if age > self.settings.ai.max_signal_age_seconds or drift > self.settings.ai.max_price_drift_pct:
                self.db.update("signals", p.signal_id, outcome="stale_after_ai",
                               outcome_detail=f"{age:.0f}s old, price moved {drift:.2f}%")
                self.emit("info", "decision", f"skipped {sym}: the moment passed",
                          f"AI said buy, but the answer took {age:.0f}s and the price moved {drift:.2f}%", sym)
                continue
            if not sess.can_enter:
                self.db.update("signals", p.signal_id, outcome="risk_blocked", outcome_detail="entry window closed")
                continue
            sig = p.signal
            r = self.settings.risk
            stop_pct = min(max(d.stop_loss_pct, r.min_stop_pct), r.max_stop_pct)
            ai_stop = sig.price * (1 - stop_pct / 100)
            sig.stop_price = max(sig.stop_price, ai_stop)  # the tighter of the two
            sig.target_price = sig.price * (1 + max(d.take_profit_pct, stop_pct) / 100)
            sig.max_hold_minutes = min(sig.max_hold_minutes, d.max_hold_minutes)
            self.db.update("signals", p.signal_id, outcome="ai_approved")
            self.emit("success", "ai", f"AI approved {sym} ({d.confidence:.0%} confident)", d.reasoning, sym,
                      notify=self.settings.automation.notify_decisions, ref_id=res.decision_id)
            self._enter(sig, p.signal_id, res, now, sess)

    def _latest_price(self, symbol: str) -> Optional[float]:
        try:
            snap = self.data.get_snapshots([symbol]).get(symbol)
        except DataUnavailable:
            snap = None
        if snap is None:
            return self._price(symbol)
        if snap.quote is not None:
            self.quotes[symbol] = (snap.quote, snap.price, snap.timestamp)
        return snap.price

    # ================================================================ risk & entries
    def _strategy_validated(self, sid: str) -> bool:
        row = self.db.query_one("SELECT verdict FROM strategy_evidence WHERE strategy=?", (sid,))
        return bool(row and row["verdict"] == "evidence")

    def _settled_cash(self, now: datetime) -> float:
        if self.account is None:
            return 0.0
        return self.ledger.settled_cash(self.account.cash, now.astimezone(ET).date())

    def _pnl_today(self, now: datetime) -> tuple[float, float]:
        day = now.astimezone(ET).date().isoformat()
        realized = float(self.db.scalar("SELECT SUM(realized_pnl) FROM trades WHERE status='closed' AND session_date=?",
                                        (day,), 0.0))
        unreal = 0.0
        for t in self.db.open_trades():
            rem = t["entry_qty"] - t["exit_qty"]
            if rem <= EPS or not t["entry_price"]:
                continue
            px = self._price(t["symbol"]) or t["entry_price"]
            unreal += (px - t["entry_price"]) * rem
            if t["exit_qty"] > 0 and t["exit_price"]:
                unreal += (t["exit_price"] - t["entry_price"]) * t["exit_qty"] - (t["fees"] or 0)
        return realized, unreal

    def risk_context(self, now: datetime, sess: SessionState) -> RiskContext:
        day = now.astimezone(ET).date().isoformat()
        open_trades = self.db.open_trades()
        opens: list[OpenRisk] = []
        pending_syms: set[str] = set()
        pending_notional = 0.0
        for t in open_trades:
            rem = t["entry_qty"] - t["exit_qty"]
            if t["status"] == "pending_entry":
                pending_syms.add(t["symbol"])
                for o in self.orders.active(trade_id=t["id"], purpose="entry"):
                    pending_notional += max(0.0, (o["qty"] or 0) - (o["filled_qty"] or 0)) * (o["limit_price"] or 0)
            if rem > EPS:
                px = self._price(t["symbol"]) or t["entry_price"]
                opens.append(OpenRisk(t["symbol"], rem, t["entry_price"], px, t["stop_price"]))
        closed_today = self.db.query("SELECT symbol, realized_pnl, exit_time FROM trades WHERE status='closed' "
                                     "AND session_date=? ORDER BY exit_time DESC", (day,))
        consec = 0
        for r in closed_today:
            if (r["realized_pnl"] or 0) < 0:
                consec += 1
            else:
                break
        last_exit = {}
        for r in closed_today:
            et = from_iso(r["exit_time"])
            if et and r["symbol"] not in last_exit:
                last_exit[r["symbol"]] = et
        trades_today = int(self.db.scalar("SELECT COUNT(*) FROM trades WHERE session_date=? AND (entry_qty>0 OR "
                                          "status='pending_entry')", (day,), 0))
        realized, unreal = self._pnl_today(now)
        live_ok = live_authorization_status(self.settings)[0] if self.mode == "live" else False
        return RiskContext(
            now=now, session=sess, mode=self.mode, autopilot=self.settings.automation.autopilot,
            emergency_stop=self.emergency_stop, live_authorized=live_ok,
            broker_connected=bool(self.health["broker"].ok), data_ok=self.health["data"].ok is not False,
            account=self.account, settled_cash=self._settled_cash(now), open_positions=opens,
            pending_entry_symbols=pending_syms, pending_buy_notional=pending_notional, realized_today=realized,
            unrealized_today=unreal, trades_today=trades_today, consecutive_losses=consec, last_exit_time=last_exit,
            held_symbols={p.symbol for p in self.external_positions})

    def _risk_check(self, sig: Signal, now: datetime, sess: SessionState):
        q = self.quotes.get(sig.symbol)
        row = self.scanner.rows.get(sig.symbol)
        if q is None and row is not None:
            half = sig.price * (row.spread_bps if math.isfinite(row.spread_bps) else 10) / 2 / 10_000
            bid, ask, qt = sig.price - half, sig.price + half, now - timedelta(seconds=row.data_age_s)
        elif q is not None:
            bid, ask, qt = q[0].bid, q[0].ask, q[2]
        else:
            bid = ask = 0.0
            qt = None
        proposal = EntryProposal(sig.symbol, sig.price, bid, ask, qt, sig.stop_price, sig.target_price, sig.strategy,
                                 self._strategy_validated(sig.strategy), self.assets.get(sig.symbol))
        return self.risk.check_entry(self.risk_context(now, sess), proposal)

    def _enter(self, sig: Signal, signal_id: int, ai_res: Optional[AIResult], now: datetime, sess: SessionState) -> None:
        self._latest_price(sig.symbol)  # fresh quote for the final check
        decision = self._risk_check(sig, now, sess)
        if not decision.approved:
            self.db.update("signals", signal_id, outcome="risk_blocked", outcome_detail="; ".join(decision.reasons)[:500])
            self.emit("info", "risk", f"risk engine blocked {sig.symbol}", "; ".join(decision.reasons), sig.symbol,
                      ref_id=signal_id)
            return
        if ai_res and ai_res.decision:
            reasoning = ai_res.decision.reasoning
        elif ai_res and ai_res.extra.get("reasoning"):
            reasoning = ai_res.extra["reasoning"]
        else:
            reasoning = "rules-only: " + "; ".join(sig.reasons)
        tid = self.db.insert(
            "trades", symbol=sig.symbol, strategy=sig.strategy, mode=self.mode, status="pending_entry", managed=1,
            session_date=now.astimezone(ET).date().isoformat(), created_at=to_iso(now), stop_price=decision.stop_price,
            initial_stop=decision.stop_price, target_price=decision.target_price,
            max_hold_until=to_iso(now + timedelta(minutes=sig.max_hold_minutes)),
            decision_mode=self.settings.ai.decision_mode, ai_decision_id=ai_res.decision_id if ai_res else None,
            signal_id=signal_id, reasoning=reasoning,
            signal_json=json.dumps({"reasons": sig.reasons, "features": sig.features, "score": sig.score,
                                    "risk_notes": decision.notes, "sizing": decision.size.explain() if decision.size else ""}))
        r = self.settings.risk
        req = dict(symbol=sig.symbol, side="buy", qty=decision.qty,
                   type="limit" if r.entry_order_type == "limit" else "market")
        if r.entry_order_type == "limit":
            req["limit_price"] = decision.limit_price
        row = self.orders.place(tid, "entry", req)
        self.emit("info", "decision", f"buying {sig.symbol} · ${decision.notional:.2f}",
                  f"{ALL_STRATEGIES[sig.strategy].name} · stop ${decision.stop_price:.2f} · target "
                  f"${decision.target_price:.2f}" + (f" · {'; '.join(decision.notes)}" if decision.notes else ""),
                  sig.symbol, ref_id=tid)
        _, delta = self.orders.sync(row) if row["status"] not in ("rejected",) else (row, None)
        if delta:
            self._on_fill(delta, now)
        self._advance_trade(self.db.trade(tid), now)

    # ================================================================ position management
    def _manage_positions(self, now: datetime, sess: SessionState) -> None:
        trades = self.db.open_trades()
        if not trades:
            return
        day = now.astimezone(ET).date().isoformat()
        for t in trades:
            if t["status"] != "open":
                continue
            rem = t["entry_qty"] - t["exit_qty"]
            if rem <= EPS:
                continue
            px = self._price(t["symbol"])
            if px is None:
                continue
            q = self.quotes.get(t["symbol"])
            bid = q[0].bid if q else px
            entry = t["entry_price"]
            hi = max(t["highest_price"] or entry, px)
            if hi > (t["highest_price"] or 0):
                self.db.update("trades", t["id"], highest_price=hi)
            reason = None
            if self.emergency_stop:
                reason = "emergency stop"
            elif sess.phase == "flatten":
                reason = "end of day — day trades are closed before the bell"
            elif t["session_date"] < day:
                if self.settings.risk.close_leftover_positions_at_open and sess.phase != "opening":
                    reason = "left over from a previous session"
            elif bid <= t["stop_price"]:
                if t["software_stop"] or not self.orders.active(trade_id=t["id"], purpose="stop"):
                    reason = f"stop-loss hit at ${bid:.2f}"
                elif bid <= t["stop_price"] * 0.995:
                    reason = "price fell through the stop and the stop order hasn't filled — selling at market"
            elif px >= t["target_price"]:
                reason = f"profit target reached (${t['target_price']:.2f})"
            elif now >= (from_iso(t["max_hold_until"]) or now + timedelta(days=1)):
                reason = "held for the maximum planned time"
            if reason is None:
                reason = self._strategy_exit(t)
            if reason:
                self._begin_exit(t, reason)
                continue
            # trailing protection: breakeven after +1R, then trail 1R below the high after +1.5R
            risk_r = max(entry - (t["initial_stop"] or t["stop_price"]), entry * 0.002)
            new_stop = t["stop_price"]
            if hi >= entry + risk_r:
                new_stop = max(new_stop, entry * 1.0005)
            if hi >= entry + 1.5 * risk_r:
                new_stop = max(new_stop, hi - risk_r)
            if new_stop > t["stop_price"]:
                self._move_stop(t, new_stop, now)
            self._maybe_review(t, px, now)
        for tid, fut in list(self.pending_reviews.items()):
            if fut.done():
                del self.pending_reviews[tid]
                try:
                    res: AIResult = fut.result()
                except Exception:
                    continue
                t = self.db.trade(tid)
                if res.ok and res.review and t and t["status"] == "open":
                    self.db.update("trades", tid, last_review_at=to_iso(now))
                    if res.review.action == "sell" and res.review.confidence >= 0.6:
                        self.emit("info", "ai", f"AI suggests selling {t['symbol']}", res.review.reasoning,
                                  t["symbol"], ref_id=res.decision_id)
                        self._begin_exit(t, "AI review: " + res.review.reasoning[:160])
        # cancel stale entry orders at the end of the day
        if sess.phase in ("wind_down", "flatten") or self.emergency_stop:
            for t in trades:
                if t["status"] == "pending_entry":
                    self._begin_exit(t, "entry window closed")

    def _strategy_exit(self, t: dict) -> Optional[str]:
        f = self.scanner.frames.get(t["symbol"])
        strat = ALL_STRATEGIES.get(t["strategy"])
        if f is None or f.empty or strat is None:
            return None
        entry_time = from_iso(t["entry_time"])
        entry_index = None
        if entry_time is not None:
            import pandas as pd
            entry_index = int(f.index.searchsorted(pd.Timestamp(entry_time)))
        if entry_index is not None and len(f) - 1 <= entry_index:
            return None  # no completed bar since entry yet
        try:
            return strat.exit(f, len(f) - 1, t["entry_price"], entry_index)
        except Exception:
            log.exception("strategy exit check failed")
            return None

    def _maybe_review(self, t: dict, px: float, now: datetime) -> None:
        ai = self.settings.ai
        if ai.decision_mode != "ai_confirm" or not ai.review_open_positions or t["id"] in self.pending_reviews:
            return
        last = from_iso(t["last_review_at"]) or from_iso(t["entry_time"]) or now
        if now - last < timedelta(minutes=ai.review_interval_minutes):
            return
        ok, _ = self.ai.can_call()
        if not ok:
            return
        self.db.update("trades", t["id"], last_review_at=to_iso(now))
        from ..analysis.features import latest_features
        f = self.scanner.frames.get(t["symbol"])
        ctx = {"trade_id": t["id"], "symbol": t["symbol"], "strategy": t["strategy"],
               "entry_price": t["entry_price"], "current_price": px,
               "unrealized_pct": round((px / t["entry_price"] - 1) * 100, 2), "stop_price": t["stop_price"],
               "target_price": t["target_price"],
               "minutes_held": round((now - (from_iso(t["entry_time"]) or now)).total_seconds() / 60),
               "indicators": latest_features(f) if f is not None else {}}
        self.pending_reviews[t["id"]] = self.ai.submit_exit_review(t["symbol"], ctx)

    def _check_daily_limit(self, now: datetime) -> None:
        if self.session is None:
            return
        ctx = self.risk_context(now, self.session)
        if not self.risk.daily_limit_breached(ctx):
            return
        day = now.astimezone(ET).date()
        if self._daily_limit_day == day:
            return
        self._daily_limit_day = day
        limit = self.risk.daily_limit(self.account.equity if self.account else 100)
        self.emit("warning", "risk", "daily loss limit reached",
                  f"down ${-ctx.pnl_today:.2f} today (limit ${limit:.2f}). No new trades until tomorrow. "
                  "Losses can exceed the limit if prices gap past stops.", notify=True)
        if self.settings.risk.close_positions_on_daily_limit:
            for t in self.db.open_trades():
                self._begin_exit(t, "daily loss limit reached")

    def _do_emergency_stop(self) -> None:
        self.emergency_stop = True
        self.db.set_state("emergency_stop", True)
        n = 0
        for t in self.db.open_trades():
            self._begin_exit(t, "emergency stop")
            n += 1
        for o in self.db.active_orders():
            if o["purpose"] == "entry":
                try:
                    self.orders.cancel(o)
                except BrokerError:
                    pass
        self.pending_ai.clear()
        self.emit("error", "risk", "EMERGENCY STOP", f"all trading halted; closing {n} position(s) managed by the app",
                  notify=True)

    # ================================================================ health & snapshots
    def _check_ai_health(self, now: datetime) -> None:
        if self.settings.ai.decision_mode == "rules":
            self.health["ai"].good("rules-only mode — AI not used for entries", now)
            return
        ok, msg = self.ai.check_health()
        (self.health["ai"].good(msg, now) if ok else self.health["ai"].bad(msg))

    def _update_risk_health(self, now: datetime) -> None:
        h = self.health["risk"]
        if self.emergency_stop:
            h.bad("emergency stop is active")
        elif self._daily_limit_day == now.astimezone(ET).date():
            h.bad("daily loss limit reached — no new trades today")
        elif not self.settings.automation.autopilot:
            h.good("autopilot is off — watching only", now)
        elif self.mode == "live" and not live_authorization_status(self.settings)[0]:
            h.bad(live_authorization_status(self.settings)[1])
        else:
            h.good("all limits OK", now)

    def _snapshot_equity(self, now: datetime, sess: SessionState) -> None:
        if self.account is None:
            return
        realized, unreal = self._pnl_today(now)
        self.db.execute(
            "INSERT OR REPLACE INTO equity_snapshots(time, session_date, equity, cash, settled_cash, buying_power, "
            "realized_today, unrealized, positions_value) VALUES(?,?,?,?,?,?,?,?,?)",
            (to_iso(now), sess.session.day.isoformat(), self.account.equity, self.account.cash,
             self._settled_cash(now), self.account.buying_power, realized, unreal, self.account.long_market_value))

    def status_line(self, now: datetime) -> dict:
        sess = self.session
        n_open = len([t for t in self.db.open_trades() if t["status"] != "pending_entry"])
        if not self.initialized:
            return {"kind": "error", "headline": "can't start yet",
                    "detail": self.init_error or "connecting to the brokerage…"}
        if self.emergency_stop:
            return {"kind": "stopped", "headline": "everything is stopped.",
                    "detail": "press reset when you're ready to trade again."}
        if self.health["broker"].ok is False:
            return {"kind": "error", "headline": "can't reach the brokerage.",
                    "detail": "no new trades until the connection is back. broker-side stop orders still protect "
                              "open positions."}
        if sess is None or not sess.is_open:
            return {"kind": "waiting", "headline": "market's closed — napping.",
                    "detail": f"{sess.describe() if sess else ''}. i'll start scanning automatically at the open."}
        if self._daily_limit_day == now.astimezone(ET).date():
            return {"kind": "paused", "headline": "hit today's loss limit.",
                    "detail": "no new trades until tomorrow. open positions are still managed."}
        if not self.settings.automation.autopilot:
            return {"kind": "paused", "headline": "autopilot is off.",
                    "detail": "i'm scanning and explaining, but not placing trades."}
        if self.mode == "live" and not live_authorization_status(self.settings)[0]:
            return {"kind": "paused", "headline": "live trading needs your ok.",
                    "detail": live_authorization_status(self.settings)[1]}
        if self.settings.ai.decision_mode == "ai_confirm" and self.health["ai"].ok is False:
            return {"kind": "paused", "headline": "the AI is offline, so i'm not buying.",
                    "detail": f"{self.health['ai'].message}. start Ollama, or switch to rules-only in settings."}
        if sess.phase == "opening":
            return {"kind": "waiting", "headline": "the market just opened.",
                    "detail": "letting the first few chaotic minutes pass before doing anything."}
        if sess.phase in ("wind_down", "flatten"):
            return {"kind": "waiting", "headline": "wrapping up for the day.",
                    "detail": "no new trades this close to the bell; closing any open positions."}
        if self.pending_ai:
            return {"kind": "trading", "headline": f"thinking about {', '.join(self.pending_ai)}…",
                    "detail": "the AI is reviewing the setup. the risk engine gets the final say."}
        if n_open:
            return {"kind": "trading", "headline": f"holding {n_open} position{'s' if n_open > 1 else ''}.",
                    "detail": f"watching {len(self.scanner.hotlist)} stocks closely for the next opportunity."}
        return {"kind": "trading", "headline": f"scanning {len(self.universe)} stocks.",
                "detail": "nothing convincing yet — sitting tight is a perfectly good trade."}

    def _publish_snapshot(self, now: datetime) -> None:
        try:
            snap = self._build_snapshot(now)
        except Exception:
            log.exception("snapshot failed")
            return
        with self._snap_lock:
            self._snapshot = snap

    def snapshot(self) -> dict:
        with self._snap_lock:
            return self._snapshot

    def _build_snapshot(self, now: datetime) -> dict:
        sess = self.session
        acct = self.account
        equity = acct.equity if acct else 0.0
        day = now.astimezone(ET).date().isoformat()
        start_eq = self.db.scalar("SELECT start_equity FROM daily_summary WHERE session_date=?", (day,), None)
        if start_eq is None:
            start_eq = (acct.last_equity if acct and acct.last_equity > 0 else equity)
        realized, unreal = self._pnl_today(now) if self.initialized else (0.0, 0.0)
        day_pnl = equity - start_eq if acct and start_eq else realized + unreal
        lim = scaled_limits(self.settings, equity or self.settings.risk.starting_capital)
        positions = []
        for t in self.db.open_trades():
            rem = t["entry_qty"] - t["exit_qty"]
            px = self._price(t["symbol"]) or t["entry_price"] or 0.0
            ep = t["entry_price"] or 0.0
            positions.append({
                "tradeId": t["id"], "symbol": t["symbol"], "strategy": ALL_STRATEGIES[t["strategy"]].name
                if t["strategy"] in ALL_STRATEGIES else t["strategy"], "status": t["status"], "qty": rem,
                "entryPrice": ep, "price": px, "value": rem * px, "pnl": (px - ep) * rem if ep else 0.0,
                "pnlPct": (px / ep - 1) * 100 if ep else 0.0, "stop": t["stop_price"] or 0.0,
                "target": t["target_price"] or 0.0,
                "protection": "app-watched stop" if t["software_stop"] else "broker stop order",
                "heldMinutes": round((now - (from_iso(t["entry_time"]) or now)).total_seconds() / 60),
                "reasoning": t["reasoning"] or "", "exitReason": t["exit_reason"] or ""})
        eq_rows = self.db.query("SELECT time, equity FROM equity_snapshots WHERE session_date=? ORDER BY time", (day,))
        ai_ok, ai_why = self.ai.can_call()
        spent_ai = self.ai.month_cost()
        status = self.status_line(now)
        return {
            "updatedAt": to_iso(now), "mode": self.mode, "isDemo": bool(self.data.is_demo),
            "clockText": now.astimezone(ET).strftime("%a %b %d · %H:%M:%S ET"),
            "feedLabel": self.data.feed_label, "coverageNote": self.data.coverage_note,
            "brokerName": self.broker.name,
            "session": {"phase": sess.phase if sess else "closed", "text": sess.describe() if sess else "",
                        "isOpen": bool(sess and sess.is_open), "minutesToOpen": sess.minutes_to_open if sess else 0,
                        "minutesToClose": sess.minutes_to_close if sess else 0},
            "account": {"equity": equity, "cash": acct.cash if acct else 0.0,
                        "settledCash": self._settled_cash(now), "buyingPower": acct.buying_power if acct else 0.0,
                        "startEquity": start_eq or 0.0, "dayPnl": day_pnl,
                        "dayPnlPct": (day_pnl / start_eq * 100) if start_eq else 0.0, "realizedToday": realized,
                        "unrealized": unreal, "positionsValue": acct.long_market_value if acct else 0.0,
                        "accountNumber": acct.account_number if acct else ""},
            "limits": {"maxPosition": lim["max_position_usd"], "dailyLossLimit": lim["daily_loss_limit_usd"],
                       "dailyLossUsed": max(0.0, -(realized + unreal)), "maxPositions": self.settings.risk.max_positions,
                       "scale": lim["scale"], "riskPerTrade": lim["risk_per_trade_usd"],
                       "compounding": self.settings.compounding.mode},
            "positions": positions,
            "externalPositions": [{"symbol": p.symbol, "qty": p.qty, "value": p.market_value,
                                   "pnl": p.unrealized_pl} for p in self.external_positions],
            "health": {k: v.to_dict() for k, v in self.health.items()},
            "status": status,
            "flags": {"emergencyStop": self.emergency_stop, "autopilot": self.settings.automation.autopilot,
                      "dailyLimitHit": self._daily_limit_day == now.astimezone(ET).date(),
                      "liveAuthorized": live_authorization_status(self.settings)[0]},
            "scanner": {"rows": self.scanner.table(), "universeSize": len(self.universe),
                        "hotCount": len(self.scanner.hotlist), "lastFullScan": to_iso(self.scanner.last_full_scan),
                        "lastHotScan": to_iso(self.scanner.last_hot_scan), "signalsToday": self.signals_today,
                        "fullScanAge": (now - self.scanner.last_full_scan).total_seconds() if self.scanner.last_full_scan else -1,
                        "hotScanAge": (now - self.scanner.last_hot_scan).total_seconds() if self.scanner.last_hot_scan else -1,
                        "hotlist": list(self.scanner.hotlist),
                        "marketTrendUp": self.scanner.market_trend_up},
            "ai": {"provider": self.ai.provider.name, "model": self.ai.provider.model,
                   "decisionMode": self.settings.ai.decision_mode, "canCall": ai_ok, "why": ai_why,
                   "healthy": self.ai.healthy, "message": self.ai.health_msg, "monthCost": spent_ai,
                   "budget": self.settings.ai.monthly_budget_usd, "pending": list(self.pending_ai),
                   "busy": self.ai.busy, "isPaid": bool(getattr(self.ai.provider, "is_paid", False))},
            "regime": self.regime.to_dict(),
            "lastDecision": self.last_decision.to_dict() if self.last_decision else None,
            "equityToday": [{"t": from_iso(r["time"]).astimezone(ET).strftime("%H:%M"), "v": r["equity"]} for r in eq_rows],
        }

    # ================================================================ chart data for the UI
    def chart(self, symbol: str) -> dict:
        f = self.scanner.frames.get(symbol)
        if f is None or f.empty:
            return {"symbol": symbol, "bars": [], "markers": []}
        tail = f.iloc[-180:]
        bars = [{"t": ts.tz_convert(ET).strftime("%H:%M"), "o": float(r["open"]), "h": float(r["high"]),
                 "l": float(r["low"]), "c": float(r["close"]), "v": float(r["volume"]), "vwap": float(r["vwap_s"]),
                 "ema20": float(r["ema20"])} for ts, r in tail.iterrows()]
        markers = []
        day = self.clock.now().astimezone(ET).date().isoformat()
        for t in self.db.query("SELECT * FROM trades WHERE symbol=? AND session_date=? AND entry_qty>0", (symbol, day)):
            for side, tm, px in (("buy", t["entry_time"], t["entry_price"]), ("sell", t["exit_time"], t["exit_price"])):
                if tm and px:
                    markers.append({"side": side, "t": from_iso(tm).astimezone(ET).strftime("%H:%M"), "price": px})
        return {"symbol": symbol, "bars": bars, "markers": markers}
