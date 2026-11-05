"""Alpaca market data (alpaca-py `StockHistoricalDataClient`).

Free "Basic" plan facts (verified Nov 2026 from Alpaca's docs/search results):
  * real-time data comes from the IEX exchange only (a small slice of U.S. volume);
  * full consolidated (SIP) data is only available 15 minutes delayed;
  * ~200 historical requests per minute; websocket limited to 30 symbols.
We therefore poll multi-symbol REST endpoints (snapshots, bars) under a token bucket
instead of streaming 400+ symbols.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Iterable, Optional

import pandas as pd

from ..credentials import AlpacaKeys
from ..models import Quote, Snapshot
from .base import DataUnavailable, empty_bars, normalize_bars
from .ratelimit import TokenBucket

log = logging.getLogger(__name__)

SNAPSHOT_BATCH = 100
BARS_BATCH = 50

FEED_LABELS = {
    "iex": "IEX real-time (one exchange, ~2–3% of U.S. volume)",
    "sip": "SIP consolidated feed (all U.S. exchanges — paid plan)",
}
COVERAGE_NOTES = {
    "iex": ("Prices and volumes come from the IEX exchange only. They track the overall market closely for "
            "liquid stocks, but volume numbers are much smaller than the full market and quotes are IEX's, "
            "not the national best bid/offer."),
    "sip": "Full consolidated U.S. market data (requires Alpaca's paid Algo Trader Plus plan).",
}


def _chunks(items: list[str], n: int) -> Iterable[list[str]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _f(x, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


class AlpacaMarketData:
    is_demo = False

    def __init__(self, keys: AlpacaKeys, feed: str = "iex", requests_per_minute: int = 180, client=None):
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient

        self.feed_name = feed
        self._feed = DataFeed.SIP if feed == "sip" else DataFeed.IEX
        self.client = client or StockHistoricalDataClient(keys.key_id, keys.secret)
        self.limiter = TokenBucket(requests_per_minute)
        self.feed_label = FEED_LABELS[feed]
        self.coverage_note = COVERAGE_NOTES[feed]
        self.requests_made = 0

    # ------------------------------------------------------------------ helpers
    def _call(self, fn, request, cost: int = 1):
        self.limiter.acquire(cost)
        self.requests_made += cost
        try:
            return fn(request)
        except Exception as exc:  # APIError, requests exceptions, timeouts
            raise DataUnavailable(f"market data request failed: {exc}") from exc

    # ---------------------------------------------------------------- snapshots
    def get_snapshots(self, symbols: list[str]) -> dict[str, Snapshot]:
        from alpaca.data.requests import StockSnapshotRequest

        out: dict[str, Snapshot] = {}
        for batch in _chunks(list(symbols), SNAPSHOT_BATCH):
            res = self._call(self.client.get_stock_snapshot,
                             StockSnapshotRequest(symbol_or_symbols=batch, feed=self._feed))
            for sym, snap in (res or {}).items():
                s = convert_snapshot(sym, snap)
                if s is not None:
                    out[sym] = s
        return out

    # --------------------------------------------------------------------- bars
    def _bars(self, symbols: list[str], start: datetime, end: datetime, timeframe) -> dict[str, pd.DataFrame]:
        from alpaca.data.requests import StockBarsRequest

        out: dict[str, pd.DataFrame] = {}
        for batch in _chunks(list(symbols), BARS_BATCH):
            req = StockBarsRequest(symbol_or_symbols=batch, timeframe=timeframe, start=start, end=end,
                                   feed=self._feed, adjustment="raw", limit=None)
            res = self._call(self.client.get_stock_bars, req, cost=1)
            data = getattr(res, "data", {}) or {}
            for sym in batch:
                bars = data.get(sym) or []
                if not bars:
                    out[sym] = empty_bars()
                    continue
                df = pd.DataFrame(
                    {
                        "open": [_f(b.open) for b in bars],
                        "high": [_f(b.high) for b in bars],
                        "low": [_f(b.low) for b in bars],
                        "close": [_f(b.close) for b in bars],
                        "volume": [_f(b.volume) for b in bars],
                        "vwap": [_f(b.vwap, _f(b.close)) for b in bars],
                    },
                    index=pd.DatetimeIndex([b.timestamp for b in bars]),
                )
                out[sym] = normalize_bars(df)
        return out

    def get_intraday_bars(self, symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
        from alpaca.data.timeframe import TimeFrame
        return self._bars(symbols, start, end, TimeFrame.Minute)

    def get_daily_bars(self, symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
        from alpaca.data.timeframe import TimeFrame
        return self._bars(symbols, start, end, TimeFrame.Day)


def convert_snapshot(symbol: str, snap) -> Optional[Snapshot]:
    """Convert an alpaca-py Snapshot model into our Snapshot (defensive about missing parts)."""
    if snap is None:
        return None
    trade = getattr(snap, "latest_trade", None)
    q = getattr(snap, "latest_quote", None)
    mb = getattr(snap, "minute_bar", None)
    db = getattr(snap, "daily_bar", None)
    pdb = getattr(snap, "previous_daily_bar", None)

    quote = None
    if q is not None and _f(q.bid_price) > 0 and _f(q.ask_price) > 0:
        quote = Quote(_f(q.bid_price), _f(q.ask_price), _f(q.bid_size), _f(q.ask_size), q.timestamp)

    price, ts = 0.0, None
    if trade is not None and _f(trade.price) > 0:
        price, ts = _f(trade.price), trade.timestamp
    if quote is not None and (ts is None or quote.timestamp > ts) and quote.spread_bps < 50:
        price, ts = quote.mid, quote.timestamp
    if price <= 0 and mb is not None:
        price, ts = _f(mb.close), mb.timestamp
    if price <= 0 or ts is None:
        return None

    prev_close = _f(pdb.close) if pdb is not None else 0.0
    today = ts.astimezone(timezone.utc).date()
    day_open = day_high = day_low = day_vol = 0.0
    if db is not None:
        if db.timestamp.astimezone(timezone.utc).date() == today:
            day_open, day_high, day_low, day_vol = _f(db.open), _f(db.high), _f(db.low), _f(db.volume)
        else:  # daily bar is still yesterday's: no trades yet today
            prev_close = _f(db.close) or prev_close
    return Snapshot(
        symbol=symbol,
        price=price,
        timestamp=ts,
        prev_close=prev_close,
        day_open=day_open,
        day_high=day_high,
        day_low=day_low,
        day_volume=day_vol,
        prev_day_volume=_f(pdb.volume) if pdb is not None else 0.0,
        minute_close=_f(mb.close) if mb is not None else price,
        minute_volume=_f(mb.volume) if mb is not None else 0.0,
        minute_time=mb.timestamp if mb is not None else None,
        quote=quote,
    )
