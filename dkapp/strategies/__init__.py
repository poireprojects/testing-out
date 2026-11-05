from __future__ import annotations

from .base import Context, EntryIdea, Strategy  # noqa: F401
from .library import IntradayMomentum, OpeningRangeBreakout, TrendFollowing, VolumeBreakout, VWAPReclaim

ALL_STRATEGIES: dict[str, Strategy] = {
    s.id: s for s in (IntradayMomentum(), TrendFollowing(), OpeningRangeBreakout(), VWAPReclaim(), VolumeBreakout())
}


def get(strategy_id: str) -> Strategy:
    return ALL_STRATEGIES[strategy_id]
