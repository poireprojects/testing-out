"""Market calendar, trading-session phases and time sources.

The broker's calendar (Alpaca `/v2/calendar`) is authoritative when connected.
The rule-based NYSE calendar here is a fallback for demo mode and offline startup;
it knows the standard holidays and early closes but not one-off closures.
"""
from __future__ import annotations

import threading
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Optional, Protocol
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


# ------------------------------------------------------------------ time sources

class Clock(Protocol):
    def now(self) -> datetime: ...


class RealClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """Test clock: time only moves when told to."""

    def __init__(self, start: datetime):
        self._t = start.astimezone(UTC)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._t

    def set(self, t: datetime) -> None:
        with self._lock:
            self._t = t.astimezone(UTC)

    def advance(self, seconds: float) -> datetime:
        with self._lock:
            self._t += timedelta(seconds=seconds)
            return self._t


class AcceleratedClock:
    """Demo clock: simulated time = start + (wall elapsed × speed)."""

    def __init__(self, sim_start: datetime, speed: float = 1.0):
        self._sim_start = sim_start.astimezone(UTC)
        self._wall_start = _time.monotonic()
        self.speed = speed
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            elapsed = (_time.monotonic() - self._wall_start) * self.speed
            return self._sim_start + timedelta(seconds=elapsed)

    def jump_to(self, sim_time: datetime) -> None:
        with self._lock:
            self._sim_start = sim_time.astimezone(UTC)
            self._wall_start = _time.monotonic()

    def set_speed(self, speed: float) -> None:
        now = self.now()
        with self._lock:
            self.speed = speed
        self.jump_to(now)


# ------------------------------------------------------------------- calendar

@dataclass(frozen=True)
class SessionDay:
    day: date
    open: datetime  # tz-aware (ET)
    close: datetime

    @property
    def early_close(self) -> bool:
        return self.close.astimezone(ET).time() < time(16, 0)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(d: date) -> Optional[date]:
    if d.weekday() == 5:  # Saturday -> Friday (except New Year, handled by caller)
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def nyse_holidays(year: int) -> set[date]:
    hol: set[date] = set()
    ny = date(year, 1, 1)
    if ny.weekday() == 6:
        hol.add(ny + timedelta(days=1))
    elif ny.weekday() != 5:  # NYSE does not observe Saturday New Year on Friday
        hol.add(ny)
    hol.add(_nth_weekday(year, 1, 0, 3))   # MLK
    hol.add(_nth_weekday(year, 2, 0, 3))   # Presidents
    hol.add(_easter(year) - timedelta(days=2))  # Good Friday
    hol.add(_last_weekday(year, 5, 0))     # Memorial
    if year >= 2022:
        hol.add(_observed(date(year, 6, 19)))  # Juneteenth
    hol.add(_observed(date(year, 7, 4)))
    hol.add(_nth_weekday(year, 9, 0, 1))   # Labor
    hol.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
    hol.add(_observed(date(year, 12, 25)))
    return hol


def nyse_early_closes(year: int) -> set[date]:
    out: set[date] = set()
    j3 = date(year, 7, 3)
    if j3.weekday() < 5 and j3 not in nyse_holidays(year):
        out.add(j3)
    out.add(_nth_weekday(year, 11, 3, 4) + timedelta(days=1))
    c24 = date(year, 12, 24)
    if c24.weekday() < 5 and c24 not in nyse_holidays(year):
        out.add(c24)
    return out


def rule_based_session(d: date) -> Optional[SessionDay]:
    if d.weekday() >= 5 or d in nyse_holidays(d.year):
        return None
    close_t = time(13, 0) if d in nyse_early_closes(d.year) else time(16, 0)
    return SessionDay(d, datetime.combine(d, time(9, 30), ET), datetime.combine(d, close_t, ET))


class MarketCalendar:
    def __init__(self, sessions: Optional[Iterable[SessionDay]] = None):
        self._sessions: dict[date, Optional[SessionDay]] = {}
        self._authoritative: set[date] = set()
        if sessions:
            self.load(sessions)

    def load(self, sessions: Iterable[SessionDay], start: Optional[date] = None, end: Optional[date] = None) -> None:
        """Load authoritative sessions. Days in [start, end] not listed are closed."""
        listed = {s.day: s for s in sessions}
        if listed:
            start = start or min(listed)
            end = end or max(listed)
            d = start
            while d <= end:
                self._sessions[d] = listed.get(d)
                self._authoritative.add(d)
                d += timedelta(days=1)

    @property
    def has_broker_data(self) -> bool:
        return bool(self._authoritative)

    def session(self, d: date) -> Optional[SessionDay]:
        if d in self._sessions:
            return self._sessions[d]
        s = rule_based_session(d)
        self._sessions[d] = s
        return s

    def is_trading_day(self, d: date) -> bool:
        return self.session(d) is not None

    def next_session(self, after: datetime) -> SessionDay:
        d = after.astimezone(ET).date()
        for _ in range(15):
            s = self.session(d)
            if s and s.close > after:
                return s
            d += timedelta(days=1)
        raise RuntimeError("no trading session found in the next two weeks")

    def previous_session(self, before: datetime) -> SessionDay:
        d = before.astimezone(ET).date() - timedelta(days=1)
        for _ in range(15):
            s = self.session(d)
            if s:
                return s
            d -= timedelta(days=1)
        raise RuntimeError("no previous trading session found")

    def add_trading_days(self, d: date, n: int) -> date:
        while n > 0:
            d += timedelta(days=1)
            if self.is_trading_day(d):
                n -= 1
        return d

    def trading_days_between(self, start: date, end: date) -> list[date]:
        out, d = [], start
        while d <= end:
            if self.is_trading_day(d):
                out.append(d)
            d += timedelta(days=1)
        return out


# ------------------------------------------------------------------- phases

@dataclass
class SessionState:
    now: datetime
    phase: str  # closed | pre_open | opening | trading | wind_down | flatten | after_close
    session: Optional[SessionDay]
    next_open: datetime
    minutes_to_open: float
    minutes_to_close: float

    @property
    def is_open(self) -> bool:
        return self.phase in ("opening", "trading", "wind_down", "flatten")

    @property
    def can_enter(self) -> bool:
        return self.phase == "trading"

    def describe(self) -> str:
        def fmt(mins: float) -> str:
            mins = max(0, int(mins))
            h, m = divmod(mins, 60)
            if h >= 24:
                d, h = divmod(h, 24)
                return f"{d}d {h}h"
            return f"{h}h {m:02d}m" if h else f"{m}m"
        if self.phase in ("closed", "after_close", "pre_open"):
            return f"market opens in {fmt(self.minutes_to_open)}"
        if self.phase == "opening":
            return "market just opened · watching the first few minutes"
        if self.phase == "trading":
            return f"market open · {fmt(self.minutes_to_close)} left"
        if self.phase == "wind_down":
            return f"closing soon · no new trades · {fmt(self.minutes_to_close)} left"
        return "closing all positions before the bell"


def session_state(calendar: MarketCalendar, now: datetime, *, no_entries_first_minutes: int = 5,
                  last_entry_minutes_before_close: int = 30, flatten_minutes_before_close: int = 10) -> SessionState:
    now = now.astimezone(UTC)
    today = calendar.session(now.astimezone(ET).date())
    nxt = calendar.next_session(now)
    next_open = nxt.open if nxt.open > now else calendar.next_session(nxt.close + timedelta(minutes=1)).open
    mto = (next_open - now).total_seconds() / 60
    if today is None:
        return SessionState(now, "closed", None, next_open, mto, 0.0)
    to_close = (today.close - now).total_seconds() / 60
    since_open = (now - today.open).total_seconds() / 60
    if since_open < 0:
        phase = "pre_open" if since_open > -90 else "closed"
        return SessionState(now, phase, today, today.open, -since_open, (today.close - today.open).total_seconds() / 60)
    if to_close <= 0:
        return SessionState(now, "after_close", today, next_open, mto, 0.0)
    if to_close <= flatten_minutes_before_close:
        phase = "flatten"
    elif to_close <= last_entry_minutes_before_close:
        phase = "wind_down"
    elif since_open < no_entries_first_minutes:
        phase = "opening"
    else:
        phase = "trading"
    return SessionState(now, phase, today, next_open, mto, to_close)
