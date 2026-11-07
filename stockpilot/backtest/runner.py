"""Backtest data preparation, execution and evidence recording.

Real backtests use Alpaca historical 1-minute bars (same feed as live trading, so the
volume-based features mean the same thing) and are cached on disk. Synthetic backtests
use the demo simulator and are clearly labeled: they exercise the machinery but can
never count as evidence for real-money trading.
"""
from __future__ import annotations

import json
import logging
import pickle
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from .. import paths
from ..analysis.features import Baseline
from ..analysis.indicators import session_vwap
from ..clock import ET, MarketCalendar, SessionDay
from ..data.base import DataUnavailable, MarketData
from ..data.demo_data import DemoMarket
from ..storage.db import Database, utcnow_iso
from .engine import BTConfig, DayInput, run_backtest

log = logging.getLogger(__name__)

DEFAULT_BACKTEST_SYMBOLS = ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "SMH", "AAPL", "MSFT", "NVDA", "AMZN",
                            "GOOGL", "META", "TSLA", "AMD", "AVGO", "JPM", "BAC", "XOM", "UNH", "COST", "NFLX", "CRM",
                            "INTC", "MU", "PLTR", "UBER", "BA", "DIS", "PYPL", "SHOP", "COIN", "SOFI", "F", "GM",
                            "T", "PFE", "KO", "WMT"]


def _trading_days(cal: MarketCalendar, end: date, n: int) -> list[SessionDay]:
    out: list[SessionDay] = []
    d = end
    while len(out) < n:
        s = cal.session(d)
        if s is not None:
            out.append(s)
        d -= timedelta(days=1)
    return sorted(out, key=lambda s: s.day)


def _spy_trend(spy: Optional[pd.DataFrame], session_minutes: int, open_ts: pd.Timestamp) -> Optional[np.ndarray]:
    if spy is None or spy.empty:
        return None
    vw = session_vwap(spy)
    above = (spy["close"] > vw).to_numpy()
    mins = ((spy.index - open_ts).total_seconds() // 60).astype(int)
    arr = np.zeros(session_minutes, dtype=bool)
    last = False
    j = 0
    for m in range(session_minutes):
        while j < len(mins) and mins[j] <= m:
            last = bool(above[j])
            j += 1
        arr[m] = last
    return arr


def _baselines_from_history(daily_volume: dict[str, list[tuple[date, float, float]]], day: date) -> dict[str, Baseline]:
    out = {}
    for sym, rows in daily_volume.items():
        prior = [(d, v, c) for d, v, c in rows if d < day][-20:]
        if len(prior) >= 5:
            out[sym] = Baseline(float(np.mean([v for _, v, _ in prior])), float(prior[-1][2]), 0.0)
    return out


def synthetic_days(market: DemoMarket, symbols: list[str], end: date, n_days: int, warmup: int = 10) -> list[DayInput]:
    cal = market.calendar
    sessions = _trading_days(cal, end, n_days + warmup)
    vol_hist: dict[str, list[tuple[date, float, float]]] = {s: [] for s in symbols}
    out: list[DayInput] = []
    for k, sess in enumerate(sessions):
        bars = {s: market.frame(s, sess.day) for s in symbols}
        st = market.daily_stats(sess.day)
        if k >= warmup:
            m = int((sess.close - sess.open).total_seconds() // 60)
            open_ts = pd.Timestamp(sess.open).tz_convert("UTC")
            out.append(DayInput(sess.day, open_ts, m, bars, _baselines_from_history(vol_hist, sess.day),
                                _spy_trend(bars.get("SPY"), m, open_ts)))
        for s in symbols:
            i = market.index.get(s)
            if i is not None and st is not None:
                vol_hist[s].append((sess.day, float(st[i, 4]), float(st[i, 3])))
    return out


class HistoryCache:
    def __init__(self, root: Optional[Path] = None, feed: str = "iex"):
        self.root = (root or paths.data_dir() / "cache" / f"bars_{feed}")
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, sym: str, d: date) -> Path:
        return self.root / f"{sym.replace('.', '_')}_{d.isoformat()}.pkl"

    def get(self, sym: str, d: date) -> Optional[pd.DataFrame]:
        p = self._p(sym, d)
        if p.exists():
            try:
                with p.open("rb") as fh:
                    return pickle.load(fh)
            except Exception:
                return None
        return None

    def put(self, sym: str, d: date, df: pd.DataFrame) -> None:
        with self._p(sym, d).open("wb") as fh:
            pickle.dump(df, fh)


def historical_days(data: MarketData, cal: MarketCalendar, symbols: list[str], end: date, n_days: int,
                    warmup: int = 10, cache: Optional[HistoryCache] = None,
                    progress: Optional[Callable[[float, str], None]] = None) -> list[DayInput]:
    """Load real 1-minute bars (cached) and build day inputs. Never includes today."""
    cache = cache or HistoryCache()
    sessions = _trading_days(cal, end, n_days + warmup)
    need: dict[date, list[str]] = {}
    frames: dict[tuple[str, date], pd.DataFrame] = {}
    for sess in sessions:
        for s in symbols:
            df = cache.get(s, sess.day)
            if df is None:
                need.setdefault(sess.day, []).append(s)
            else:
                frames[(s, sess.day)] = df
    todo = sorted(need)
    for k, d in enumerate(todo):
        sess = cal.session(d)
        try:
            got = data.get_intraday_bars(need[d], sess.open, sess.close)
        except DataUnavailable as exc:
            raise DataUnavailable(f"could not download history for {d}: {exc}") from exc
        for s in need[d]:
            df = got.get(s)
            if df is None:
                continue
            cache.put(s, d, df)
            frames[(s, d)] = df
        if progress:
            progress((k + 1) / max(1, len(todo)) * 0.5, f"downloaded {d.isoformat()}")
    vol_hist: dict[str, list[tuple[date, float, float]]] = {s: [] for s in symbols}
    out: list[DayInput] = []
    for k, sess in enumerate(sessions):
        bars = {s: frames[(s, sess.day)] for s in symbols if (s, sess.day) in frames and not frames[(s, sess.day)].empty}
        if k >= warmup:
            m = int((sess.close - sess.open).total_seconds() // 60)
            open_ts = pd.Timestamp(sess.open).tz_convert("UTC")
            out.append(DayInput(sess.day, open_ts, m, bars, _baselines_from_history(vol_hist, sess.day),
                                _spy_trend(bars.get("SPY"), m, open_ts)))
        for s, df in bars.items():
            vol_hist[s].append((sess.day, float(df["volume"].sum()), float(df["close"].iat[-1])))
    return out


def record_results(db: Database, results: dict, source: str, params: dict) -> int:
    rid = db.insert("backtest_runs", time=utcnow_iso(), source=source, params=json.dumps(params),
                    results=json.dumps(results, default=float))
    for sid, r in results["strategies"].items():
        verdict = r["verdict"]
        if source.startswith("synthetic") and verdict == "evidence":
            verdict = "synthetic_only"  # never counts as real evidence
        db.execute("INSERT INTO strategy_evidence(strategy, verdict, source, updated_at, details) VALUES(?,?,?,?,?) "
                   "ON CONFLICT(strategy) DO UPDATE SET verdict=excluded.verdict, source=excluded.source, "
                   "updated_at=excluded.updated_at, details=excluded.details",
                   (sid, verdict, source, utcnow_iso(), json.dumps({"explanation": r["explanation"],
                                                                    "oos": r["outOfSample"], "run_id": rid})))
    return rid


def latest_results(db: Database) -> Optional[dict]:
    row = db.query_one("SELECT * FROM backtest_runs ORDER BY id DESC LIMIT 1")
    if not row:
        return None
    res = json.loads(row["results"])
    res["source"] = row["source"]
    res["ranAt"] = row["time"]
    res["params"] = json.loads(row["params"] or "{}")
    return res


def run_and_record(db: Database, *, source: str, days: list[DayInput], strategies: list[str], cfg: BTConfig,
                   params: dict, progress: Optional[Callable[[float, str], None]] = None) -> dict:
    res = run_backtest(days, strategies, cfg, progress=(lambda f, m: progress(0.5 + f * 0.5, m)) if progress else None,
                       synthetic=source.startswith("synthetic"), expected_symbols=params.get("symbols"))
    record_results(db, res, source, params)
    res["source"] = source
    return res
