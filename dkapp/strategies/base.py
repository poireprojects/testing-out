"""Strategy interface. Strategies are long-only, rules-based and evaluated on 1-minute
feature frames. They never see future bars: `entry(f, i)` may only read rows <= i."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class EntryIdea:
    score: float
    stop_price: float
    target_price: float
    reasons: list[str] = field(default_factory=list)
    max_hold_minutes: int = 90


@dataclass
class Context:
    symbol: str
    market_trend_up: Optional[bool] = None  # SPY above its VWAP?
    session_minutes: int = 390


class Strategy:
    id: str = "base"
    name: str = "base"
    summary: str = ""
    explain: str = ""

    def entry(self, f: pd.DataFrame, i: int, ctx: Context) -> Optional[EntryIdea]:
        raise NotImplementedError

    def exit(self, f: pd.DataFrame, i: int, entry_price: float, entry_index: Optional[int]) -> Optional[str]:
        return None

    # helpers
    @staticmethod
    def val(f: pd.DataFrame, col: str, i: int) -> float:
        try:
            v = float(f[col].iat[i])
        except (KeyError, IndexError, TypeError, ValueError):
            return math.nan
        return v

    @staticmethod
    def ok(*vals: float) -> bool:
        return all(v is not None and math.isfinite(v) for v in vals)


def clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def risk_reward_target(price: float, stop: float, rr: float) -> float:
    return price + rr * max(price - stop, 0.0)
