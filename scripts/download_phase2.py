"""Phase 2 data (read-only market data; no trading API calls).

    ALPACA_PAPER_KEY=PK... python scripts/download_phase2.py daily   OUT_DIR
    ALPACA_PAPER_KEY=PK... python scripts/download_phase2.py gapdays OUT_DIR START END CACHE_DIR
    ALPACA_PAPER_KEY=PK... python scripts/download_phase2.py minute  OUT_DIR START END CACHE_DIR   (40 symbols)

daily: IEX daily bars 2022-11-01 → 2026-11-05 for the curated universe (+ index ETFs), one pickle per symbol.
gapdays: 1-minute bars only for symbol-days whose daily bars show a gap ≥ +3% (pre-registered H2
pre-filter; the price/liquidity/rvol5 conditions are applied later by the research code).
Days must be registered in research/ledger.json first; the vault (reserved for future rounds) is refused.
"""
from __future__ import annotations

import os
import pickle
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from stockpilot.backtest.runner import DEFAULT_BACKTEST_SYMBOLS, HistoryCache  # noqa: E402
from stockpilot.clock import MarketCalendar  # noqa: E402
from stockpilot.data.universe import curated_universe  # noqa: E402

VAULT_END = date(2022, 10, 31)
DAILY_START, DAILY_END = date(2022, 11, 1), date(2026, 11, 5)
INDEX = ["SPY", "QQQ", "IWM", "DIA"]


def client():
    key = os.environ["ALPACA_PAPER_KEY"]
    if key.upper().startswith("AK"):
        raise SystemExit("refusing: that looks like a live key")
    from alpaca.data.historical import StockHistoricalDataClient
    return StockHistoricalDataClient(key, os.environ.get("ALPACA_PAPER_SECRET", "proxy-injected"))


def bars(c, symbols, start, end, minute: bool):
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    for attempt in range(5):
        try:
            return c.get_stock_bars(StockBarsRequest(symbol_or_symbols=symbols, feed=DataFeed.IEX, start=start, end=end,
                                                     timeframe=TimeFrame.Minute if minute else TimeFrame.Day)).df
        except Exception as exc:
            print("  retry", attempt + 1, str(exc)[:120], flush=True)
            time.sleep(3 * 2 ** attempt)
    raise RuntimeError("download failed")


def refuse_vault(d: date) -> None:
    if d <= VAULT_END:
        raise SystemExit(f"{d} is in the vault (reserved for future rounds)")


def cmd_daily(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    c = client()
    syms = list(dict.fromkeys(INDEX + [s for s in curated_universe()]))
    start = datetime(DAILY_START.year, DAILY_START.month, DAILY_START.day, tzinfo=timezone.utc)
    end = datetime(DAILY_END.year, DAILY_END.month, DAILY_END.day, 23, tzinfo=timezone.utc)
    for k in range(0, len(syms), 40):
        chunk = [s for s in syms[k:k + 40] if not (out / f"{s.replace('.', '_')}.pkl").exists()]
        if not chunk:
            continue
        df = bars(c, chunk, start, end, minute=False)
        for s in chunk:
            try:
                d = df.xs(s, level=0)[["open", "high", "low", "close", "volume"]].astype(float)
            except KeyError:
                d = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            d.index = pd.to_datetime(d.index).tz_convert("America/New_York").date
            with open(out / f"{s.replace('.', '_')}.pkl", "wb") as f:
                pickle.dump(d, f)
        print(f"daily {k + len(chunk)}/{len(syms)}", flush=True)


def load_daily(daily_dir: Path) -> dict[str, pd.DataFrame]:
    out = {}
    for p in sorted(daily_dir.glob("*.pkl")):
        with open(p, "rb") as f:
            out[p.stem.replace("_", ".")] = pickle.load(f)
    return out


def gap_symbol_days(daily: dict[str, pd.DataFrame], start: date, end: date, gap: float = 0.03) -> dict[date, list[str]]:
    """Pre-filter only (gap ≥ +3% at the open). Uses yesterday's close and today's open — both known at 9:30."""
    out: dict[date, list[str]] = {}
    for s, d in daily.items():
        if s in INDEX or d.empty:
            continue
        prev = d["close"].shift(1)
        g = d["open"] / prev - 1
        for day, v in g.items():
            if start <= day <= end and v >= gap:
                out.setdefault(day, []).append(s)
    return dict(sorted(out.items()))


def cmd_gapdays(out: Path, start: date, end: date, cache_dir: Path) -> None:
    refuse_vault(start)
    daily = load_daily(out)
    days = gap_symbol_days(daily, start, end)
    cache = HistoryCache(cache_dir)
    cal = MarketCalendar()
    c = client()
    n = sum(len(v) for v in days.values())
    print(f"{len(days)} days, {n} gap symbol-days", flush=True)
    for k, (day, syms) in enumerate(days.items()):
        need = [s for s in syms if not cache._p(s, day).exists()]
        sess = cal.session(day)
        if not need or sess is None:
            continue
        df = bars(c, need, sess.open, sess.close, minute=True)
        if df is None or df.empty or not isinstance(df.index, pd.MultiIndex):
            continue  # none of these symbols traded on IEX that day: left missing, never filled in
        for s in need:
            try:
                d = df.xs(s, level=0)[["open", "high", "low", "close", "volume", "vwap"]].astype(float)
            except KeyError:
                continue
            d.index = pd.to_datetime(d.index).tz_convert("UTC")
            cache.put(s, day, d)
        if k % 25 == 0:
            print(f"gap days {k + 1}/{len(days)}", flush=True)


def cmd_minute(start: date, end: date, cache_dir: Path) -> None:
    refuse_vault(start)
    cache = HistoryCache(cache_dir)
    cal = MarketCalendar()
    c = client()
    sessions = []
    d = start
    while d <= end:
        s = cal.session(d)
        if s is not None:
            sessions.append(s)
        d += timedelta(days=1)
    syms = DEFAULT_BACKTEST_SYMBOLS[:40]
    for k, sym in enumerate(syms):
        if all(cache._p(sym, s.day).exists() for s in sessions):
            continue
        # one symbol at a time, in ~3-month chunks (keeps memory small)
        for i in range(0, len(sessions), 63):
            part = sessions[i:i + 63]
            df = bars(c, sym, part[0].open, part[-1].close, minute=True)
            if df is None or df.empty:
                continue
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(sym, level=0)
            df = df[["open", "high", "low", "close", "volume", "vwap"]].astype(float)
            df.index = pd.to_datetime(df.index).tz_convert("UTC")
            for s in part:
                piece = df[(df.index >= pd.Timestamp(s.open).tz_convert("UTC")) & (df.index < pd.Timestamp(s.close).tz_convert("UTC"))]
                if not piece.empty:
                    cache.put(sym, s.day, piece)
        print(f"minute {k + 1}/{len(syms)} {sym}", flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "daily":
        cmd_daily(Path(sys.argv[2]))
    elif cmd == "gapdays":
        cmd_gapdays(Path(sys.argv[2]), date.fromisoformat(sys.argv[3]), date.fromisoformat(sys.argv[4]), Path(sys.argv[5]))
    elif cmd == "minute":
        cmd_minute(date.fromisoformat(sys.argv[3]), date.fromisoformat(sys.argv[4]), Path(sys.argv[5]))
