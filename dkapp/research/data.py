"""Load research days from a bar cache without touching the network."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from ..backtest.engine import DayInput
from ..backtest.runner import DEFAULT_BACKTEST_SYMBOLS, HistoryCache, historical_days
from ..clock import MarketCalendar


class _CacheOnly:
    def get_intraday_bars(self, *a, **k):
        raise RuntimeError("research runs read cached bars only (no network, no credentials)")


def load_cached_days(cache_dir: Path, last_day: date, n_days: int,
                     symbols: Optional[list[str]] = None) -> list[DayInput]:
    days = historical_days(_CacheOnly(), MarketCalendar(), symbols or DEFAULT_BACKTEST_SYMBOLS[:40], last_day, n_days,
                           cache=HistoryCache(Path(cache_dir)))
    return sorted(days, key=lambda d: d.day)
