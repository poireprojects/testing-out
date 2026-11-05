"""Thread-safe token bucket so we stay under the data API's per-minute request limit."""
from __future__ import annotations

import threading
import time


class RateLimitTimeout(Exception):
    pass


class TokenBucket:
    def __init__(self, per_minute: int, burst: int | None = None, clock=time.monotonic, sleep=time.sleep):
        self.rate = per_minute / 60.0
        self.capacity = float(burst if burst is not None else max(1, per_minute // 6))
        self.tokens = self.capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()
        self.total_acquired = 0

    def _refill(self) -> None:
        now = self._clock()
        self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
        self._last = now

    def try_acquire(self, n: int = 1) -> bool:
        with self._lock:
            self._refill()
            if self.tokens >= n:
                self.tokens -= n
                self.total_acquired += n
                return True
            return False

    def acquire(self, n: int = 1, timeout: float = 30.0) -> None:
        deadline = self._clock() + timeout
        while True:
            with self._lock:
                self._refill()
                if self.tokens >= n:
                    self.tokens -= n
                    self.total_acquired += n
                    return
                wait = (n - self.tokens) / self.rate
            if self._clock() + wait > deadline:
                raise RateLimitTimeout(f"rate limit: would need to wait {wait:.1f}s")
            self._sleep(min(wait, 1.0))
