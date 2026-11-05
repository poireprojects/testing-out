"""Local settings (JSON on disk). Secrets are NOT stored here — see credentials.py."""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from . import paths

log = logging.getLogger(__name__)

STRATEGY_IDS = ("momentum", "trend", "orb", "vwap", "volume_breakout")


class RiskSettings(BaseModel):
    """Deterministic safety rules. Dollar amounts are stated at the starting capital and
    scale with the account when reinvesting (see sizing.py)."""

    starting_capital: float = Field(100.0, gt=0)
    max_position_usd: float = Field(20.0, gt=0)
    max_positions: int = Field(2, ge=1, le=20)
    daily_loss_limit_usd: float = Field(2.0, gt=0)
    max_risk_per_trade_pct: float = Field(1.0, gt=0, le=10)
    default_stop_pct: float = Field(1.5, gt=0, le=20)
    min_stop_pct: float = Field(0.4, gt=0, le=20)
    max_stop_pct: float = Field(3.0, gt=0, le=20)
    max_spread_bps: float = Field(30.0, gt=0)
    max_data_age_seconds: int = Field(90, ge=5)
    no_entries_first_minutes: int = Field(5, ge=0)
    last_entry_minutes_before_close: int = Field(30, ge=0)
    flatten_minutes_before_close: int = Field(10, ge=1)
    max_trades_per_day: int = Field(10, ge=1)
    symbol_cooldown_minutes: int = Field(20, ge=0)
    max_consecutive_losses: int = Field(3, ge=1)
    min_price: float = Field(5.0, ge=0)
    max_price: float = Field(1500.0, gt=0)
    min_order_usd: float = Field(1.0, gt=0)
    close_positions_on_daily_limit: bool = False
    # conservative = only spend settled cash (never risks a good-faith violation)
    settlement_mode: Literal["conservative", "broker"] = "conservative"
    # Legacy FINRA pattern-day-trader guard. The PDT rule was retired on 2026-06-04
    # (Alpaca moved to the intraday-margin framework), so this is off by default.
    pdt_guard: bool = False
    reduce_size_after_losses: bool = True
    close_leftover_positions_at_open: bool = True
    entry_order_type: Literal["limit", "market"] = "limit"
    limit_offset_bps: float = Field(10.0, ge=0, le=100)
    entry_timeout_seconds: int = Field(45, ge=5)


class CompoundingSettings(BaseModel):
    mode: Literal["reinvest", "fixed"] = "reinvest"
    fixed_allocation_usd: float = Field(100.0, gt=0)


class AISettings(BaseModel):
    # rules = quantitative strategies decide alone; ai_confirm = AI must approve each entry
    decision_mode: Literal["rules", "ai_confirm"] = "ai_confirm"
    provider: Literal["ollama", "anthropic"] = "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high"] = "low"
    min_confidence: float = Field(0.6, ge=0, le=1)
    monthly_budget_usd: float = Field(5.0, ge=0)
    timeout_seconds: float = Field(60.0, ge=5)
    review_open_positions: bool = True
    review_interval_minutes: int = Field(10, ge=1)
    recheck_cooldown_minutes: int = Field(10, ge=1)
    max_calls_per_hour: int = Field(60, ge=1)
    max_signal_age_seconds: int = Field(90, ge=10)
    max_price_drift_pct: float = Field(0.5, gt=0)


class StrategySettings(BaseModel):
    enabled: dict[str, bool] = Field(default_factory=lambda: {s: True for s in STRATEGY_IDS})
    # In paper/demo, unvalidated strategies may trade to collect forward-test evidence.
    # Live trading ALWAYS requires backtest evidence.
    paper_allow_unvalidated: bool = True
    min_signal_score: float = Field(0.55, ge=0, le=1)


class AutomationSettings(BaseModel):
    autopilot: bool = True
    minimize_to_tray: bool = True
    launch_at_login: bool = False
    keep_awake_during_market: bool = True
    start_minimized: bool = False
    notify_trades: bool = True
    notify_decisions: bool = True
    notify_errors: bool = True
    notify_risk: bool = True


class DataSettings(BaseModel):
    feed: Literal["iex", "sip"] = "iex"
    universe_size: int = Field(400, ge=20, le=600)
    hotlist_size: int = Field(20, ge=3, le=60)
    full_scan_seconds: int = Field(60, ge=15)
    hot_scan_seconds: int = Field(15, ge=5)
    requests_per_minute: int = Field(180, ge=10)
    monthly_data_cost_usd: float = Field(0.0, ge=0)


class LiveAuthorization(BaseModel):
    authorized_at: str
    phrase: str
    acknowledgements: list[str]
    limits: dict[str, float]


class BrokerSettings(BaseModel):
    mode: Literal["demo", "paper", "live"] = "demo"
    live_authorization: Optional[LiveAuthorization] = None


class WindowSettings(BaseModel):
    x: Optional[int] = None
    y: Optional[int] = None
    width: int = 1380
    height: int = 880
    maximized: bool = False
    page: int = 0


class DemoSettings(BaseModel):
    speed: float = Field(1.0, ge=0.25, le=60)
    seed: int = 7


class AppSettings(BaseModel):
    schema_version: int = 1
    risk: RiskSettings = Field(default_factory=RiskSettings)
    compounding: CompoundingSettings = Field(default_factory=CompoundingSettings)
    ai: AISettings = Field(default_factory=AISettings)
    strategies: StrategySettings = Field(default_factory=StrategySettings)
    automation: AutomationSettings = Field(default_factory=AutomationSettings)
    data: DataSettings = Field(default_factory=DataSettings)
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    window: WindowSettings = Field(default_factory=WindowSettings)
    demo: DemoSettings = Field(default_factory=DemoSettings)
    first_run_done: bool = False

    def copy_deep(self) -> "AppSettings":
        return AppSettings.model_validate(self.model_dump())


# ---------------------------------------------------------------- persistence

def load_settings(path: Path | None = None) -> AppSettings:
    path = path or paths.settings_file()
    if not path.exists():
        return AppSettings()
    try:
        return AppSettings.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValidationError, json.JSONDecodeError, OSError) as exc:
        backup = path.with_suffix(f".broken-{datetime.now():%Y%m%d%H%M%S}.json")
        try:
            shutil.copy2(path, backup)
        except OSError:
            pass
        log.error("settings file unreadable (%s); backed up to %s and using defaults", exc, backup)
        return AppSettings()


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    path = path or paths.settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = settings.model_dump_json(indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".settings-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---------------------------------------------------------- live authorization

# Fields where a larger number means more risk.
_RISKIER_IF_LARGER = (
    "max_position_usd",
    "max_positions",
    "daily_loss_limit_usd",
    "max_risk_per_trade_pct",
    "max_stop_pct",
    "max_trades_per_day",
    "max_consecutive_losses",
    "max_spread_bps",
    "max_data_age_seconds",
)

LIVE_PHRASE = "i authorize real-money trading"
LIVE_ACKS = (
    "i understand this can lose real money and profits are not guaranteed",
    "i reviewed my paper-trading results",
    "i understand stop-loss orders and daily limits do not guarantee a maximum loss",
    "i understand the app only trades while my computer is awake and online",
)


def limits_snapshot(risk: RiskSettings) -> dict[str, float]:
    snap = {k: float(getattr(risk, k)) for k in _RISKIER_IF_LARGER}
    snap["settlement_broker"] = 1.0 if risk.settlement_mode == "broker" else 0.0
    return snap


def authorize_live(settings: AppSettings, phrase: str, acknowledgements: list[str]) -> tuple[bool, str]:
    if phrase.strip().lower() != LIVE_PHRASE:
        return False, f'type exactly: "{LIVE_PHRASE}"'
    missing = [a for a in LIVE_ACKS if a not in acknowledgements]
    if missing:
        return False, "please confirm every statement before enabling live trading"
    settings.broker.live_authorization = LiveAuthorization(
        authorized_at=datetime.now(timezone.utc).isoformat(),
        phrase=LIVE_PHRASE,
        acknowledgements=list(acknowledgements),
        limits=limits_snapshot(settings.risk),
    )
    return True, "live trading authorized"


def live_authorization_status(settings: AppSettings) -> tuple[bool, str]:
    auth = settings.broker.live_authorization
    if auth is None:
        return False, "live trading has not been authorized"
    current = limits_snapshot(settings.risk)
    for key, value in current.items():
        if value > auth.limits.get(key, float("-inf")) + 1e-12:
            return False, f"risk setting '{key}' was raised after authorization — re-authorize to continue"
    return True, f"authorized {auth.authorized_at[:10]}"
