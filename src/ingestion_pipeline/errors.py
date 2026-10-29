"""Exception hierarchy.

Each pipeline error carries a ``category`` that is stored on the run record so
operators can tell an auth problem from an outage from a data problem without
reading stack traces. ``retryable`` tells the HTTP client whether another
attempt can possibly help.
"""

from __future__ import annotations


class PipelineError(Exception):
    category = "pipeline_error"
    retryable = False


class ConfigurationError(PipelineError):
    category = "configuration"


class ConcurrentRunError(PipelineError):
    category = "concurrent_run"


# ---------------------------------------------------------------- source / HTTP
class SourceError(PipelineError):
    category = "source_error"

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(SourceError):
    """401/403: retrying with the same credentials cannot succeed."""

    category = "authentication"


class ClientRequestError(SourceError):
    """Other 4xx: our request is wrong (bad parameter, unknown endpoint)."""

    category = "client_request"


class RateLimitedError(SourceError):
    category = "rate_limited"
    retryable = True

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class TransientServerError(SourceError):
    category = "source_unavailable"
    retryable = True


class RequestTimeoutError(SourceError):
    category = "timeout"
    retryable = True


class TransportError(SourceError):
    """Connection refused / reset / DNS - the request may not have reached the API."""

    category = "transport"
    retryable = True


class MalformedResponseError(SourceError):
    """Body is not JSON or does not match the page envelope contract."""

    category = "malformed_response"
    retryable = True


class RetriesExhaustedError(SourceError):
    category = "retries_exhausted"

    def __init__(self, message: str, *, last_error: SourceError) -> None:
        super().__init__(message, status_code=last_error.status_code)
        self.last_error = last_error


class NonAdvancingCursorError(SourceError):
    category = "non_advancing_cursor"


# ---------------------------------------------------------------- data
class BreakingSchemaChangeError(PipelineError):
    """A required field disappeared from (nearly) every record on a page."""

    category = "breaking_schema_change"


class SimulatedCrash(BaseException):
    """Raised by test hooks to emulate a process dying mid-run (``kill -9``).

    It derives from BaseException on purpose: the pipeline's ``except Exception``
    handlers do not catch it, so - like a real crash - the run row stays
    'running' and nothing past the current transaction is recorded."""
