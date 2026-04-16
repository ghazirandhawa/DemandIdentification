from __future__ import annotations

import os
import threading
import time
from collections import deque


class SlidingWindowRateLimiter:
    """
    Thread-safe limiter: at most ``max_calls`` acquisitions per ``period_seconds``
    sliding window (SEC allows up to 10 HTTP requests per second to sec.gov).
    """

    __slots__ = ("_lock", "_max_calls", "_period", "_times")

    def __init__(self, max_calls: int, period_seconds: float = 1.0) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be at least 1")
        if period_seconds <= 0:
            raise ValueError("period_seconds must be positive")
        self._max_calls = max_calls
        self._period = period_seconds
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            sleep_for: float = 0.0
            with self._lock:
                now = time.monotonic()
                while self._times and now - self._times[0] >= self._period:
                    self._times.popleft()
                if len(self._times) < self._max_calls:
                    self._times.append(time.monotonic())
                    return
                wait = self._period - (now - self._times[0])
                sleep_for = max(wait, 0.0)
            if sleep_for > 0:
                time.sleep(sleep_for)


def default_max_rps_from_env() -> int:
    raw = os.environ.get("SEC_MAX_RPS", "9").strip()
    try:
        n = int(float(raw))
    except ValueError:
        n = 9
    return max(1, min(10, n))


def build_default_limiter() -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(max_calls=default_max_rps_from_env(), period_seconds=1.0)
