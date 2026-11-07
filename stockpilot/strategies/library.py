"""The five rules-based intraday strategies.

Each one is deliberately simple and explainable. None is assumed to work — the
backtester and paper trading decide whether any of them shows evidence of an edge.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .base import Context, EntryIdea, Strategy, clip01, risk_reward_target


class IntradayMomentum(Strategy):
    id = "momentum"
    name = "intraday momentum"
    summary = "Buys stocks that are already rising strongly today on heavy volume."
    explain = ("Looks for a stock up meaningfully over the last 30 minutes, trading above its average price for "
               "the day (VWAP), with unusually high volume. The idea: strong moves on heavy volume often continue "
               "for a little while. Exits when the move fades.")

    def entry(self, f: pd.DataFrame, i: int, ctx: Context) -> Optional[EntryIdea]:
        v = lambda c: self.val(f, c, i)
        minute, c, vwap, e20, e9 = v("minute"), v("close"), v("vwap_s"), v("ema20"), v("ema9")
        m30, m5, rv, rsi, vr, atr = v("mom30"), v("mom5"), v("rvol"), v("rsi"), v("vol_ratio"), v("atr")
        if not self.ok(minute, c, vwap, e20, m30, m5, rv, rsi, vr, atr) or minute < 20:
            return None
        if not (m30 >= 0.8 and m5 > 0 and c > vwap and c > e20 and e9 > e20 and rv >= 1.5
                and 55 <= rsi <= 78 and vr >= 1.2):
            return None
        stop = c - max(1.5 * atr, c * 0.004)
        score = clip01(0.40 + 0.15 * min(m30 / 2.0, 1.0) + 0.15 * min((rv - 1.5) / 2.0, 1.0)
                       + 0.10 * (vr >= 2.0) + 0.10 * bool(ctx.market_trend_up))
        return EntryIdea(score, stop, risk_reward_target(c, stop, 2.0), [
            f"up {m30:.1f}% in the last 30 minutes",
            f"volume is {rv:.1f}× a normal day so far",
            f"trading {((c / vwap) - 1) * 100:.2f}% above VWAP",
            f"RSI {rsi:.0f} (strong but not extreme)",
        ], max_hold_minutes=60)

    def exit(self, f, i, entry_price, entry_index) -> Optional[str]:
        if i < 1:
            return None
        if self.val(f, "close", i) < self.val(f, "ema9", i) and self.val(f, "close", i - 1) < self.val(f, "ema9", i - 1):
            return "momentum faded (two closes below the 9-minute average)"
        if self.val(f, "rsi", i) > 85:
            return "overextended (RSI above 85) — taking profit"
        return None


class TrendFollowing(Strategy):
    id = "trend"
    name = "trend following"
    summary = "Buys a dip inside an established uptrend."
    explain = ("Waits for a steady uptrend (short-term average above the longer one, both rising), then buys when "
               "the price dips back to the 20-minute average and bounces. Exits if the trend breaks.")

    def entry(self, f, i, ctx) -> Optional[EntryIdea]:
        if i < 12:
            return None
        v = lambda c, k=i: self.val(f, c, k)
        minute, c, o, e20, e50, vwap, rsi, atr = (v("minute"), v("close"), v("open"), v("ema20"), v("ema50"),
                                                  v("vwap_s"), v("rsi"), v("atr"))
        e50_prev, low_prev, e20_prev = v("ema50", i - 10), v("low", i - 1), v("ema20", i - 1)
        if not self.ok(minute, c, o, e20, e50, vwap, rsi, atr, e50_prev, low_prev, e20_prev) or minute < 30:
            return None
        slope = (e50 / e50_prev - 1) * 100
        m30, rv = v("mom30"), v("rvol")
        if not self.ok(m30, rv):
            return None
        # A real trend: rising averages, price recently stretched above the 20-min average, then a pullback.
        stretch = float((f["high"].iloc[max(0, i - 12):i - 1] / f["ema20"].iloc[max(0, i - 12):i - 1]).max() - 1) * 100
        if not (e20 > e50 > e50_prev and slope >= 0.08 and m30 >= 0.3 and rv >= 1.0 and stretch >= 0.3
                and c > vwap and low_prev <= e20_prev * 1.001 and c > e20 and c > o and 45 <= rsi <= 68):
            return None
        recent_low = float(f["low"].iloc[max(0, i - 4):i + 1].min())
        stop = min(recent_low, e50) - 0.1 * atr
        score = clip01(0.42 + 0.2 * min(slope / 0.3, 1.0) + 0.15 * bool(ctx.market_trend_up)
                       + 0.1 * min(max(rv, 0) / 2.0, 1.0))
        return EntryIdea(score, stop, risk_reward_target(c, stop, 2.0), [
            "uptrend: 20-minute average above the 50-minute average and rising",
            "price dipped to the 20-minute average and bounced",
            f"above VWAP, RSI {rsi:.0f}",
        ], max_hold_minutes=120)

    def exit(self, f, i, entry_price, entry_index) -> Optional[str]:
        if self.val(f, "close", i) < self.val(f, "ema50", i):
            return "trend broke (price fell below the 50-minute average)"
        return None


class OpeningRangeBreakout(Strategy):
    id = "orb"
    name = "opening-range breakout"
    summary = "Buys when price breaks above the high of the first 15 minutes."
    explain = ("The first 15 minutes set a range. If price later breaks above that range's high with strong "
               "volume, buyers may be taking control for the day. Exits if price falls back into the range.")

    def entry(self, f, i, ctx) -> Optional[EntryIdea]:
        if i < 1:
            return None
        v = lambda c, k=i: self.val(f, c, k)
        minute, c, prev_c, orh, orl, vr, vwap = (v("minute"), v("close"), v("close", i - 1), v("or_high"),
                                                 v("or_low"), v("vol_ratio"), v("vwap_s"))
        if not self.ok(minute, c, prev_c, orh, orl, vr, vwap) or not (16 <= minute <= 120):
            return None
        rng_pct = (orh - orl) / orl * 100 if orl > 0 else 0
        if not (c > orh >= prev_c and vr >= 1.5 and c > vwap and 0.3 <= rng_pct <= 4.0):
            return None
        stop = (orh + orl) / 2
        score = clip01(0.45 + 0.2 * min((vr - 1.5) / 2.5, 1.0) + 0.15 * bool(ctx.market_trend_up)
                       + 0.1 * min(max(v("rvol"), 0) / 2.5, 1.0))
        return EntryIdea(score, stop, risk_reward_target(c, stop, 2.0), [
            f"broke above the opening-range high ${orh:.2f}",
            f"breakout bar volume {vr:.1f}× recent average",
            f"opening range was {rng_pct:.1f}% wide",
        ], max_hold_minutes=90)

    def exit(self, f, i, entry_price, entry_index) -> Optional[str]:
        orh, orl, c = self.val(f, "or_high", i), self.val(f, "or_low", i), self.val(f, "close", i)
        if self.ok(orh, orl, c) and c < orh - 0.25 * (orh - orl):
            return "breakout failed (back inside the opening range)"
        return None


class VWAPReclaim(Strategy):
    id = "vwap"
    name = "VWAP reclaim"
    summary = "Buys when price climbs back above the day's average price."
    explain = ("VWAP is the average price everyone paid today, weighted by volume. When a stock that spent time "
               "below VWAP climbs back above it on rising volume, buyers are regaining control. Exits if it slips "
               "back below.")

    def entry(self, f, i, ctx) -> Optional[EntryIdea]:
        if i < 10:
            return None
        v = lambda c, k=i: self.val(f, c, k)
        minute, c, vwap, prev_c, prev_vwap, vr, rsi, atr, e9, e9p = (
            v("minute"), v("close"), v("vwap_s"), v("close", i - 1), v("vwap_s", i - 1), v("vol_ratio"), v("rsi"),
            v("atr"), v("ema9"), v("ema9", i - 1))
        if not self.ok(minute, c, vwap, prev_c, prev_vwap, vr, rsi, atr, e9, e9p) or minute < 20:
            return None
        below = int((f["close"].iloc[i - 10:i] < f["vwap_s"].iloc[i - 10:i]).sum())
        if not (c > vwap and prev_c <= prev_vwap and below >= 5 and vr >= 1.3 and rsi > 50 and e9 > e9p):
            return None
        recent_low = float(f["low"].iloc[i - 5:i + 1].min())
        stop = min(vwap - 0.5 * atr, recent_low)
        score = clip01(0.42 + 0.18 * min((vr - 1.3) / 2.0, 1.0) + 0.15 * bool(ctx.market_trend_up)
                       + 0.1 * min(below / 10, 1.0))
        return EntryIdea(score, stop, risk_reward_target(c, stop, 2.0), [
            f"reclaimed VWAP ${vwap:.2f} after {below} of the last 10 minutes below it",
            f"volume {vr:.1f}× recent average",
            f"RSI {rsi:.0f}",
        ], max_hold_minutes=90)

    def exit(self, f, i, entry_price, entry_index) -> Optional[str]:
        if i < 1:
            return None
        atr = self.val(f, "atr", i)
        lost = [self.val(f, "close", k) < self.val(f, "vwap_s", k) - 0.25 * atr for k in (i - 1, i)]
        if all(lost):
            return "lost VWAP again"
        return None


class VolumeBreakout(Strategy):
    id = "volume_breakout"
    name = "volume-confirmed breakout"
    summary = "Buys a new 30-minute high made on a burst of volume."
    explain = ("When price pushes through its highest level of the last 30 minutes on a big jump in volume, "
               "it signals real demand rather than drift. Exits if the breakout stalls.")

    def entry(self, f, i, ctx) -> Optional[EntryIdea]:
        v = lambda c, k=i: self.val(f, c, k)
        minute, c, o, hh, vr, vwap, rv, rsi, atr = (v("minute"), v("close"), v("open"), v("hh30"), v("vol_ratio"),
                                                    v("vwap_s"), v("rvol"), v("rsi"), v("atr"))
        if not self.ok(minute, c, o, hh, vr, vwap, rv, rsi, atr) or minute < 30:
            return None
        if not (c > hh and vr >= 2.5 and c > vwap and rv >= 1.2 and rsi < 80 and c > o):
            return None
        stop = hh - 0.5 * atr
        score = clip01(0.45 + 0.2 * min((vr - 2.5) / 3.0, 1.0) + 0.15 * min((rv - 1.2) / 2.0, 1.0)
                       + 0.1 * bool(ctx.market_trend_up))
        return EntryIdea(score, stop, risk_reward_target(c, stop, 2.0), [
            f"new 30-minute high above ${hh:.2f}",
            f"breakout volume {vr:.1f}× recent average",
            f"relative volume {rv:.1f}×",
        ], max_hold_minutes=60)

    def exit(self, f, i, entry_price, entry_index) -> Optional[str]:
        if i < 2 or entry_index is None or i - entry_index < 3:
            return None
        c, e9, vr = self.val(f, "close", i), self.val(f, "ema9", i), self.val(f, "vol_ratio", i)
        if self.ok(c, e9, vr) and c < e9 and vr < 0.8:
            return "breakout stalled (volume dried up, price below the 9-minute average)"
        return None
