"""Load research days from a bar cache without touching the network — and never the reserved holdout
by accident: `load_range` refuses RESERVED ledger days unless the caller is the one-time holdout run,
which leaves a marker so the holdout can't quietly be reused."""
from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..backtest.engine import DayInput
from ..backtest.runner import DEFAULT_BACKTEST_SYMBOLS, HistoryCache, historical_days
from ..clock import MarketCalendar
from .ledger import RESERVED, DataLedger

HOLDOUT_MARKER = Path(__file__).resolve().parents[2] / "research" / "HOLDOUT_USED.json"


class _CacheOnly:
    def get_intraday_bars(self, *a, **k):
        raise RuntimeError("research runs read cached bars only (no network, no credentials)")


def load_cached_days(cache_dir: Path, last_day: date, n_days: int,
                     symbols: Optional[list[str]] = None) -> list[DayInput]:
    days = historical_days(_CacheOnly(), MarketCalendar(), symbols or DEFAULT_BACKTEST_SYMBOLS[:40], last_day, n_days,
                           cache=HistoryCache(Path(cache_dir)))
    return sorted(days, key=lambda d: d.day)


def sessions_between(start: date, end: date) -> list[date]:
    cal = MarketCalendar()
    out, d = [], start
    while d <= end:
        if cal.session(d) is not None:
            out.append(d)
        d += timedelta(days=1)
    return out


class ReservedData(PermissionError):
    pass


def load_range(cache_dir: Path, start: date, end: date, purpose: str = "development",
               ledger: Optional[DataLedger] = None, symbols: Optional[list[str]] = None,
               allow_reuse: bool = False, marker: Path = HOLDOUT_MARKER) -> list[DayInput]:
    """Days start..end (the 10 sessions before `start` are used only as warm-up for baselines)."""
    ledger = ledger or DataLedger.load()
    days = sessions_between(start, end)
    reserved = [d for d in days if ledger.status(d) == RESERVED]
    if reserved and purpose != "holdout":
        raise ReservedData(f"{len(reserved)} requested days are the reserved holdout "
                           f"({reserved[0]}..{reserved[-1]}); development code may not load them")
    if purpose == "holdout":
        if marker.exists() and not allow_reuse:
            raise ReservedData(f"the holdout was already used ({marker.read_text()[:200]})")
        if not marker.exists():
            try:
                commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=marker.parents[1], capture_output=True,
                                        text=True).stdout.strip()
            except Exception:
                commit = ""
            marker.write_text(json.dumps({"firstUsed": datetime.now(timezone.utc).isoformat(), "commit": commit,
                                          "days": [str(days[0]), str(days[-1])]}) + "\n")
    return load_cached_days(cache_dir, end, len(days), symbols)
