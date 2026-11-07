"""Bulk-download IEX 1-minute bars into the research cache (one file per symbol-day, HistoryCache format).

    ALPACA_PAPER_KEY=PK... ALPACA_PAPER_SECRET=... python scripts/download_history.py CACHE_DIR 2025-09-02 2026-08-26

Paper keys only (market data is the same for paper and live; a live-looking AK... key is refused).
Only regular-session bars are kept; missing symbol-days are left missing (never filled in).
Register the period in research/ledger.json BEFORE downloading it.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from stockpilot.backtest.runner import DEFAULT_BACKTEST_SYMBOLS, HistoryCache  # noqa: E402
from stockpilot.clock import MarketCalendar  # noqa: E402


def main(cache_dir: str, start: str, end: str, symbols: list[str]) -> int:
    key, secret = os.environ["ALPACA_PAPER_KEY"], os.environ.get("ALPACA_PAPER_SECRET", "x")
    if key.upper().startswith("AK"):
        raise SystemExit("refusing: that looks like a live key")
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    client = StockHistoricalDataClient(key, secret)
    cal = MarketCalendar()
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    sessions = []
    d = d0
    while d <= d1:
        s = cal.session(d)
        if s is not None:
            sessions.append(s)
        d += timedelta(days=1)
    cache = HistoryCache(Path(cache_dir))
    print(f"{len(sessions)} sessions, {len(symbols)} symbols", flush=True)
    for k, sym in enumerate(symbols):
        if all(cache._p(sym, s.day).exists() for s in sessions):
            continue
        t0 = time.monotonic()
        for attempt in range(5):
            try:
                req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Minute, feed=DataFeed.IEX,
                                       start=sessions[0].open, end=sessions[-1].close)
                df = client.get_stock_bars(req).df
                break
            except Exception as exc:  # rate limits / transient network errors
                print(f"  {sym}: {exc} (retry {attempt + 1})", flush=True)
                time.sleep(2 ** attempt * 3)
        else:
            print(f"  {sym}: giving up", flush=True)
            continue
        if df is None or df.empty:
            print(f"  {sym}: no data", flush=True)
            continue
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(sym, level=0)
        df = df[["open", "high", "low", "close", "volume", "vwap"]].astype(float)
        idx = df.index.tz_convert("UTC") if df.index.tz is not None else df.index.tz_localize("UTC")
        df.index = idx
        n = 0
        for s in sessions:
            part = df[(df.index >= pd.Timestamp(s.open).tz_convert("UTC")) & (df.index < pd.Timestamp(s.close).tz_convert("UTC"))]
            if not part.empty:
                cache.put(sym, s.day, part)
                n += 1
        print(f"[{k + 1}/{len(symbols)}] {sym}: {len(df)} bars, {n}/{len(sessions)} days, {time.monotonic() - t0:.0f}s",
              flush=True)
    return 0


if __name__ == "__main__":
    syms = sys.argv[4].split(",") if len(sys.argv) > 4 else DEFAULT_BACKTEST_SYMBOLS[:40]
    raise SystemExit(main(sys.argv[1], sys.argv[2], sys.argv[3], syms))
