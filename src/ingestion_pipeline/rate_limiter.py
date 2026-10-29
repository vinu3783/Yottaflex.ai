"""Client-side throttle: enforce a minimum interval between requests.

Being polite *before* the server says 429 is cheaper than recovering after.
The clock and sleep functions are injectable so tests run instantly.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        requests_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: float | None = None

    def acquire(self) -> float:
        """Block until a request may be sent; return the seconds waited."""
        if self.min_interval == 0:
            return 0.0
        now = self._clock()
        waited = 0.0
        if self._next_allowed is not None and now < self._next_allowed:
            waited = self._next_allowed - now
            self._sleep(waited)
            now = self._next_allowed
        self._next_allowed = now + self.min_interval
        return waited

    def penalize(self, seconds: float) -> None:
        """Push the next allowed request out (used after a 429 Retry-After)."""
        if seconds > 0:
            self._next_allowed = max(self._next_allowed or 0.0, self._clock() + seconds)
