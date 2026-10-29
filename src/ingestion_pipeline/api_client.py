"""HTTP client for the source API.

Responsibilities: authentication header, client-side throttling, timeouts,
classifying every failure, bounded retries, and handing *every* HTTP attempt
(successful or not) to the raw archive before anything else happens.

It knows nothing about the database or record validation; those are injected
(``archive`` callback) or handled by the pipeline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import ValidationError

from .config import PipelineSettings
from .errors import (
    AuthenticationError,
    ClientRequestError,
    MalformedResponseError,
    RateLimitedError,
    RequestTimeoutError,
    RetriesExhaustedError,
    SourceError,
    TransientServerError,
    TransportError,
)
from .rate_limiter import RateLimiter
from .retry_policy import RetryAfterTooLong, RetryPolicy, parse_retry_after
from .schemas import PageEnvelope

log = logging.getLogger(__name__)

MAX_STORED_TEXT = 64 * 1024


@dataclass
class HttpStats:
    attempts: int = 0
    retries: int = 0
    rate_limited: int = 0
    server_errors: int = 0
    timeouts: int = 0
    transport_errors: int = 0
    malformed: int = 0


@dataclass
class AttemptRecord:
    """Everything the raw archive stores about one HTTP attempt (no secrets)."""

    entity: str
    endpoint: str
    params: dict[str, Any]
    page_number: int
    attempt: int
    outcome: str  # ok | http_error | malformed | timeout | transport_error
    retrieved_at: datetime
    elapsed_ms: int
    status_code: int | None = None
    request_id: str | None = None
    body_sha256: str | None = None
    body: Any = None
    body_text: str | None = None
    record_count: int | None = None
    error: str | None = None


@dataclass
class Page:
    envelope: PageEnvelope
    raw_response_id: int
    attempts: int
    params: dict[str, Any] = field(default_factory=dict)


ArchiveFn = Callable[[AttemptRecord], int]


def sanitize_base_url(url: str) -> str:
    """Drop any user:password@ and query string from the base URL."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))


class ApiClient:
    def __init__(
        self,
        settings: PipelineSettings,
        *,
        http: httpx.Client | None = None,
        retry_policy: RetryPolicy | None = None,
        rate_limiter: RateLimiter | None = None,
        archive: ArchiveFn | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._owns_http = http is None
        self.http = http or httpx.Client(
            base_url=settings.api_base_url,
            timeout=httpx.Timeout(settings.request_timeout_seconds),
        )
        self.retry = retry_policy or RetryPolicy(
            max_attempts=settings.max_attempts,
            base_delay=settings.backoff_base_seconds,
            max_delay=settings.backoff_max_seconds,
            max_retry_after=settings.max_retry_after_seconds,
        )
        self.limiter = rate_limiter or RateLimiter(settings.requests_per_second, sleep=sleep)
        self.archive: ArchiveFn = archive or (lambda _record: 0)
        self.sleep = sleep
        self.stats = HttpStats()
        self.endpoint_base = sanitize_base_url(settings.api_base_url)

    # ------------------------------------------------------------ lifecycle
    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        if self.settings.api_token is None:
            return {}
        token = self.settings.api_token.get_secret_value()
        return {self.settings.auth_header: f"{self.settings.auth_scheme} {token}".strip()}

    # ------------------------------------------------------------ main entry
    def fetch_page(self, entity: str, params: dict[str, Any], page_number: int) -> Page:
        attempt = 0
        while True:
            attempt += 1
            record, outcome = self._attempt(entity, params, page_number, attempt)
            raw_id = self.archive(record)
            if isinstance(outcome, PageEnvelope):
                return Page(outcome, raw_id, attempt, params)

            error = outcome
            if not error.retryable or (
                isinstance(error, MalformedResponseError)
                and not self.settings.retry_malformed_responses
            ):
                raise error
            if not self.retry.should_retry(attempt):
                raise RetriesExhaustedError(
                    f"{entity} page {page_number}: giving up after {attempt} attempts "
                    f"(last error: {error.category}: {error})",
                    last_error=error,
                )
            retry_after = error.retry_after if isinstance(error, RateLimitedError) else None
            try:
                delay = self.retry.delay_for(attempt, retry_after)
            except RetryAfterTooLong as exc:
                raise RetriesExhaustedError(
                    f"{entity} page {page_number}: {exc}", last_error=error
                ) from None
            if retry_after:
                self.limiter.penalize(retry_after)
            self.stats.retries += 1
            log.warning(
                "retrying request",
                extra={
                    "page": page_number,
                    "attempt": attempt,
                    "error_category": error.category,
                    "status_code": error.status_code,
                    "retry_in_seconds": round(delay, 3),
                },
            )
            self.sleep(delay)

    def _attempt(
        self, entity: str, params: dict[str, Any], page_number: int, attempt: int
    ) -> tuple[AttemptRecord, PageEnvelope | SourceError]:
        self.limiter.acquire()
        self.stats.attempts += 1
        path = f"/v1/{entity}"
        record = AttemptRecord(
            entity=entity,
            endpoint=f"{self.endpoint_base}{path}",
            params=dict(params),
            page_number=page_number,
            attempt=attempt,
            outcome="ok",
            retrieved_at=datetime.now(UTC),
            elapsed_ms=0,
        )
        started = time.monotonic()
        try:
            response = self.http.get(path, params=params, headers=self._headers())
        except httpx.TimeoutException as exc:
            self.stats.timeouts += 1
            record.outcome, record.error = "timeout", f"{type(exc).__name__}"
            record.elapsed_ms = int((time.monotonic() - started) * 1000)
            return record, RequestTimeoutError(
                f"no response within {self.settings.request_timeout_seconds}s"
            )
        except httpx.TransportError as exc:
            self.stats.transport_errors += 1
            record.outcome, record.error = "transport_error", f"{type(exc).__name__}: {exc}"
            record.elapsed_ms = int((time.monotonic() - started) * 1000)
            return record, TransportError(f"{type(exc).__name__}: {exc}")

        content = response.content
        record.elapsed_ms = int((time.monotonic() - started) * 1000)
        record.status_code = response.status_code
        record.request_id = response.headers.get("X-Request-ID")
        record.body_sha256 = hashlib.sha256(content).hexdigest()
        return record, self._classify(response, content, record)

    def _classify(
        self, response: httpx.Response, content: bytes, record: AttemptRecord
    ) -> PageEnvelope | SourceError:
        status = response.status_code
        if status != 200:
            record.outcome = "http_error"
            record.body_text = content[:MAX_STORED_TEXT].decode("utf-8", "replace")
            record.error = f"HTTP {status}"
            if status in (401, 403):
                return AuthenticationError(
                    f"source rejected credentials (HTTP {status})", status_code=status
                )
            if status == 429:
                self.stats.rate_limited += 1
                return RateLimitedError(
                    "rate limited (HTTP 429)",
                    retry_after=parse_retry_after(response.headers.get("Retry-After")),
                )
            if status in self.settings.retryable_status_codes:
                self.stats.server_errors += 1
                return TransientServerError(f"HTTP {status}", status_code=status)
            return ClientRequestError(f"HTTP {status}", status_code=status)

        try:
            body = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.stats.malformed += 1
            record.outcome = "malformed"
            record.body_text = content[:MAX_STORED_TEXT].decode("utf-8", "replace")
            record.error = f"invalid JSON: {exc}"
            return MalformedResponseError(f"response body is not valid JSON ({exc})")
        record.body = body
        try:
            envelope = PageEnvelope.model_validate(body)
        except ValidationError as exc:
            self.stats.malformed += 1
            record.outcome = "malformed"
            fields = sorted({".".join(map(str, e["loc"])) for e in exc.errors()})
            record.error = f"envelope violates contract: {fields}"
            return MalformedResponseError(f"response envelope violates contract: {fields}")
        record.record_count = len(envelope.data)
        return envelope
