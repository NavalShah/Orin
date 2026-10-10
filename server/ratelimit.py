"""Per-user token buckets, in memory (spec 13.4, 10.7). One process only; a multi-worker
deployment would move this to Redis."""
from __future__ import annotations

import math
import threading
import time

from .errors import ApiError


class RateLimiter:
    def __init__(self, limits: dict[str, tuple[int, float]]):
        self.limits = limits  # name -> (burst, seconds to refill one token)
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}  # (name, user) -> (tokens, last seen)

    def check(self, name: str, user_id: str) -> None:
        """Take one token or raise a 429 in the standard error shape."""
        if name not in self.limits:
            return
        burst, refill_s = self.limits[name]
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get((name, user_id), (float(burst), now))
            tokens = min(float(burst), tokens + (now - last) / refill_s)
            if tokens >= 1.0:
                self._buckets[(name, user_id)] = (tokens - 1.0, now)
                return
            self._buckets[(name, user_id)] = (tokens, now)
            wait_s = max(1, math.ceil((1.0 - tokens) * refill_s))
        raise ApiError(
            429, "rate_limited", "You are sending requests faster than Orin can handle.",
            hint=f"Wait about {wait_s} second{'s' if wait_s != 1 else ''} and try again.",
            retryable=True, headers={"Retry-After": str(wait_s)},
        )
