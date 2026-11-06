"""Market regime + opportunity ranking: compare every candidate before allocating capital.

The ranker is deterministic and explainable. It never treats an AI confidence number as
evidence of profit; the only "edge" evidence it uses is measured results after costs
(real-data backtests and paper forward tests).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .models import Signal
from .risk.fees import CostModel
from .strategies import ALL_STRATEGIES

# Which strategies suit which market regime (1.0 = natural fit, 0 = don't trade it there).
REGIME_FIT = {
    "trending up":   {"momentum": 1.0, "trend": 1.0, "orb": 1.0, "vwap": 0.8, "volume_breakout": 1.0},
    "choppy":        {"momentum": 0.3, "trend": 0.4, "orb": 0.5, "vwap": 1.0, "volume_breakout": 0.5},
    "risk-off":      {"momentum": 0.0, "trend": 0.0, "orb": 0.2, "vwap": 0.4, "volume_breakout": 0.2},
    "volatile":      {"momentum": 0.5, "trend": 0.4, "orb": 0.6, "vwap": 0.6, "volume_breakout": 0.5},
    "unknown":       {"momentum": 0.7, "trend": 0.7, "orb": 0.7, "vwap": 0.7, "volume_breakout": 0.7},
}
REGIME_TEXT = {
    "trending up": "the market is rising broadly — momentum-style setups have the wind at their back",
    "choppy": "the market is going sideways — breakouts often fail, so only the clearest setups count",
    "risk-off": "the market is falling broadly — buying here fights the tide, so the bar is very high",
    "volatile": "the market is swinging unusually hard — stops get hit more easily",
    "unknown": "not enough market data yet to judge conditions",
}


MIN_STOP_COST_MULTIPLE = 4.0  # with a 2:1 target this leaves ≥1.4:1 after costs (3.3× is the break-even for 1.3:1)
REWARD_MULTIPLE = 2.0


def cost_aware_bracket(price: float, stop: float, target: float, cost_pct: float) -> tuple[float, float, bool]:
    """Widen a too-tight stop (and its target, keeping 2:1) so costs can't swamp the trade.
    Dollar risk stays capped by position sizing, so this never loosens a risk limit."""
    if price <= 0:
        return stop, target, False
    stop_pct = (price - stop) / price * 100
    min_stop = MIN_STOP_COST_MULTIPLE * cost_pct
    if stop_pct >= min_stop:
        return stop, target, False
    new_stop = price * (1 - min_stop / 100)
    new_target = max(target, price * (1 + REWARD_MULTIPLE * min_stop / 100))
    return new_stop, new_target, True


@dataclass
class Regime:
    label: str
    spy_above_vwap: Optional[bool]
    spy_change_pct: Optional[float]
    breadth_pct: Optional[float]   # share of liquid stocks up since the open
    vol_ratio: Optional[float]     # SPY's range today vs its typical daily range

    @property
    def text(self) -> str:
        return REGIME_TEXT[self.label]

    def to_dict(self) -> dict:
        return {"label": self.label, "text": self.text, "spyAboveVwap": self.spy_above_vwap,
                "spyChangePct": self.spy_change_pct, "breadthPct": self.breadth_pct, "volRatio": self.vol_ratio}


def classify_regime(spy_above_vwap: Optional[bool], spy_change_pct: Optional[float], breadth_pct: Optional[float],
                    vol_ratio: Optional[float]) -> Regime:
    if spy_above_vwap is None and breadth_pct is None:
        label = "unknown"
    elif vol_ratio is not None and vol_ratio >= 1.8:
        label = "volatile"
    elif (spy_change_pct is not None and spy_change_pct <= -0.6) or (breadth_pct is not None and breadth_pct < 30
                                                                    and spy_above_vwap is False):
        label = "risk-off"
    elif spy_above_vwap and (breadth_pct is None or breadth_pct >= 50):
        label = "trending up"
    else:
        label = "choppy"
    return Regime(label, spy_above_vwap, spy_change_pct, breadth_pct, vol_ratio)


@dataclass
class Evidence:
    """Measured results for a strategy, after costs. Never synthetic."""
    verdict: str = "untested"          # evidence | no_edge | insufficient | untested | synthetic_only
    avg_return_pct: Optional[float] = None
    trades: int = 0
    source: str = ""


@dataclass
class Candidate:
    signal: Signal
    score: float = 0.0
    take: bool = False
    reasons: list[str] = field(default_factory=list)   # why it's ranked where it is
    blockers: list[str] = field(default_factory=list)  # why it's not taken
    reward_after_cost_pct: float = 0.0
    cost_pct: float = 0.0
    rr_after_cost: float = 0.0
    quant: Optional[dict] = None   # the statistical model's assessment (shadow unless gating is on)

    def to_dict(self) -> dict:
        s = self.signal
        return {"symbol": s.symbol, "strategy": ALL_STRATEGIES[s.strategy].name if s.strategy in ALL_STRATEGIES else s.strategy,
                "strategyId": s.strategy, "score": round(self.score, 3), "take": self.take,
                "reasons": self.reasons, "blockers": self.blockers, "costPct": round(self.cost_pct, 3),
                "rewardAfterCostPct": round(self.reward_after_cost_pct, 3), "rrAfterCost": round(self.rr_after_cost, 2),
                "agreeing": len(s.agreeing) or 1, "stopPct": round(s.stop_pct, 2), "targetPct": round(s.target_pct, 2),
                "quant": self.quant}


@dataclass
class Decision:
    regime: Regime
    candidates: list[Candidate]
    chosen: list[Candidate]
    free_slots: int
    summary: str

    def to_dict(self) -> dict:
        return {"regime": self.regime.to_dict(), "freeSlots": self.free_slots, "summary": self.summary,
                "candidates": [c.to_dict() for c in self.candidates],
                "chosen": [c.signal.symbol for c in self.chosen]}


class OpportunityRanker:
    MIN_RR_AFTER_COST = 1.3       # potential gain must beat potential loss by 30% after costs
    MAX_COST_SHARE = 0.35         # costs may eat at most 35% of the target
    TAKE_THRESHOLD = 0.55

    def __init__(self, costs: Optional[CostModel] = None, max_stop_pct: Optional[float] = None,
                 max_spread_bps: Optional[float] = None):
        self.costs = costs or CostModel()
        # the risk engine's hard limits, so a slot is never given to a trade it would refuse anyway
        self.max_stop_pct = max_stop_pct
        self.max_spread_bps = max_spread_bps

    def score(self, sig: Signal, regime: Regime, evidence: Evidence, notional: float, spread_bps: float,
              dollar_volume_rank: float = 0.5) -> Candidate:
        c = Candidate(sig)
        cost = self.costs.round_trip_cost_pct(max(notional, 1.0), sig.price, spread_bps)
        stop, target, widened = cost_aware_bracket(sig.price, sig.stop_price, sig.target_price, cost)
        if widened:
            sig.stop_price, sig.target_price = stop, target
            c.reasons.append("stop widened so trading costs can't swamp it (dollar risk is still capped by sizing)")
        reward, risk = sig.target_pct, sig.stop_pct
        c.cost_pct = cost
        c.reward_after_cost_pct = reward - cost
        c.rr_after_cost = (reward - cost) / (risk + cost) if risk + cost > 0 else 0.0
        fit = REGIME_FIT.get(regime.label, REGIME_FIT["unknown"]).get(sig.strategy, 0.5)
        agree = len(sig.agreeing) or 1

        # evidence: measured results only; unknown is neutral, proven-bad is a hard no
        ev_term = 0.0
        if evidence.verdict == "evidence" and evidence.avg_return_pct is not None:
            ev_term = min(0.15, max(0.0, evidence.avg_return_pct) * 0.5)
            c.reasons.append(f"this strategy made {evidence.avg_return_pct:+.2f}% per trade after costs on unseen history")
        elif evidence.verdict == "no_edge":
            c.blockers.append("real-history testing found no edge for this strategy")
        elif evidence.trades >= 20 and evidence.avg_return_pct is not None and evidence.avg_return_pct < 0:
            c.blockers.append(f"its paper forward test is losing ({evidence.avg_return_pct:+.2f}% per trade over "
                              f"{evidence.trades} trades)")

        rr_term = min(1.0, max(0.0, (c.rr_after_cost - 1.0) / 2.0))
        c.score = (0.35 * sig.score + 0.25 * rr_term + 0.15 * fit + 0.10 * min(1.0, (agree - 1) / 2)
                   + 0.10 * dollar_volume_rank + ev_term)
        c.reasons.insert(0, f"{ALL_STRATEGIES[sig.strategy].name if sig.strategy in ALL_STRATEGIES else sig.strategy} "
                            f"setup, strength {sig.score:.0%}")
        c.reasons.append(f"aims for +{reward:.2f}% with a {risk:.2f}% stop; costs ≈ {cost:.2f}% round trip")
        if agree > 1:
            c.reasons.append(f"{agree} strategies agree")
        if c.rr_after_cost < self.MIN_RR_AFTER_COST:
            c.blockers.append(f"after costs the upside is only {c.rr_after_cost:.1f}× the downside (need "
                              f"{self.MIN_RR_AFTER_COST:.1f}×)")
        if reward > 0 and cost / reward > self.MAX_COST_SHARE:
            c.blockers.append(f"costs would eat {cost / reward:.0%} of the target")
        if self.max_spread_bps is not None and spread_bps > self.max_spread_bps:
            c.blockers.append(f"the bid/ask spread ({spread_bps:.0f} bps) is wider than the {self.max_spread_bps:.0f} bps "
                              f"limit — too expensive to get in and out")
        if self.max_stop_pct is not None and risk > self.max_stop_pct + 1e-9:
            c.blockers.append(f"to leave room for costs the stop would have to be {risk:.1f}% away, beyond the "
                              f"{self.max_stop_pct:.1f}% limit")
        if fit == 0.0:
            c.blockers.append(f"{ALL_STRATEGIES[sig.strategy].name if sig.strategy in ALL_STRATEGIES else sig.strategy} "
                              f"setups don't suit a {regime.label} market")
        threshold = self.TAKE_THRESHOLD + (0.12 if regime.label in ("risk-off", "volatile") else 0.0)
        if c.score < threshold and not c.blockers:
            c.blockers.append(f"overall score {c.score:.2f} is below the {threshold:.2f} bar for a {regime.label} market")
        return c

    def decide(self, candidates: Iterable[Candidate], regime: Regime, free_slots: int) -> Decision:
        ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
        chosen: list[Candidate] = []
        for c in ranked:
            if not c.blockers and len(chosen) < free_slots:
                c.take = True
                chosen.append(c)
            elif not c.blockers:
                c.blockers.append("a better opportunity took the last free slot")
        if not ranked:
            summary = "no setups right now — nothing worth considering."
        elif free_slots <= 0:
            summary = f"{len(ranked)} setup(s) seen, but all position slots are in use."
        elif chosen:
            names = ", ".join(c.signal.symbol for c in chosen)
            others = len(ranked) - len(chosen)
            summary = f"compared {len(ranked)} setup(s) and picked {names}" + (
                f"; passed on {others} weaker one(s)." if others else ".")
        else:
            top = ranked[0]
            summary = (f"compared {len(ranked)} setup(s) and chose not to trade: the best one ({top.signal.symbol}) "
                       f"didn't clear the bar — {top.blockers[0]}.")
        return Decision(regime, ranked, chosen, free_slots, summary)


def breadth_pct(rows: Iterable) -> Optional[float]:
    el = [r for r in rows if getattr(r, "eligible", False) and getattr(r, "day_volume", 0) > 0]
    if len(el) < 20:
        return None
    return 100.0 * sum(1 for r in el if r.change_open_pct > 0) / len(el)


def vol_ratio(spy_row, spy_baseline) -> Optional[float]:
    if spy_row is None or spy_baseline is None or not getattr(spy_baseline, "avg_range_pct", 0):
        return None
    hi, lo = getattr(spy_row, "day_high", 0) or 0, getattr(spy_row, "day_low", 0) or 0
    if hi <= 0 or lo <= 0:
        return None
    return ((hi - lo) / lo * 100) / spy_baseline.avg_range_pct if spy_baseline.avg_range_pct > 0 else None


def finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)
