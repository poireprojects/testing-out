"""Bridge between the QML interface and the trading backend.

All trading happens on the engine thread. This controller (main/UI thread) only:
reads the engine's published snapshot, forwards commands into the engine's queue,
and reads history from the database for the trades/performance/AI views.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from .. import APP_NAME, __version__, credentials, paths
from ..ai.engine import build_provider
from ..ai.providers import ANTHROPIC_MODELS, ANTHROPIC_PRICES, OllamaProvider
from ..clock import ET
from ..config import (LIVE_ACKS, LIVE_PHRASE, STRATEGY_IDS, AppSettings, authorize_live, live_authorization_status,
                      save_settings)
from ..glossary import GLOSSARY
from ..performance import performance_report
from ..platform_utils import autostart
from ..platform_utils.keepawake import KeepAwake
from ..runtime import NotConfigured, Runtime, test_alpaca_connection
from ..storage.db import Database, from_iso
from ..strategies import ALL_STRATEGIES
from .models import DictListModel

log = logging.getLogger(__name__)

SCANNER_ROLES = ["symbol", "price", "changePct", "changeOpenPct", "dayVolume", "rvol", "spreadBps", "dataAge", "score",
                 "eligible", "flags", "hot", "signal", "strategy", "aiVerdict", "rsi", "distVwap", "mom15", "spark"]
POSITION_ROLES = ["tradeId", "symbol", "strategy", "status", "qty", "entryPrice", "price", "value", "pnl", "pnlPct",
                  "stop", "target", "protection", "heldMinutes", "reasoning", "exitReason"]
EVENT_ROLES = ["id", "time", "timeText", "level", "category", "title", "message", "symbol"]
DECISION_ROLES = ["id", "time", "timeText", "kind", "symbol", "strategy", "provider", "modelName", "action", "confidence",
                  "evidenceQuality", "reasoning", "risks", "valid", "error", "costUsd", "latencyMs"]
TRADE_ROLES = ["id", "symbol", "strategy", "qty", "entryPrice", "exitPrice", "pnl", "pnlPct", "fees", "entryTime",
               "exitTime", "heldMinutes", "exitReason", "reasoning", "decisionMode"]
ORDER_ROLES = ["id", "timeText", "symbol", "side", "type", "purpose", "status", "qty", "filledQty", "price",
               "limitPrice", "stopPrice", "rejectReason"]


def plain(obj: Any) -> Any:
    """Convert numpy scalars / tuples etc. into plain Python types QML understands."""
    if isinstance(obj, dict):
        return {str(k): plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return obj
    if isinstance(obj, int):
        return obj
    try:
        f = float(obj)
    except (TypeError, ValueError):
        return str(obj)
    if f != f or f in (float("inf"), float("-inf")):
        return 0.0
    return f


def _time_text(iso: Optional[str]) -> str:
    dt = from_iso(iso)
    return dt.astimezone(ET).strftime("%b %d · %H:%M:%S") if dt else ""


def _set_path(d: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    for p in parts[:-1]:
        d = d[p]
    d[parts[-1]] = value


class AppController(QObject):
    stateChanged = Signal()
    settingsChanged = Signal()
    performanceChanged = Signal()
    backtestChanged = Signal()
    chartChanged = Signal()
    credentialsChanged = Signal()
    ollamaModelsChanged = Signal()
    modeChanged = Signal()
    toast = Signal(str, str, str)
    quitRequested = Signal()
    showRequested = Signal()
    aiTestFinished = Signal(bool, str)
    connectionTestFinished = Signal(str, bool, str)
    anthropicKeyFinished = Signal(bool, str)
    setupChanged = Signal()
    setupRequested = Signal()

    def __init__(self, settings: AppSettings, notifier: Optional[Callable[[str, str, str], None]] = None,
                 start_engine: bool = True):
        super().__init__()
        self._settings = settings
        self._notifier = notifier
        self.runtime: Optional[Runtime] = None
        self._state: dict = {"booting": True}
        self._setup: dict = {"busy": False, "progress": 0.0, "message": "", "error": "", "checked": False,
                             "installed": False, "running": False, "hasModel": False, "models": [],
                             "canAutoInstall": False, "model": "", "modelNote": "", "done": False}
        self._seq = 0
        self._last_snap_at: Optional[str] = None
        self._performance: dict = {}
        self._backtest: dict = {"running": False, "progress": 0.0, "message": "", "results": None}
        self._chart: dict = {"symbol": "", "bars": [], "markers": []}
        self._selected = ""
        self._ollama_models: list[str] = []
        self._pending_ui: list[Callable[[], None]] = []
        self._pending_lock = threading.Lock()
        self._not_configured: Optional[str] = None
        self.keepawake = KeepAwake()

        self._scanner = DictListModel(SCANNER_ROLES, key="symbol", parent=self)
        self._positions = DictListModel(POSITION_ROLES, key="tradeId", parent=self)
        self._activity = DictListModel(EVENT_ROLES, key=None, parent=self)
        self._decisions = DictListModel(DECISION_ROLES, key="id", parent=self)
        self._trades = DictListModel(TRADE_ROLES, key="id", parent=self)
        self._orders = DictListModel(ORDER_ROLES, key="id", parent=self)

        self._fast = QTimer(self)
        self._fast.setInterval(500)
        self._fast.timeout.connect(self._poll)
        self._slow = QTimer(self)
        self._slow.setInterval(5000)
        self._slow.timeout.connect(self._refresh_history)
        if start_engine:
            self.start_runtime()
        self._fast.start()
        self._slow.start()
        QTimer.singleShot(300, self._refresh_history)

    # ================================================================ runtime
    def start_runtime(self) -> None:
        self.stop_runtime()
        mode = self._settings.broker.mode
        self._not_configured = None
        try:
            rt = Runtime(self._settings, mode)
            rt.build()
            rt.start()
            self.runtime = rt
        except NotConfigured as exc:
            self._not_configured = str(exc)
            self.runtime = None
        except Exception as exc:
            log.exception("could not start the trading engine")
            self._not_configured = f"couldn't start: {exc}"
            self.runtime = None
        self._seq = 0
        self._activity.set_items([])
        self._load_recent_events()
        self._state = self._empty_state()
        self.stateChanged.emit()
        self.modeChanged.emit()

    def stop_runtime(self) -> None:
        if self.runtime is not None:
            self.runtime.stop(timeout=10)
            self.runtime = None

    @property
    def engine(self):
        return self.runtime.engine if self.runtime else None

    def _db(self) -> Optional[Database]:
        return self.runtime.db if self.runtime else None

    def _run_bg(self, fn: Callable[[], Any], done: Callable[[Any], None]) -> None:
        def work():
            try:
                res = fn()
            except Exception as exc:  # surfaced to the UI
                log.exception("background task failed")
                res = exc
            with self._pending_lock:
                self._pending_ui.append(lambda: done(res))
        threading.Thread(target=work, daemon=True).start()

    def _empty_state(self) -> dict:
        return {"booting": self.runtime is not None, "mode": self._settings.broker.mode,
                "notConfigured": self._not_configured or "", "isDemo": self._settings.broker.mode == "demo",
                "status": {"kind": "error" if self._not_configured else "waiting",
                           "headline": "connect your brokerage" if self._not_configured else "starting up…",
                           "detail": self._not_configured or "warming up the engine"},
                "account": {}, "limits": {}, "positions": [], "health": {}, "flags": {}, "scanner": {"rows": []},
                "ai": {}, "session": {}, "equityToday": []}

    # ================================================================ polling
    def _poll(self) -> None:
        with self._pending_lock:
            jobs, self._pending_ui = self._pending_ui, []
        for j in jobs:
            try:
                j()
            except Exception:
                log.exception("ui job failed")
        if self._backtest.get("_dirty"):
            self._backtest["_dirty"] = False
            self.backtestChanged.emit()
        eng = self.engine
        if eng is None:
            return
        snap = eng.snapshot()
        if snap and snap.get("updatedAt") != self._last_snap_at:
            self._last_snap_at = snap.get("updatedAt")
            snap = plain(snap)
            snap["booting"] = False
            snap["notConfigured"] = ""
            self._state = snap
            self._scanner.set_items(snap.get("scanner", {}).get("rows", []))
            self._positions.set_items(snap.get("positions", []))
            self.stateChanged.emit()
            phase = snap.get("session", {}).get("phase", "closed")
            self.keepawake.set(self._settings.automation.keep_awake_during_market and
                               phase in ("pre_open", "opening", "trading", "wind_down", "flatten"))
        events = eng.drain_events(self._seq)
        if events:
            self._seq = events[-1]["seq"]
            self._activity.prepend([self._event_row(e) for e in reversed(events)])
            for e in events:
                self._maybe_notify(e)

    def _event_row(self, e: dict) -> dict:
        return {"id": e["id"], "time": e["time"], "timeText": _time_text(e["time"]), "level": e["level"],
                "category": e["category"], "title": e["title"], "message": e.get("message") or "",
                "symbol": e.get("symbol") or ""}

    def _load_recent_events(self) -> None:
        db = self._db()
        if db is None:
            return
        rows = db.query("SELECT * FROM events ORDER BY id DESC LIMIT 200")
        self._activity.set_items([self._event_row(r) for r in rows])

    def _maybe_notify(self, e: dict) -> None:
        a = self._settings.automation
        cat, lvl = e["category"], e["level"]
        if not e.get("notify"):
            return
        allowed = ((cat == "trade" and a.notify_trades) or (cat in ("ai", "decision") and a.notify_decisions)
                   or (cat == "risk" and a.notify_risk) or (lvl == "error" and a.notify_errors)
                   or (cat == "system" and a.notify_trades))
        self.toast.emit(lvl, e["title"], e.get("message") or "")
        if allowed and self._notifier:
            self._notifier(e["title"], e.get("message") or "", lvl)

    def _refresh_history(self) -> None:
        db = self._db()
        if db is None:
            return
        try:
            rows = db.query("SELECT * FROM ai_decisions ORDER BY id DESC LIMIT 150")
            self._decisions.set_items([{
                "id": r["id"], "time": r["time"], "timeText": _time_text(r["time"]), "kind": r["kind"],
                "symbol": r["symbol"] or "", "strategy": ALL_STRATEGIES[r["strategy"]].name
                if r["strategy"] in ALL_STRATEGIES else (r["strategy"] or ""),
                "provider": r["provider"] or "", "modelName": r["model"] or "", "action": r["action"] or "",
                "confidence": r["confidence"] if r["confidence"] is not None else -1.0,
                "evidenceQuality": r["evidence_quality"] or "", "reasoning": r["reasoning"] or "",
                "risks": ", ".join(json.loads(r["risks"] or "[]")), "valid": bool(r["valid"]),
                "error": r["error"] or "", "costUsd": r["cost_usd"] or 0.0, "latencyMs": r["latency_ms"] or 0}
                for r in rows])
            orows = db.query("SELECT * FROM orders ORDER BY id DESC LIMIT 150")
            self._orders.set_items([{
                "id": o["id"], "timeText": _time_text(o["created_at"]), "symbol": o["symbol"], "side": o["side"],
                "type": o["type"], "purpose": o["purpose"], "status": o["status"], "qty": o["qty"] or 0.0,
                "filledQty": o["filled_qty"] or 0.0, "price": o["filled_avg_price"] or 0.0,
                "limitPrice": o["limit_price"] or 0.0, "stopPrice": o["stop_price"] or 0.0,
                "rejectReason": o["reject_reason"] or ""} for o in orows])
            perf = performance_report(db, self._settings)
            self._trades.set_items(plain(perf["trades"]))
            self._performance = plain(perf)
            self.performanceChanged.emit()
            if self._backtest.get("results") is None:
                from ..backtest.runner import latest_results
                lr = latest_results(db)
                if lr:
                    self._backtest["results"] = plain(lr)
                    self.backtestChanged.emit()
        except Exception:
            log.exception("history refresh failed")
        if self._selected:
            self._refresh_chart()

    # ================================================================ properties
    def _g_state(self): return self._state
    state = Property("QVariantMap", _g_state, notify=stateChanged)

    def _g_settings(self): return self._settings.model_dump()
    settings = Property("QVariantMap", _g_settings, notify=settingsChanged)

    def _g_perf(self): return self._performance
    performance = Property("QVariantMap", _g_perf, notify=performanceChanged)

    def _g_bt(self): return {k: v for k, v in self._backtest.items() if not k.startswith("_")}
    backtest = Property("QVariantMap", _g_bt, notify=backtestChanged)

    def _g_chart(self): return self._chart
    chart = Property("QVariantMap", _g_chart, notify=chartChanged)

    def _g_mode(self): return self._settings.broker.mode
    mode = Property(str, _g_mode, notify=modeChanged)

    def _g_name(self): return APP_NAME
    appName = Property(str, _g_name, constant=True)

    def _g_ver(self): return __version__
    version = Property(str, _g_ver, constant=True)

    def _g_gloss(self): return GLOSSARY
    glossary = Property("QVariantMap", _g_gloss, constant=True)

    def _g_scanner(self): return self._scanner
    scannerModel = Property(QObject, _g_scanner, constant=True)

    def _g_positions(self): return self._positions
    positionsModel = Property(QObject, _g_positions, constant=True)

    def _g_activity(self): return self._activity
    activityModel = Property(QObject, _g_activity, constant=True)

    def _g_decisions(self): return self._decisions
    decisionsModel = Property(QObject, _g_decisions, constant=True)

    def _g_trades(self): return self._trades
    tradesModel = Property(QObject, _g_trades, constant=True)

    def _g_orders(self): return self._orders
    ordersModel = Property(QObject, _g_orders, constant=True)

    def _g_ollama(self): return self._ollama_models
    ollamaModels = Property("QVariantList", _g_ollama, notify=ollamaModelsChanged)

    def _g_creds(self):
        ok, store = credentials.secure_backend_available()
        return {"paperSaved": credentials.load_alpaca_keys("paper") is not None,
                "liveSaved": credentials.load_alpaca_keys("live") is not None,
                "anthropicSaved": bool(credentials.load_anthropic_key()), "secureStore": ok, "storeName": store}
    credentialsInfo = Property("QVariantMap", _g_creds, notify=credentialsChanged)

    def _g_live(self):
        ok, msg = live_authorization_status(self._settings)
        return {"authorized": ok, "message": msg, "phrase": LIVE_PHRASE, "acks": list(LIVE_ACKS)}
    liveAuth = Property("QVariantMap", _g_live, notify=settingsChanged)

    def _g_static(self):
        return {"strategies": [{"id": s.id, "name": s.name, "summary": s.summary, "explain": s.explain}
                               for s in ALL_STRATEGIES.values()],
                "anthropicModels": [{"id": m, "label": label, "input": ANTHROPIC_PRICES[m][0],
                                     "output": ANTHROPIC_PRICES[m][1]} for m, label in ANTHROPIC_MODELS],
                "paths": {"data": str(paths.data_dir()), "logs": str(paths.log_dir()),
                          "config": str(paths.config_dir())}}
    staticInfo = Property("QVariantMap", _g_static, constant=True)

    # ================================================================ slots: trading
    @Slot()
    def emergencyStop(self) -> None:
        if self.engine:
            self.engine.command("emergency_stop")
        self.toast.emit("error", "emergency stop", "halting trading and closing positions…")

    @Slot()
    def resetEmergency(self) -> None:
        if self.engine:
            self.engine.command("reset_emergency")

    @Slot(bool)
    def setAutopilot(self, on: bool) -> None:
        self.setSetting("automation.autopilot", on)

    @Slot(int)
    def closePosition(self, trade_id: int) -> None:
        if self.engine:
            self.engine.command("close_position", trade_id=trade_id)

    @Slot(str)
    def selectSymbol(self, symbol: str) -> None:
        self._selected = symbol
        self._refresh_chart()

    def _refresh_chart(self) -> None:
        if self.engine and self._selected:
            try:
                self._chart = plain(self.engine.chart(self._selected))
            except Exception:
                log.exception("chart failed")
                self._chart = {"symbol": self._selected, "bars": [], "markers": []}
            self.chartChanged.emit()

    @Slot(str, result=str)
    def explain(self, term: str) -> str:
        return GLOSSARY.get(term.lower(), "")

    # ================================================================ slots: settings
    @Slot(str, "QVariant", result=str)
    def setSetting(self, path: str, value: Any) -> str:
        if path.startswith("broker."):
            return "use the trading-mode controls to change this"
        data = self._settings.model_dump()
        try:
            _set_path(data, path, value)
            new = AppSettings.model_validate(data)
        except Exception as exc:
            msg = str(exc).split("\n")
            return msg[2].strip() if len(msg) > 2 else msg[0]
        r = new.risk
        if r.min_stop_pct > r.max_stop_pct:
            return "minimum stop can't be larger than the maximum stop"
        old = self._settings
        self._settings = new
        save_settings(new)
        if self.engine:
            self.engine.command("settings", settings=new.copy_deep())
        if path.startswith("ai.") and self.engine:
            def swap(e, s=new.copy_deep()):
                e.ai.set_provider(build_provider(s))
                e.timers.ai_health = 0.0
            self.engine.command("call", fn=swap)
        if path == "automation.launch_at_login":
            ok, msg = autostart.set_enabled(bool(value))
            if not ok:
                self.toast.emit("warning", "launch at login", msg)
        if path == "demo.speed" and self.runtime and self.runtime.mode == "demo" and self.runtime.clock:
            self.runtime.clock.set_speed(float(value))
        if path.startswith("data.feed") or path.startswith("data.universe_size") or path == "demo.seed":
            self.toast.emit("info", "saved", "this change takes effect the next time the engine starts")
        was, now_ok = live_authorization_status(old)[0], live_authorization_status(new)[0]
        if was and not now_ok:
            self.toast.emit("warning", "live authorization paused",
                            "you raised a risk limit, so real-money trading needs your re-authorization")
        self.settingsChanged.emit()
        return ""

    @Slot(str, bool, result=str)
    def setStrategyEnabled(self, sid: str, on: bool) -> str:
        if sid not in STRATEGY_IDS:
            return "unknown strategy"
        enabled = dict(self._settings.strategies.enabled)
        enabled[sid] = on
        return self.setSetting("strategies.enabled", enabled)

    @Slot(str, bool, result=str)
    def setMode(self, mode: str, force: bool = False) -> str:
        if mode not in ("demo", "paper", "live"):
            return "unknown mode"
        if mode == self._settings.broker.mode:
            return ""
        if mode in ("paper", "live") and credentials.load_alpaca_keys(mode) is None:
            return f"save and test your Alpaca {mode} keys first"
        if mode == "live":
            ok, msg = live_authorization_status(self._settings)
            if not ok:
                return msg
        if not force and self.engine and any(p["status"] != "pending_entry" for p in self._state.get("positions", [])):
            return "open_positions"
        self._settings.broker.mode = mode  # type: ignore[assignment]
        save_settings(self._settings)
        self._performance = {}
        self._backtest = {"running": False, "progress": 0.0, "message": "", "results": None}
        self.start_runtime()
        self.settingsChanged.emit()
        self.backtestChanged.emit()
        self.toast.emit("info", f"switched to {mode} mode",
                        {"demo": "simulated prices and fake money", "paper": "real prices, fake money",
                         "live": "REAL money — your safety rules are enforced on every trade"}[mode])
        return ""

    # ================================================================ slots: credentials
    @Slot(str, str, str)
    def saveAlpacaKeys(self, env: str, key_id: str, secret: str) -> None:
        ok, store = credentials.secure_backend_available()
        if not ok:
            self.connectionTestFinished.emit(env, False, f"can't store keys securely on this computer: {store}")
            return

        def work():
            res = test_alpaca_connection(env, key_id, secret)
            if res[0]:
                credentials.save_alpaca_keys(env, key_id, secret)
            return res

        def done(res):
            if isinstance(res, Exception):
                self.connectionTestFinished.emit(env, False, str(res))
            else:
                self.connectionTestFinished.emit(env, res[0], res[1] + (" · saved securely" if res[0] else ""))
            self.credentialsChanged.emit()
        self._run_bg(work, done)

    @Slot(str)
    def deleteAlpacaKeys(self, env: str) -> None:
        if self._settings.broker.mode == env:
            self.toast.emit("warning", "keys in use", "switch to another mode before removing these keys")
            return
        credentials.delete_alpaca_keys(env)
        self.credentialsChanged.emit()

    @Slot(str)
    def saveAnthropicKey(self, key: str) -> None:
        ok, store = credentials.secure_backend_available()
        if not ok:
            self.anthropicKeyFinished.emit(False, f"can't store keys securely on this computer: {store}")
            return
        from ..ai.providers import AnthropicProvider

        def work():
            return AnthropicProvider(key.strip(), self._settings.ai.anthropic_model).health()

        def done(res):
            if isinstance(res, Exception):
                self.anthropicKeyFinished.emit(False, str(res))
                return
            if res[0]:
                credentials.save_anthropic_key(key)
                self.setSetting("ai.anthropic_model", self._settings.ai.anthropic_model)  # rebuild provider
            self.anthropicKeyFinished.emit(res[0], res[1] + (" · saved securely" if res[0] else ""))
            self.credentialsChanged.emit()
        self._run_bg(work, done)

    @Slot()
    def deleteAnthropicKey(self) -> None:
        credentials.delete_anthropic_key()
        self.credentialsChanged.emit()
        self.setSetting("ai.provider", "ollama")

    @Slot()
    def testAI(self) -> None:
        s = self._settings.copy_deep()

        def done(res):
            if isinstance(res, Exception):
                self.aiTestFinished.emit(False, str(res))
            else:
                self.aiTestFinished.emit(res[0], res[1])
                if self.engine:
                    self.engine.command("check_ai")
        self._run_bg(lambda: build_provider(s).health(), done)

    @Slot()
    def refreshOllamaModels(self) -> None:
        s = self._settings

        def done(res):
            self._ollama_models = [] if isinstance(res, Exception) else list(res)
            self.ollamaModelsChanged.emit()
        self._run_bg(lambda: OllamaProvider(s.ai.ollama_url, s.ai.ollama_model).list_models(), done)

    # ================================================================ slots: live authorization
    @Slot(str, "QVariantList", result=str)
    def authorizeLive(self, phrase: str, acks: list) -> str:
        if credentials.load_alpaca_keys("live") is None:
            return "save and test your live Alpaca keys first"
        ok, msg = authorize_live(self._settings, phrase, [str(a) for a in acks])
        if not ok:
            return msg
        save_settings(self._settings)
        self.settingsChanged.emit()
        return ""

    @Slot()
    def revokeLive(self) -> None:
        self._settings.broker.live_authorization = None
        save_settings(self._settings)
        if self._settings.broker.mode == "live":
            self.setMode("paper", True)
        self.settingsChanged.emit()

    @Slot(result="QVariantMap")
    def paperSummary(self) -> dict:
        try:
            db = Database(paths.database_file("paper"), "paper")
            rep = performance_report(db, self._settings)
            db.close()
            return {"trades": rep["stats"]["count"], "total": rep["stats"]["total"], "winRate": rep["stats"]["winRate"],
                    "days": rep["tradingDays"], "profitFactor": rep["stats"]["profitFactor"],
                    "validated": [s["name"] for s in rep["byStrategy"] if s["evidence"] == "evidence"]}
        except Exception as exc:
            return {"trades": 0, "total": 0.0, "winRate": 0.0, "days": 0, "profitFactor": 0.0, "validated": [],
                    "error": str(exc)}

    # ================================================================ slots: backtests
    @Slot(int, int)
    def runBacktest(self, days: int = 40, n_symbols: int = 30) -> None:
        if self._backtest.get("running"):
            return
        mode = self._settings.broker.mode
        s = self._settings.copy_deep()
        self._backtest.update({"running": True, "progress": 0.0, "message": "preparing…", "_dirty": True})

        def progress(f: float, msg: str) -> None:
            self._backtest.update({"progress": float(f), "message": msg, "_dirty": True})

        def work():
            from ..backtest.engine import BTConfig
            from ..backtest.runner import DEFAULT_BACKTEST_SYMBOLS, historical_days, run_and_record, synthetic_days
            from ..clock import MarketCalendar
            from ..data.demo_data import DemoMarket
            cal = MarketCalendar()
            end = (datetime.now(timezone.utc).astimezone(ET) - timedelta(days=1)).date()
            syms = DEFAULT_BACKTEST_SYMBOLS[:n_symbols]
            strategies = [k for k, v in s.strategies.enabled.items() if v] or list(STRATEGY_IDS)
            cfg = BTConfig(notional=s.risk.max_position_usd, flatten_minutes_before_close=s.risk.flatten_minutes_before_close,
                           last_entry_minutes_before_close=s.risk.last_entry_minutes_before_close,
                           no_entries_first_minutes=s.risk.no_entries_first_minutes, min_stop_pct=s.risk.min_stop_pct,
                           max_stop_pct=s.risk.max_stop_pct, min_signal_score=s.strategies.min_signal_score)
            if mode == "demo":
                progress(0.05, "generating synthetic demo history…")
                market = DemoMarket(syms, cal, s.demo.seed)
                bt_days = synthetic_days(market, syms, end, days)
                source = "synthetic (demo simulator)"
                dbs = [Database(paths.database_file("demo"), "demo")]
            else:
                from ..data.alpaca_data import AlpacaMarketData
                keys = credentials.load_alpaca_keys(mode)
                if keys is None:
                    raise RuntimeError("no Alpaca keys saved")
                data = AlpacaMarketData(keys, s.data.feed, s.data.requests_per_minute)
                bt_days = historical_days(data, cal, syms, end, days, progress=progress)
                source = f"alpaca historical ({s.data.feed.upper()} feed)"
                dbs = [Database(paths.database_file(m), m) for m in ("paper", "live")]
            res = None
            for i, db in enumerate(dbs):
                if i == 0:
                    res = run_and_record(db, source=source, days=bt_days, strategies=strategies, cfg=cfg,
                                         params={"days": days, "symbols": syms}, progress=progress)
                else:
                    from ..backtest.runner import record_results
                    record_results(db, res, source, {"days": days, "symbols": syms})
                db.close()
            res["ranAt"] = datetime.now(timezone.utc).isoformat()
            return res

        def done(res):
            if isinstance(res, Exception):
                self._backtest.update({"running": False, "message": f"backtest failed: {res}", "_dirty": True})
                self.toast.emit("error", "backtest failed", str(res)[:200])
            else:
                self._backtest.update({"running": False, "progress": 1.0, "message": "done", "results": plain(res),
                                       "_dirty": True})
                self.toast.emit("success", "backtest finished", "results are on the performance page")
            self._refresh_history()
        self._run_bg(work, done)

    # ================================================================ slots: app/window
    @Slot()
    def openLogsFolder(self) -> None:
        autostart.open_folder(paths.log_dir())

    @Slot()
    def openDataFolder(self) -> None:
        autostart.open_folder(paths.data_dir())

    @Slot(int, int, int, int, bool, int)
    def saveWindow(self, x: int, y: int, w: int, h: int, maximized: bool, page: int) -> None:
        win = self._settings.window
        win.x, win.y, win.width, win.height, win.maximized, win.page = x, y, max(900, w), max(600, h), maximized, page
        save_settings(self._settings)

    @Slot(result="QVariantMap")
    def windowGeometry(self) -> dict:
        return self._settings.window.model_dump()

    @Slot()
    def requestQuit(self) -> None:
        st = self._state
        has_pos = any(p.get("status") != "pending_entry" for p in st.get("positions", []))
        if has_pos and st.get("session", {}).get("isOpen"):
            self.showRequested.emit()
            self.quitRequested.emit()
        else:
            self.quitApp(False)

    @Slot(bool)
    def quitApp(self, close_positions: bool) -> None:
        from PySide6.QtWidgets import QApplication
        if close_positions and self.engine:
            def close_all(e):
                for t in e.db.open_trades():
                    e._begin_exit(t, "closed because you quit the app")
            self.engine.command("call", fn=close_all)
            waited = {"n": 0}

            def check():
                waited["n"] += 1
                open_n = len(self.engine.db.open_trades()) if self.engine else 0
                if open_n == 0 or waited["n"] > 40:
                    self._finish_quit()
                else:
                    QTimer.singleShot(500, check)
            QTimer.singleShot(500, check)
            return
        self._finish_quit()

    def _finish_quit(self) -> None:
        from PySide6.QtWidgets import QApplication
        self.keepawake.set(False)
        self.stop_runtime()
        save_settings(self._settings)
        QApplication.quit()

    # ================================================================ guided setup
    def _g_setup(self): return dict(self._setup)
    setupState = Property("QVariantMap", _g_setup, notify=setupChanged)

    def _setup_update(self, **kw) -> None:
        """Thread-safe: background work queues the change for the UI thread."""
        with self._pending_lock:
            self._pending_ui.append(lambda: (self._setup.update(kw), self.setupChanged.emit()))

    @Slot()
    def openSetup(self) -> None:
        self.setupRequested.emit()

    @Slot(str)
    def openUrl(self, url: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))

    @Slot()
    def checkSetup(self) -> None:
        from .. import setup_assistant as sa
        url = self._settings.ai.ollama_url

        def work():
            st = sa.status(url)
            model, note = sa.recommended_model()
            return st, model, note

        def done(res):
            if isinstance(res, Exception):
                self._setup.update(error=str(res))
            else:
                st, model, note = res
                self._setup.update(checked=True, installed=st.installed, running=st.running, models=st.models,
                                   hasModel=st.has(model) or st.has(self._settings.ai.ollama_model),
                                   canAutoInstall=st.can_auto_install, model=model, modelNote=note)
            self.setupChanged.emit()
        self._run_bg(work, done)

    @Slot()
    def setupLocalAI(self) -> None:
        """One click: install Ollama if needed, start it, download the recommended model, switch the app to it."""
        if self._setup.get("busy"):
            return
        from .. import setup_assistant as sa
        url = self._settings.ai.ollama_url
        self._setup.update(busy=True, error="", progress=0.0, message="checking this computer…")
        self.setupChanged.emit()

        def prog(f: float, msg: str) -> None:
            self._setup_update(progress=f, message=msg)

        def work():
            st = sa.status(url)
            model = sa.recommended_model()[0]
            if st.has(self._settings.ai.ollama_model):
                model = self._settings.ai.ollama_model
            if not st.installed:
                msg = sa.install(prog)
                prog(-1.0, msg)
                if not sa.wait_until_running(url, seconds=600):
                    return False, "Ollama isn't running yet. finish its installer, then click “set up for me” again."
            elif not st.running:
                prog(-1.0, "starting Ollama…")
                sa.start()
                if not sa.wait_until_running(url, seconds=45):
                    return False, "couldn't start Ollama. open the Ollama app once, then try again."
            if not sa.status(url).has(model):
                sa.pull_model(url, model, prog)
            return True, model

        def done(res):
            if isinstance(res, Exception):
                self._setup.update(busy=False, error=f"{res}")
            elif not res[0]:
                self._setup.update(busy=False, error=res[1])
            else:
                model = res[1]
                self.setSetting("ai.provider", "ollama")
                self.setSetting("ai.ollama_model", model)
                self.setSetting("ai.decision_mode", "ai_confirm")
                self._setup.update(busy=False, progress=1.0, done=True, installed=True, running=True, hasModel=True,
                                   message=f"local AI is ready ({model}). it costs nothing to run.")
                if self.engine:
                    self.engine.command("check_ai")
            self.setupChanged.emit()
            self.refreshOllamaModels()
        self._run_bg(work, done)

    @Slot()
    def markFirstRunDone(self) -> None:
        self._settings.first_run_done = True
        save_settings(self._settings)
        self.settingsChanged.emit()

    def shutdown(self) -> None:
        self._fast.stop()
        self._slow.stop()
        self.keepawake.set(False)
        self.stop_runtime()
