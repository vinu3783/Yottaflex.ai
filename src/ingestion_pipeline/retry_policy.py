"""Bounded exponential backoff with full jitter.

delay(attempt) = uniform(0, min(max_delay, base * 2 ** (attempt - 1)))

"Full jitter" spreads retries from many clients over the whole window, which
avoids synchronized retry storms. A server-provided ``Retry-After`` is a
*minimum*: we never retry sooner than the server asked, but we refuse to wait
longer than ``max_retry_after`` (fail clearly instead of hanging).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


class RetryAfterTooLong(Exception):
    def __init__(self, requested: float, allowed: float) -> None:
        super().__init__(f"server asked to wait {requested:.0f}s (> allowed {allowed:.0f}s)")
        self.requested = requested


@dataclass
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 0.5
    max_delay: float = 30.0
    max_retry_after: float = 60.0
    rng: random.Random = field(default_factory=random.Random)

    def should_retry(self, attempt: int) -> bool:
        """``attempt`` is the 1-based number of the attempt that just failed."""
        return attempt < self.max_attempts

    def backoff(self, attempt: int) -> float:
        ceiling = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
        return self.rng.uniform(0, ceiling)

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        delay = self.backoff(attempt)
        if retry_after is not None:
            if retry_after > self.max_retry_after:
                raise RetryAfterTooLong(retry_after, self.max_retry_after)
            delay = max(delay, retry_after)
        return delay


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Retry-After is delta-seconds or an HTTP-date. Unparsable -> None (ignored)."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        seconds = (when - (now or datetime.now(UTC))).total_seconds()
    return max(0.0, seconds)
