"""Two-stage market scanner.

Stage 1 (every ~60 s): one multi-symbol snapshot request per 100 symbols covers the
whole universe (300–500 names) — cheap enough for the free data plan's rate limit.
Rows are ranked by relative volume, intraday strength, liquidity and spread.

Stage 2 (every ~15 s): the top candidates (the "hot list") get full 1-minute bar
history for today, technical features, and strategy evaluation.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .analysis.features import Baseline, feature_frame, latest_features
from .analysis.indicators import relative_volume
from .clock import SessionDay
from .config import AppSettings
from .data.base import DataUnavailable, MarketData
from .models import Signal, Snapshot
from .strategies import ALL_STRATEGIES, Context

log = logging.getLogger(__name__)


@dataclass
class ScanRow:
    symbol: str
    price: float
    change_pct: float
    change_open_pct: float
    day_volume: float
    rvol: float
    spread_bps: float
    data_age_s: float
    score: float = 0.0
    eligible: bool = True
    flags: list[str] = field(default_factory=list)
    hot: bool = False
    signal: str = ""
    strategy: str = ""
    ai_verdict: str = ""
    rsi: Optional[float] = None
    dist_vwap_pct: Optional[float] = None
    mom15: Optional[float] = None
    spark: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "price": self.price, "changePct": self.change_pct,
            "changeOpenPct": self.change_open_pct, "dayVolume": self.day_volume, "rvol": self.rvol,
            "spreadBps": self.spread_bps if math.isfinite(self.spread_bps) else -1.0, "dataAge": self.data_age_s,
            "score": self.score, "eligible": self.eligible, "flags": ", ".join(self.flags), "hot": self.hot,
            "signal": self.signal, "strategy": self.strategy, "aiVerdict": self.ai_verdict,
            "rsi": -1.0 if self.rsi is None else self.rsi,
            "distVwap": 0.0 if self.dist_vwap_pct is None else self.dist_vwap_pct,
            "mom15": 0.0 if self.mom15 is None else self.mom15,
            "spark": self.spark,
        }


def _rank(values: np.ndarray) -> np.ndarray:
    if len(values) == 0:
        return values
    order = values.argsort().argsort().astype(float)
    return order / max(1, len(values) - 1)


class Scanner:
    def __init__(self, data: MarketData, settings: AppSettings, universe: list[str],
                 baselines: Optional[dict[str, Baseline]] = None):
        self.data = data
        self.settings = settings
        self.universe = list(universe)
        self.baselines: dict[str, Baseline] = baselines or {}
        self.rows: dict[str, ScanRow] = {}
        self.frames: dict[str, pd.DataFrame] = {}
        self.last_full_scan: Optional[datetime] = None
        self.last_hot_scan: Optional[datetime] = None
        self.market_trend_up: Optional[bool] = None
        self.hotlist: list[str] = []

    # --------------------------------------------------------------- stage 1
    def full_scan(self, now: datetime, session: Optional[SessionDay], always: Iterable[str] = ()) -> list[ScanRow]:
        snaps = self.data.get_snapshots(self.universe)
        self.last_full_scan = now
        minutes = max(1.0, (now - session.open).total_seconds() / 60.0) if session and now > session.open else 0.0
        sess_minutes = int((session.close - session.open).total_seconds() // 60) if session else 390
        rows: list[ScanRow] = []
        for sym in self.universe:
            s = snaps.get(sym)
            if s is None:
                continue
            rows.append(self._row_from_snapshot(s, now, minutes, sess_minutes))
        self._score(rows)
        eligible = sorted((r for r in rows if r.eligible), key=lambda r: r.score, reverse=True)
        keep = [r.symbol for r in eligible[: self.settings.data.hotlist_size]]
        for a in always:
            if a not in keep and a in snaps:
                keep.append(a)
        self.hotlist = keep
        old = self.rows
        self.rows = {}
        for r in rows:
            r.hot = r.symbol in keep
            prev = old.get(r.symbol)
            if prev is not None:  # keep stage-2 enrichments until refreshed
                r.signal, r.strategy, r.ai_verdict = prev.signal, prev.strategy, prev.ai_verdict
                r.rsi, r.dist_vwap_pct, r.mom15, r.spark = prev.rsi, prev.dist_vwap_pct, prev.mom15, prev.spark
            self.rows[r.symbol] = r
        return rows

    def _row_from_snapshot(self, s: Snapshot, now: datetime, minutes: float, sess_minutes: int) -> ScanRow:
        rs = self.settings.risk
        base = self.baselines.get(s.symbol)
        prev_close = s.prev_close or (base.prev_close if base else 0.0)
        chg = (s.price / prev_close - 1) * 100 if prev_close > 0 else 0.0
        chg_open = (s.price / s.day_open - 1) * 100 if s.day_open > 0 else 0.0
        adv = base.avg_daily_volume if base and base.avg_daily_volume > 0 else s.prev_day_volume
        rvol = relative_volume(s.day_volume, adv, minutes, sess_minutes) if minutes > 0 else 0.0
        spread = s.quote.spread_bps if s.quote else float("inf")
        age = max(0.0, (now - s.timestamp).total_seconds())
        row = ScanRow(s.symbol, s.price, chg, chg_open, s.day_volume, rvol, spread, age)
        if s.price < rs.min_price:
            row.flags.append("price below minimum")
        if s.price > rs.max_price:
            row.flags.append("price above maximum")
        if not math.isfinite(spread):
            row.flags.append("no quote")
        elif spread > rs.max_spread_bps:
            row.flags.append("spread too wide")
        if age > rs.max_data_age_seconds:
            row.flags.append("stale data")
        if minutes > 0 and s.day_volume <= 0:
            row.flags.append("no trades today")
        row.eligible = not row.flags
        return row

    @staticmethod
    def _score(rows: list[ScanRow]) -> None:
        el = [r for r in rows if r.eligible]
        if not el:
            return
        rvol = _rank(np.array([r.rvol for r in el]))
        up = _rank(np.array([max(r.change_open_pct, 0.0) for r in el]))
        mag = _rank(np.array([abs(r.change_pct) for r in el]))
        dollar = _rank(np.array([math.log1p(r.day_volume * r.price) for r in el]))
        tight = _rank(np.array([-r.spread_bps for r in el]))
        for k, r in enumerate(el):
            r.score = float(0.35 * rvol[k] + 0.25 * up[k] + 0.15 * mag[k] + 0.15 * dollar[k] + 0.10 * tight[k])

    # --------------------------------------------------------------- stage 2
    def hot_scan(self, now: datetime, session: SessionDay, symbols: Optional[list[str]] = None) -> dict[str, pd.DataFrame]:
        symbols = symbols or self.hotlist
        if not symbols:
            return {}
        bars = self.data.get_intraday_bars(symbols, session.open, now)
        self.last_hot_scan = now
        sess_minutes = int((session.close - session.open).total_seconds() // 60)
        frames: dict[str, pd.DataFrame] = {}
        for sym, df in bars.items():
            if df is None or df.empty:
                continue
            df = df[df.index >= pd.Timestamp(session.open)]
            if df.empty:
                continue
            f = feature_frame(df, session.open, self.baselines.get(sym), sess_minutes)
            frames[sym] = f
            row = self.rows.get(sym)
            if row is not None:
                lf = latest_features(f)
                row.rsi = lf.get("rsi14")
                row.dist_vwap_pct = lf.get("distance_from_vwap_pct")
                row.mom15 = lf.get("momentum_15m_pct")
                row.spark = [round(float(x), 4) for x in f["close"].iloc[-60:].tolist()]
        self.frames.update(frames)
        spy = frames.get("SPY")
        if spy is not None and not spy.empty:
            self.market_trend_up = bool(spy["close"].iat[-1] > spy["vwap_s"].iat[-1])
        return frames

    def signals(self, now: datetime, frames: dict[str, pd.DataFrame], enabled: Iterable[str],
                exclude: Iterable[str] = ()) -> list[Signal]:
        """Evaluate enabled strategies on the newest completed bar of each hot symbol."""
        enabled = [s for s in enabled if s in ALL_STRATEGIES]
        excluded = set(exclude)
        out: list[Signal] = []
        for sym, f in frames.items():
            row = self.rows.get(sym)
            if row is not None:
                row.signal = "watch" if row.hot else ""
                row.strategy = ""
            if sym in excluded or f.empty or (row is not None and not row.eligible):
                continue
            last_bar_end = f.index[-1] + pd.Timedelta(minutes=1)
            if (now - last_bar_end.to_pydatetime()) > timedelta(minutes=3):
                continue  # bars too old to act on
            i = len(f) - 1
            ctx = Context(sym, self.market_trend_up)
            best: Optional[Signal] = None
            fired: list[str] = []
            for sid in enabled:
                idea = ALL_STRATEGIES[sid].entry(f, i, ctx)
                if idea is None:
                    continue
                fired.append(sid)
                price = float(f["close"].iat[i])
                sig = Signal(sym, sid, now, price, idea.stop_price, idea.target_price, idea.score,
                             idea.reasons, idea.max_hold_minutes, latest_features(f))
                if best is None or sig.score > best.score:
                    best = sig
            if best is not None:
                best.agreeing = fired
                out.append(best)
                if row is not None:
                    row.signal, row.strategy = "buy", best.strategy
        out.sort(key=lambda s: s.score, reverse=True)
        return out

    def table(self, limit: int = 120) -> list[dict]:
        rows = sorted(self.rows.values(), key=lambda r: (r.hot, r.eligible, r.score), reverse=True)
        return [r.to_dict() for r in rows[:limit]]


def load_baselines(data: MarketData, symbols: list[str], now: datetime, lookback_days: int = 30) -> dict[str, Baseline]:
    """Average daily volume / previous close / typical daily range from completed daily bars."""
    out: dict[str, Baseline] = {}
    try:
        daily = data.get_daily_bars(symbols, now - timedelta(days=lookback_days), now - timedelta(hours=12))
    except DataUnavailable as exc:
        log.warning("could not load daily baselines: %s", exc)
        return out
    for sym, df in daily.items():
        if df is None or df.empty:
            continue
        tail = df.iloc[-20:]
        rng = ((tail["high"] - tail["low"]) / tail["close"] * 100).mean()
        out[sym] = Baseline(float(tail["volume"].mean()), float(tail["close"].iat[-1]), float(rng))
    return out
