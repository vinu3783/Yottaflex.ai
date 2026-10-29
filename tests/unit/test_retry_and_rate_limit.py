import random
from datetime import UTC, datetime

import pytest

from ingestion_pipeline.rate_limiter import RateLimiter
from ingestion_pipeline.retry_policy import RetryAfterTooLong, RetryPolicy, parse_retry_after


def test_backoff_is_exponential_bounded_and_jittered():
    policy = RetryPolicy(base_delay=1, max_delay=8, rng=random.Random(0))
    for attempt, ceiling in [(1, 1), (2, 2), (3, 4), (4, 8), (5, 8), (10, 8)]:
        samples = [policy.backoff(attempt) for _ in range(200)]
        assert all(0 <= s <= ceiling for s in samples)
        # full jitter: delays are spread out, not all equal to the ceiling
        assert max(samples) - min(samples) > ceiling * 0.5


def test_backoff_is_deterministic_with_seeded_rng():
    a = RetryPolicy(rng=random.Random(7))
    b = RetryPolicy(rng=random.Random(7))
    assert [a.backoff(i) for i in range(1, 6)] == [b.backoff(i) for i in range(1, 6)]


def test_retry_after_is_a_minimum():
    policy = RetryPolicy(base_delay=0.01, max_delay=0.01, rng=random.Random(0))
    assert policy.delay_for(1, retry_after=3) == 3


def test_retry_after_longer_than_allowed_fails_fast():
    with pytest.raises(RetryAfterTooLong):
        RetryPolicy(max_retry_after=10).delay_for(1, retry_after=3600)


def test_attempts_are_bounded():
    policy = RetryPolicy(max_attempts=3)
    assert [policy.should_retry(a) for a in (1, 2, 3, 4)] == [True, True, False, False]


def test_parse_retry_after():
    assert parse_retry_after("5") == 5
    assert parse_retry_after("-3") == 0
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None
    now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert parse_retry_after("Thu, 01 Jan 2026 00:00:30 GMT", now=now) == 30


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def test_rate_limiter_enforces_min_interval():
    clock = FakeClock()
    limiter = RateLimiter(4, clock=clock, sleep=clock.sleep)  # 0.25s apart
    for _ in range(5):
        limiter.acquire()
    assert clock.sleeps == pytest.approx([0.25] * 4)
    assert clock.now == pytest.approx(101.0)


def test_rate_limiter_does_not_wait_when_caller_is_slow():
    clock = FakeClock()
    limiter = RateLimiter(4, clock=clock, sleep=clock.sleep)
    limiter.acquire()
    clock.now += 10
    assert limiter.acquire() == 0


def test_rate_limiter_penalty_after_429():
    clock = FakeClock()
    limiter = RateLimiter(100, clock=clock, sleep=clock.sleep)
    limiter.acquire()
    limiter.penalize(2.0)
    assert limiter.acquire() == pytest.approx(2.0)


def test_rate_limiter_disabled():
    clock = FakeClock()
    limiter = RateLimiter(0, clock=clock, sleep=clock.sleep)
    for _ in range(10):
        limiter.acquire()
    assert clock.sleeps == []
