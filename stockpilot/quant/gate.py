"""The statistical model inside the live decision loop.

For every ranked candidate it estimates P(win), the expected result after costs and how uncertain that
estimate is, using the SAME feature code as the research (quant/features.py). By default it only
records (shadow). It may block a PAPER entry only when the model's card is paper-eligible and the owner
enabled gating. It can never add a trade the rules + risk engine didn't already allow, and it never
applies in live mode.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Mapping, Optional

import numpy as np

from .features import feature_vector, strategy_vector
from .registry import ModelCard, ModelRegistry

log = logging.getLogger(__name__)

NO_EDGE_TEXT = "No candidate demonstrates an adequate expected advantage after costs."

# latest_features() names -> feature-frame names used by feature_vector()
LATEST_TO_FRAME = {"price": "close", "vwap": "vwap_s", "momentum_5m_pct": "mom5", "momentum_15m_pct": "mom15",
                   "momentum_30m_pct": "mom30", "distance_from_vwap_pct": "dist_vwap_pct", "rsi14": "rsi",
                   "relative_volume": "rvol", "last_bar_volume_vs_avg": "vol_ratio", "volatility_daily_pct": "rv30",
                   "change_from_open_pct": "chg_open_pct", "change_from_prev_close_pct": "chg_prev_pct"}


def frame_row(latest: Mapping) -> dict:
    return {LATEST_TO_FRAME[k]: v for k, v in latest.items() if k in LATEST_TO_FRAME}


@dataclass
class Assessment:
    model_id: str
    p_win: float
    exp_net_pct: float
    exp_net_sd: float
    passes: bool
    blocks: bool                    # True only when gating is active
    text: str
    drivers: list[tuple[str, float]] = field(default_factory=list)
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return {"model": self.model_id, "pWin": round(self.p_win, 4), "expNetPct": round(self.exp_net_pct, 4),
                "expNetSd": round(self.exp_net_sd, 4) if math.isfinite(self.exp_net_sd) else None,
                "passes": self.passes, "blocks": self.blocks, "text": self.text,
                "drivers": [[k, round(v, 4)] for k, v in self.drivers], "latencyMs": round(self.latency_ms, 2)}


class QuantGate:
    KIND = "strategy_filter"

    def __init__(self, registry: Optional[ModelRegistry]):
        self.registry = registry
        self.card: Optional[ModelCard] = None
        self.model = None
        self.error = ""
        self.reload()

    def reload(self) -> None:
        self.card, self.model, self.error = None, None, ""
        if self.registry is None:
            return
        try:
            self.card = self.registry.active_card(self.KIND)
            self.model = self.registry.load(self.card.id) if self.card else None
        except Exception as exc:  # a broken model file must never stop trading safety logic
            log.exception("could not load the statistical model")
            self.error, self.card, self.model = str(exc), None, None

    @property
    def available(self) -> bool:
        return self.model is not None

    def may_gate(self, mode: str, gate_enabled: bool) -> bool:
        return (self.available and gate_enabled and mode == "paper" and self.card.stage == "paper"
                and self.card.paper_eligible)

    def assess(self, sig, spy_latest: Optional[Mapping], breadth: Optional[float], minute: float,
               session_minutes: int, gating: bool, min_exp: float = 0.0, min_p: float = 0.5) -> Optional[Assessment]:
        if not self.available:
            return None
        t0 = time.perf_counter()
        x = feature_vector(frame_row(sig.features), frame_row(spy_latest or {}), breadth, minute, session_minutes)
        x += strategy_vector(sig.strategy, sig.stop_pct, sig.target_pct, len(sig.agreeing) or 1, sig.score)
        p = self.model.predict(np.array([x], dtype=float))
        pw, e = float(p["p_win"][0]), float(p["exp_net"][0])
        sd = float(p["exp_net_sd"][0])
        ok = e > min_exp and pw > min_p
        unc = f" ± {sd:.2f}%" if math.isfinite(sd) else ""
        text = (f"model {self.card.id}: expects {e:+.2f}%{unc} after costs, P(win) {pw:.0%}"
                + ("" if ok else " — below the bar (needs > " + f"{min_exp:+.2f}% and > {min_p:.0%})"))
        return Assessment(self.card.id, pw, e, sd, ok, gating and not ok, text, self.model.explain(np.array(x)),
                          (time.perf_counter() - t0) * 1000)
