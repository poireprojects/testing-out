"""Replay historical candidates through the app's AI decision modes: rules + ranker vs "ai confirms" vs "ai picks".

The AI is asked through the app's own AIEngine (same prompts, JSON schema, validation and approval rule)
about exactly what the live app would show it at that minute — never anything later. So that a language
model can't recognise the stock or the day and "remember" what happened next, symbols become neutral
names, the date becomes a fixed fake date and prices are rescaled so the signal price is 100. Percentages,
volumes ratios, time of day and the market context are kept.

Results are after the same costs as every other backtest number. AI calls to a local model cost $0; the
latency is recorded. This measures one model on one period — it is evidence about that model, not about
"AI" in general.
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

import numpy as np

from ..analysis.features import feature_frame, latest_features
from ..models import Signal
from ..strategies import ALL_STRATEGIES, Context
from .engine import BTConfig, BTTrade, DayInput, FrameView, replay_portfolio, risk_adjusted, summarize

log = logging.getLogger(__name__)

FAKE_DAY = date(2001, 1, 2)
_PRICE_KEYS = ("price", "vwap", "ema9", "ema20", "ema50", "atr14", "opening_range_high", "opening_range_low",
               "prior_30m_high")


@dataclass
class AIStats:
    calls: int = 0
    invalid: int = 0
    seconds: float = 0.0
    approved: int = 0
    skipped: int = 0

    def to_dict(self) -> dict:
        return {"calls": self.calls, "invalid": self.invalid, "approved": self.approved, "skipped": self.skipped,
                "avgSeconds": self.seconds / self.calls if self.calls else 0.0}


class PromptBuilder:
    """Rebuilds the live app's view of a candidate at its signal bar, anonymised."""

    def __init__(self, days: list[DayInput], cfg: BTConfig):
        self.days = {d.day: d for d in days}
        self.cfg = cfg
        self._frames: dict[tuple[date, str], tuple] = {}
        self._alias: dict[str, str] = {}

    def _frame(self, day: DayInput, sym: str):
        key = (day.day, sym)
        if key not in self._frames:
            f = feature_frame(day.bars[sym], day.session_open.to_pydatetime(), day.baselines.get(sym),
                              day.session_minutes)
            self._frames[key] = (f, FrameView(f))
        return self._frames[key]

    def alias(self, sym: str) -> str:
        if sym not in self._alias:
            self._alias[sym] = f"STOCK_{len(self._alias) + 1:03d}"
        return self._alias[sym]

    def build(self, t: BTTrade) -> tuple[Signal, dict]:
        day = self.days[t.day]
        f, fv = self._frame(day, t.symbol)
        i = max(0, t.entry_i - 1)                     # the bar the signal fired on
        minute = int(fv["minute"].a[i]) - 1
        trend = None
        if day.spy_trend is not None and 0 <= minute < len(day.spy_trend):
            trend = bool(day.spy_trend[minute])
        strat = ALL_STRATEGIES[t.strategy]
        idea = strat.entry(fv, i, Context(t.symbol, trend, day.session_minutes))
        ref = float(fv["close"].a[i]) or 1.0
        k = 100.0 / ref
        feats = latest_features(f.iloc[: i + 1])
        for key in _PRICE_KEYS:
            if feats.get(key) is not None:
                feats[key] = round(feats[key] * k, 4)
        bars = []
        for j in range(max(0, i - 14), i + 1):
            ts = f.index[j]
            bars.append({"t": (datetime(2001, 1, 2, 9, 30) + timedelta(minutes=int(fv["minute"].a[j]) - 1)).strftime("%H:%M"),
                         "o": round(fv["open"].a[j] * k, 4), "h": round(fv["high"].a[j] * k, 4),
                         "l": round(fv["low"].a[j] * k, 4), "c": round(fv["close"].a[j] * k, 4),
                         "v": int(fv["volume"].a[j])})
            del ts
        spy_chg = None
        spy = day.bars.get("SPY")
        if spy is not None and not spy.empty:
            upto = spy[spy.index <= f.index[i]]
            if not upto.empty:
                spy_chg = round((float(upto["close"].iat[-1]) / float(spy["open"].iat[0]) - 1) * 100, 2)
        when = datetime(FAKE_DAY.year, FAKE_DAY.month, FAKE_DAY.day, 14, 30, tzinfo=timezone.utc) + timedelta(minutes=minute)
        sig = Signal(self.alias(t.symbol), t.strategy, when, 100.0, 100.0 * (1 - t.stop_pct / 100),
                     100.0 * (1 + t.target_pct / 100), float(t.score),
                     [_rescale_text(r, k) for r in idea.reasons] if idea else [],
                     int(idea.max_hold_minutes) if idea else 60, feats, [t.strategy] * max(1, t.agreeing))
        cost = self.cfg.costs.round_trip_cost_pct(self.cfg.notional, ref, self.cfg.spread_bps)
        ctx = {"signal_id": None, "spread_bps": self.cfg.spread_bps, "recent_bars": bars,
               "round_trip_cost_pct": round(cost, 3),
               "market": {"spy_change_pct": spy_chg, "spy_above_vwap": trend,
                          "minutes_to_close": day.session_minutes - minute},
               "account": {"open_positions": 0, "today_pnl_usd": 0.0},
               "data_note": "IEX feed only (a few % of all U.S. volume); prices rescaled so the signal price is 100"}
        return sig, ctx


_DOLLARS = re.compile(r"\$(\d+(?:\.\d+)?)")


def _rescale_text(text: str, k: float) -> str:
    """Dollar amounts inside a strategy's explanation get the same rescaling as every other price."""
    return _DOLLARS.sub(lambda m: f"${float(m.group(1)) * k:.2f}", text)


def _key(t: BTTrade) -> tuple:
    return (t.day, t.symbol, t.strategy, t.entry_i)


class AIModes:
    """Ask the AI (synchronously) and remember every answer, so each candidate is asked at most once."""

    def __init__(self, ai_engine, prompts: PromptBuilder):
        self.ai = ai_engine
        self.prompts = prompts
        self.confirm_cache: dict[tuple, bool] = {}
        self.stats_confirm = AIStats()
        self.stats_select = AIStats()
        self.log: list[dict] = []

    def confirm(self, t: BTTrade) -> bool:
        k = _key(t)
        if k in self.confirm_cache:
            return self.confirm_cache[k]
        sig, ctx = self.prompts.build(t)
        t0 = time.monotonic()
        res = self.ai.evaluate_entry(sig, ctx)
        self.stats_confirm.seconds += time.monotonic() - t0
        self.stats_confirm.calls += 1
        if not res.ok:
            self.stats_confirm.invalid += 1
        ok = bool(res.approved)
        if ok:
            self.stats_confirm.approved += 1
        else:
            self.stats_confirm.skipped += 1
        self.confirm_cache[k] = ok
        self.log.append({"mode": "confirm", "trade": [str(t.day), t.symbol, t.strategy, t.entry_i],
                         "approved": ok, "valid": res.ok, "retPct": t.ret_pct,
                         "confidence": res.decision.confidence if res.decision else None,
                         "reasoning": (res.decision.reasoning if res.decision else res.error or "")[:300]})
        return ok

    def choose(self, day: date, minute: int, shortlist: list, free: int) -> list[BTTrade]:
        options, by_alias = [], {}
        for c, t in shortlist:
            sig, ctx = self.prompts.build(t)
            o = c.to_dict()
            o.update({"symbol": sig.symbol, "indicators": sig.features, "why_it_triggered": sig.reasons})
            options.append(o)
            by_alias[sig.symbol] = t
        ctx_all = {"market": ctx["market"], "time_et": sig.time.strftime("%H:%M"),
                   "minutes_to_close": ctx["market"]["minutes_to_close"], "data_note": ctx["data_note"]}
        t0 = time.monotonic()
        res = self.ai.evaluate_selection(options, ctx_all)
        self.stats_select.seconds += time.monotonic() - t0
        self.stats_select.calls += 1
        if not res.ok:
            self.stats_select.invalid += 1
            return []
        choice = res.selection.choice
        picked = by_alias.get(choice)
        if picked is not None:
            self.stats_select.approved += 1
        else:
            self.stats_select.skipped += 1
        self.log.append({"mode": "select", "day": str(day), "minute": minute, "options": list(by_alias),
                         "choice": choice, "retPcts": {a: round(t.ret_pct, 4) for a, t in by_alias.items()},
                         "reasoning": res.selection.reasoning[:300]})
        return [picked] if picked is not None else []


def compare_modes(days: list[DayInput], trades: list[BTTrade], cfg: BTConfig, ai_engine,
                  sample_for_filter: int = 120, seed: int = 5,
                  progress: Optional[Callable[[str], None]] = None) -> dict:
    """rules + ranker vs ai confirms vs ai picks on the same candidates, same slots, same costs."""
    day_list = sorted({d.day for d in days})
    spy_by_day = {d.day: d.spy_trend for d in days}
    prompts = PromptBuilder(days, cfg)
    modes = AIModes(ai_engine, prompts)
    real = [t for t in trades if not t.random_baseline]

    def report(ts):
        s = summarize(ts)
        return {k: s[k] for k in ("trades", "winRate", "avgRetPct", "totalPnl", "profitFactor", "maxDrawdown")} | \
            {"riskAdjusted": risk_adjusted(ts, day_list)}

    ranked = replay_portfolio(real, cfg, True, spy_by_day)
    if progress:
        progress(f"rules + ranker: {len(ranked)} trades; asking the AI about each pick")
    confirm = replay_portfolio(real, cfg, True, spy_by_day, gate=modes.confirm)
    if progress:
        progress(f"ai confirms: {len(confirm)} trades ({modes.stats_confirm.calls} AI calls); now ai picks")
    select = replay_portfolio(real, cfg, True, spy_by_day, chooser=modes.choose)
    if progress:
        progress(f"ai picks: {len(select)} trades ({modes.stats_select.calls} AI calls); now the filter sample")

    # a larger, capital-free check: does the AI's yes/no separate better setups from worse ones?
    rng = random.Random(seed)
    pool = [t for t in real if _key(t) not in modes.confirm_cache]
    sample = rng.sample(pool, min(sample_for_filter, len(pool)))
    for t in sample:
        modes.confirm(t)
    asked = [t for t in real if _key(t) in modes.confirm_cache]
    yes = [t for t in asked if modes.confirm_cache[_key(t)]]
    no = [t for t in asked if not modes.confirm_cache[_key(t)]]

    def avg(ts):
        return float(np.mean([t.ret_pct for t in ts])) if ts else 0.0

    every = summarize(real)
    out = {
        "days": len(day_list), "candidates": len(real),
        "modes": {"rules_ranker": report(ranked), "ai_confirm": report(confirm), "ai_select": report(select)},
        "filter": {"asked": len(asked), "approved": len(yes), "skipped": len(no),
                   "approvedAvgRetPct": avg(yes), "skippedAvgRetPct": avg(no), "allAvgRetPct": avg(asked),
                   "everySignalAvgRetPct": every["avgRetPct"]},
        "ai": {"confirm": modes.stats_confirm.to_dict(), "select": modes.stats_select.to_dict(),
               "provider": getattr(ai_engine.provider, "name", ""), "model": getattr(ai_engine.provider, "model", ""),
               "costUsd": 0.0},
        "log": modes.log,
    }
    return out
