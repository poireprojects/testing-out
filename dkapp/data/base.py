from __future__ import annotations

from datetime import datetime
from typing import Protocol

import pandas as pd

from ..models import Snapshot

BAR_COLUMNS = ["open", "high", "low", "close", "volume", "vwap"]


class DataUnavailable(Exception):
    """Market data could not be retrieved (network, auth, plan limits)."""


class MarketData(Protocol):
    is_demo: bool
    feed_label: str
    coverage_note: str

    def get_snapshots(self, symbols: list[str]) -> dict[str, Snapshot]: ...

    def get_intraday_bars(self, symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]: ...

    def get_daily_bars(self, symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]: ...


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=BAR_COLUMNS, index=pd.DatetimeIndex([], tz="UTC"), dtype=float)


def normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure a UTC DatetimeIndex, sorted, de-duplicated, float columns."""
    if df is None or df.empty:
        return empty_bars()
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, utc=True)
    elif out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")
    if "vwap" not in out.columns:
        out["vwap"] = (out["high"] + out["low"] + out["close"]) / 3
    out = out[BAR_COLUMNS].astype(float)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out
