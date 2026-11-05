"""DEMONSTRATION market simulator.

Generates realistic-looking but entirely *synthetic* minute bars so the whole app
(scanner, strategies, AI, risk engine, order flow) can run before a brokerage is
connected. Everything produced here is labeled DEMO in the interface and stored in
a separate database. It contains no real prices and says nothing about whether any
strategy works in real markets.
"""
from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from ..clock import Clock, MarketCalendar, SessionDay
from ..models import Quote, Snapshot
from .base import empty_bars

EPOCH = date(2025, 1, 2)
UTC = timezone.utc
_ETF_LIKE = set("SPY QQQ IWM DIA VOO VTI IVV RSP XLK XLF XLE XLV XLY XLP XLI XLB XLU XLRE XLC SMH SOXX IGV "
                "GLD SLV TLT IEF SHY HYG LQD EEM EFA VNQ VEA VWO".split())


def _stable_int(*parts) -> int:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(h[:8], "little")


def volume_profile(m: int) -> np.ndarray:
    """U-shaped intraday volume profile, normalized to sum to 1."""
    x = np.arange(m, dtype=float)
    w = 1.0 + 2.6 * np.exp(-x / 22.0) + 1.6 * np.exp(-(m - 1 - x) / 28.0)
    return w / w.sum()


def _vol_profile(m: int) -> np.ndarray:
    x = np.arange(m, dtype=float)
    w = 1.0 + 1.4 * np.exp(-x / 25.0) + 0.5 * np.exp(-(m - 1 - x) / 30.0)
    return w / w.mean()


@dataclass
class DayData:
    session: SessionDay
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray

    @property
    def minutes(self) -> int:
        return self.o.shape[1]


class DemoMarket:
    def __init__(self, symbols: list[str], calendar: MarketCalendar, seed: int = 7):
        self.symbols = list(dict.fromkeys(symbols))
        self.index = {s: i for i, s in enumerate(self.symbols)}
        self.calendar = calendar
        self.seed = seed
        n = len(self.symbols)
        self.base_price = np.empty(n)
        self.daily_vol = np.empty(n)
        self.beta = np.empty(n)
        self.adv = np.empty(n)
        self.spread_bps = np.empty(n)
        for i, s in enumerate(self.symbols):
            r = np.random.default_rng(_stable_int("sym", s, seed))
            etf = s in _ETF_LIKE
            self.base_price[i] = math.exp(r.uniform(math.log(15), math.log(520)))
            self.daily_vol[i] = r.uniform(0.007, 0.016) if etf else r.uniform(0.013, 0.038)
            self.beta[i] = r.uniform(0.85, 1.15) if etf else r.uniform(0.6, 1.7)
            self.adv[i] = math.exp(r.uniform(math.log(3e6), math.log(6e7))) if etf else math.exp(r.uniform(math.log(6e5), math.log(2.5e7)))
            self.spread_bps[i] = r.uniform(0.6, 2.5) if etf else r.uniform(1.5, 9.0)
        self._levels: dict[date, np.ndarray] = {}
        self._days: "OrderedDict[date, DayData]" = OrderedDict()
        self._daily_stats: dict[date, np.ndarray] = {}

    # ------------------------------------------------------------ generation
    def _level(self, d: date) -> np.ndarray:
        """Closing level multiplier for trading day d (deterministic random walk)."""
        if d <= EPOCH:
            return self.base_price.copy()
        if d in self._levels:
            return self._levels[d]
        known = [k for k in self._levels if k < d]
        cur_day = max(known) if known else EPOCH
        level = self._levels.get(cur_day, self.base_price.copy())
        x = cur_day
        while x < d:
            x += timedelta(days=1)
            if not self.calendar.is_trading_day(x):
                continue
            r = np.random.default_rng(_stable_int("lvl", x.toordinal(), self.seed))
            level = level * np.exp(r.normal(0.0003, 0.6 * self.daily_vol))
            self._levels[x] = level
        self._levels[d] = level
        return level

    def day(self, d: date) -> Optional[DayData]:
        if d in self._days:
            self._days.move_to_end(d)
            return self._days[d]
        sess = self.calendar.session(d)
        if sess is None:
            return None
        dd = self._generate(sess)
        self._days[d] = dd
        while len(self._days) > 4:
            self._days.popitem(last=False)
        return dd

    def _generate(self, sess: SessionDay) -> DayData:
        n = len(self.symbols)
        m = int((sess.close - sess.open).total_seconds() // 60)
        r = np.random.default_rng(_stable_int("day", sess.day.toordinal(), self.seed))
        prev = self.calendar.previous_session(sess.open)
        open_px = self._level(prev.day) * np.exp(r.normal(0.0, 0.35 * self.daily_vol))

        prof = _vol_profile(m)
        sig = (self.daily_vol / math.sqrt(390.0))[:, None] * prof[None, :]
        mkt_drift = r.normal(0.0, 0.004) / m
        mkt = r.normal(mkt_drift, 0.0085 / math.sqrt(390.0), size=m) * prof
        idio = r.normal(0.0, 1.0, size=(n, m)) * sig * 0.9
        ret = self.beta[:, None] * mkt[None, :] + idio
        vmult = np.ones((n, m))

        # Momentum episodes: some hold, some fade. No built-in edge — just texture.
        has_ep = r.random(n) < 0.3
        for i in np.nonzero(has_ep)[0]:
            start = int(r.integers(8, max(9, m - 70)))
            dur = int(r.integers(12, 55))
            move = r.normal(0.0, 1.0) * self.daily_vol[i] * 0.9
            end = min(m, start + dur)
            ret[i, start:end] += move / max(1, end - start)
            vmult[i, start:end] *= r.uniform(1.8, 4.0)
            if r.random() < 0.5:  # partial fade afterwards
                f_end = min(m, end + dur)
                ret[i, end:f_end] -= 0.6 * move / max(1, f_end - end)

        c = open_px[:, None] * np.exp(np.cumsum(ret, axis=1))
        o = np.concatenate([open_px[:, None], c[:, :-1]], axis=1)
        wick = np.abs(r.normal(0.0, 1.0, size=(n, m))) * sig * 0.6
        h = np.maximum(o, c) * np.exp(wick)
        l = np.minimum(o, c) * np.exp(-np.abs(r.normal(0.0, 1.0, size=(n, m))) * sig * 0.6)
        v = self.adv[:, None] * volume_profile(m)[None, :] * r.lognormal(0.0, 0.35, size=(n, m)) * vmult
        return DayData(sess, o, h, l, c, np.round(v))

    # --------------------------------------------------------------- queries
    def daily_stats(self, d: date) -> Optional[np.ndarray]:
        """[n, 5] open, high, low, close, volume for a completed day."""
        if d in self._daily_stats:
            return self._daily_stats[d]
        dd = self.day(d)
        if dd is None:
            return None
        st = np.stack([dd.o[:, 0], dd.h.max(axis=1), dd.l.min(axis=1), dd.c[:, -1], dd.v.sum(axis=1)], axis=1)
        self._daily_stats[d] = st
        return st

    def minute_index(self, dd: DayData, t: datetime) -> float:
        return (t - dd.session.open).total_seconds() / 60.0

    def frame(self, symbol: str, d: date, upto: Optional[datetime] = None, start: Optional[datetime] = None) -> pd.DataFrame:
        """Completed 1-minute bars for `symbol` on day d whose minute ended at or before `upto`."""
        i = self.index.get(symbol)
        dd = self.day(d)
        if i is None or dd is None:
            return empty_bars()
        k = dd.minutes
        if upto is not None:
            k = int(max(0, min(dd.minutes, math.floor(self.minute_index(dd, upto)))))
        idx = pd.date_range(dd.session.open.astimezone(UTC), periods=dd.minutes, freq="1min")[:k]
        df = pd.DataFrame({
            "open": dd.o[i, :k], "high": dd.h[i, :k], "low": dd.l[i, :k], "close": dd.c[i, :k],
            "volume": dd.v[i, :k],
        }, index=idx)
        df["vwap"] = (df["high"] + df["low"] + df["close"]) / 3
        if start is not None:
            df = df[df.index >= start]
        return df

    def live_price(self, symbol: str, t: datetime) -> Optional[tuple[float, float, float]]:
        """(price, partial_high, partial_low) inside the current minute, or the last close if closed."""
        i = self.index.get(symbol)
        if i is None:
            return None
        d = t.astimezone(self.calendar_tz()).date()
        dd = self.day(d)
        if dd is None or t < dd.session.open:
            sess = self.calendar.previous_session(t if dd is None else dd.session.open)
            st = self.daily_stats(sess.day)
            return (float(st[i, 3]),) * 3 if st is not None else None
        mi = self.minute_index(dd, t)
        if mi >= dd.minutes:
            px = float(dd.c[i, -1])
            return px, px, px
        j = int(mi)
        frac = mi - j
        o, c = dd.o[i, j], dd.c[i, j]
        wiggle = math.sin((j * 7.3 + frac * 11.0 + i) * 1.7) * (dd.h[i, j] - dd.l[i, j]) * 0.15 * (1 - frac)
        px = float(o + (c - o) * frac + wiggle)
        px = min(max(px, dd.l[i, j]), dd.h[i, j])
        return px, float(max(o, px)), float(min(o, px))

    @staticmethod
    def calendar_tz():
        from ..clock import ET
        return ET

    def quote(self, symbol: str, t: datetime) -> Optional[Quote]:
        lp = self.live_price(symbol, t)
        if lp is None:
            return None
        px = lp[0]
        i = self.index[symbol]
        dd = self.day(t.astimezone(self.calendar_tz()).date())
        widen = 1.0
        if dd is not None:
            mi = self.minute_index(dd, t)
            if 0 <= mi < 5:
                widen = 2.5
        half = float(px * self.spread_bps[i] * widen / 2 / 10_000)
        return Quote(round(px - half, 4), round(px + half, 4), 300.0, 300.0, t)


class DemoMarketData:
    """MarketData implementation backed by DemoMarket and a (possibly accelerated) clock."""

    is_demo = True
    feed_label = "DEMO — simulated prices, not real market data"
    coverage_note = ("You are looking at a built-in market simulator. Prices, volumes and results are made up so "
                     "you can see how the app works. Connect Alpaca in settings to use real market data.")

    def __init__(self, market: DemoMarket, clock: Clock):
        self.market = market
        self.clock = clock
        self.requests_made = 0

    def _today(self) -> date:
        from ..clock import ET
        return self.clock.now().astimezone(ET).date()

    def get_snapshots(self, symbols: list[str]) -> dict[str, Snapshot]:
        from ..clock import ET
        self.requests_made += 1
        now = self.clock.now()
        d = now.astimezone(ET).date()
        dd = self.market.day(d)
        prev = self.market.calendar.previous_session(now if dd is None else dd.session.open)
        prev_st = self.market.daily_stats(prev.day)
        prev2 = self.market.calendar.previous_session(prev.open)
        prev2_st = self.market.daily_stats(prev2.day)
        out: dict[str, Snapshot] = {}
        for s in symbols:
            i = self.market.index.get(s)
            if i is None:
                continue
            q = self.market.quote(s, now)
            if q is None:
                continue
            day_open = day_high = day_low = day_vol = 0.0
            minute_close, minute_vol, minute_time = q.mid, 0.0, None
            prev_close = float(prev_st[i, 3])
            prev_vol = float(prev_st[i, 4])
            if dd is not None and now >= dd.session.open:
                k = int(min(dd.minutes, math.floor(self.market.minute_index(dd, now))))
                lp = self.market.live_price(s, now)
                if k > 0:
                    day_open = float(dd.o[i, 0])
                    day_high = float(max(dd.h[i, :k].max(), lp[1]))
                    day_low = float(min(dd.l[i, :k].min(), lp[2]))
                    day_vol = float(dd.v[i, :k].sum())
                    minute_close, minute_vol = float(dd.c[i, k - 1]), float(dd.v[i, k - 1])
                    minute_time = dd.session.open + timedelta(minutes=k - 1)
                else:
                    day_open = day_high = day_low = float(dd.o[i, 0])
            elif dd is None or now < dd.session.open:
                prev_close = float(prev_st[i, 3])
                prev_vol = float(prev2_st[i, 4]) if prev2_st is not None else prev_vol
                if dd is None:
                    pass
            out[s] = Snapshot(symbol=s, price=q.mid, timestamp=now, prev_close=prev_close, day_open=day_open,
                              day_high=day_high, day_low=day_low, day_volume=day_vol, prev_day_volume=prev_vol,
                              minute_close=minute_close, minute_volume=minute_vol, minute_time=minute_time, quote=q)
        return out

    def get_intraday_bars(self, symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
        from ..clock import ET
        self.requests_made += 1
        end = min(end, self.clock.now())
        out: dict[str, pd.DataFrame] = {}
        days = []
        d = start.astimezone(ET).date()
        while d <= end.astimezone(ET).date():
            if self.market.calendar.is_trading_day(d):
                days.append(d)
            d += timedelta(days=1)
        for s in symbols:
            frames = [self.market.frame(s, d, upto=end, start=start) for d in days]
            frames = [f for f in frames if not f.empty]
            out[s] = pd.concat(frames) if frames else empty_bars()
        return out

    def get_daily_bars(self, symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
        from ..clock import ET
        self.requests_made += 1
        now = self.clock.now()
        end = min(end, now)
        days = []
        d = start.astimezone(ET).date()
        while d <= end.astimezone(ET).date():
            sess = self.market.calendar.session(d)
            if sess is not None and sess.close <= now:
                days.append(sess)
            d += timedelta(days=1)
        out: dict[str, pd.DataFrame] = {}
        stats = [(sess, self.market.daily_stats(sess.day)) for sess in days]
        for s in symbols:
            i = self.market.index.get(s)
            if i is None or not stats:
                out[s] = empty_bars()
                continue
            idx = pd.DatetimeIndex([sess.open.astimezone(UTC).replace(hour=4, minute=0) for sess, _ in stats])
            arr = np.array([st[i] for _, st in stats])
            df = pd.DataFrame(arr, columns=["open", "high", "low", "close", "volume"], index=idx)
            df["vwap"] = (df["high"] + df["low"] + df["close"]) / 3
            out[s] = df
        return out
