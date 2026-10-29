"""HTTP client behaviour against scripted responses (httpx.MockTransport)."""

from __future__ import annotations

import json
import random
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from ingestion_pipeline.api_client import ApiClient, AttemptRecord, sanitize_base_url
from ingestion_pipeline.config import PipelineSettings
from ingestion_pipeline.errors import (
    AuthenticationError,
    ClientRequestError,
    MalformedResponseError,
    RetriesExhaustedError,
)
from ingestion_pipeline.retry_policy import RetryPolicy

OK_BODY = {"data": [{"a": 1}], "has_more": False, "next_cursor": None}
SECRET = "super-secret-token"


def scripted(responses: list[Callable[[httpx.Request], httpx.Response] | httpx.Response]):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = responses[min(len(seen) - 1, len(responses) - 1)]
        return item(request) if callable(item) else item

    return handler, seen


def make_client(handler, *, max_attempts=4, **settings):
    s = PipelineSettings(
        api_base_url="https://user:pw@api.example.com/",
        api_token=SecretStr(SECRET),
        requests_per_second=0,
        max_attempts=max_attempts,
        **settings,
    )
    archived: list[AttemptRecord] = []
    sleeps: list[float] = []

    def archive(rec: AttemptRecord) -> int:
        archived.append(rec)
        return len(archived)

    client = ApiClient(
        s,
        http=httpx.Client(
            base_url="https://api.example.com", transport=httpx.MockTransport(handler)
        ),
        retry_policy=RetryPolicy(
            max_attempts=max_attempts, base_delay=0.1, max_delay=1, rng=random.Random(1)
        ),
        archive=archive,
        sleep=sleeps.append,
    )
    return client, archived, sleeps


def ok() -> httpx.Response:
    return httpx.Response(200, json=OK_BODY, headers={"X-Request-ID": "req-1"})


def test_success_sends_bearer_token_and_archives_attempt():
    handler, seen = scripted([ok()])
    client, archived, sleeps = make_client(handler)
    page = client.fetch_page("orders", {"limit": 10}, 1)
    assert page.envelope.data == [{"a": 1}]
    assert page.raw_response_id == 1 and page.attempts == 1
    assert seen[0].headers["Authorization"] == f"Bearer {SECRET}"
    rec = archived[0]
    assert rec.outcome == "ok" and rec.status_code == 200 and rec.request_id == "req-1"
    assert rec.body == OK_BODY and rec.record_count == 1 and len(rec.body_sha256) == 64
    assert sleeps == []


def test_archive_never_contains_credentials():
    handler, _ = scripted([httpx.Response(503), ok()])
    client, archived, _ = make_client(handler)
    client.fetch_page("orders", {"limit": 10}, 1)
    dumped = json.dumps([vars(r) for r in archived], default=str)
    assert SECRET not in dumped
    assert "pw@" not in dumped
    assert archived[0].endpoint == "https://api.example.com/v1/orders"


def test_429_honours_retry_after_then_recovers():
    handler, seen = scripted([httpx.Response(429, headers={"Retry-After": "7"}), ok()])
    client, archived, sleeps = make_client(handler)
    page = client.fetch_page("orders", {}, 1)
    assert page.attempts == 2 and len(seen) == 2
    assert sleeps[0] >= 7
    assert client.stats.rate_limited == 1 and client.stats.retries == 1
    assert [r.outcome for r in archived] == ["http_error", "ok"]


def test_transient_5xx_then_recovery():
    handler, _ = scripted([httpx.Response(500), httpx.Response(503), ok()])
    client, archived, sleeps = make_client(handler)
    assert client.fetch_page("orders", {}, 1).attempts == 3
    assert client.stats.server_errors == 2 and len(sleeps) == 2
    assert [r.status_code for r in archived] == [500, 503, 200]


def test_exhausted_retries_raise_with_last_error():
    handler, seen = scripted([httpx.Response(503)])
    client, archived, sleeps = make_client(handler, max_attempts=3)
    with pytest.raises(RetriesExhaustedError) as exc:
        client.fetch_page("orders", {}, 4)
    assert exc.value.last_error.category == "source_unavailable"
    assert len(seen) == 3 and len(archived) == 3 and len(sleeps) == 2


def test_five_consecutive_429s_are_bounded():
    handler, seen = scripted([httpx.Response(429, headers={"Retry-After": "1"})])
    client, _, sleeps = make_client(handler, max_attempts=5)
    with pytest.raises(RetriesExhaustedError):
        client.fetch_page("orders", {}, 1)
    assert len(seen) == 5 and len(sleeps) == 4 and all(s >= 1 for s in sleeps)


def test_retry_after_beyond_limit_fails_instead_of_hanging():
    handler, seen = scripted([httpx.Response(429, headers={"Retry-After": "86400"})])
    client, _, sleeps = make_client(handler, max_retry_after_seconds=60)
    with pytest.raises(RetriesExhaustedError, match="86400"):
        client.fetch_page("orders", {}, 1)
    assert len(seen) == 1 and sleeps == []


@pytest.mark.parametrize("status", [401, 403])
def test_invalid_credentials_fail_fast_without_retry(status):
    handler, seen = scripted([httpx.Response(status)])
    client, archived, sleeps = make_client(handler)
    with pytest.raises(AuthenticationError):
        client.fetch_page("orders", {}, 1)
    assert len(seen) == 1 and sleeps == [] and archived[0].status_code == status


def test_other_client_errors_are_permanent():
    handler, seen = scripted([httpx.Response(400, json={"detail": "bad param"})])
    client, _, _ = make_client(handler)
    with pytest.raises(ClientRequestError):
        client.fetch_page("orders", {}, 1)
    assert len(seen) == 1


def test_timeout_is_retried_then_recovers():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    handler, seen = scripted([timeout, ok()])
    client, archived, _ = make_client(handler)
    assert client.fetch_page("orders", {}, 1).attempts == 2
    assert len(seen) == 2
    assert client.stats.timeouts == 1
    assert archived[0].outcome == "timeout" and archived[0].status_code is None


def test_connection_error_is_retried():
    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    handler, _ = scripted([refused, ok()])
    client, archived, _ = make_client(handler)
    client.fetch_page("orders", {}, 1)
    assert archived[0].outcome == "transport_error"


def test_malformed_json_is_archived_and_retried():
    handler, _ = scripted([httpx.Response(200, text='{"data": [tru'), ok()])
    client, archived, _ = make_client(handler)
    client.fetch_page("orders", {}, 1)
    assert archived[0].outcome == "malformed"
    assert archived[0].body_text == '{"data": [tru' and archived[0].body is None


def test_envelope_contract_violation_exhausts_retries():
    handler, _ = scripted([httpx.Response(200, json={"items": []})])
    client, archived, _ = make_client(handler, max_attempts=2)
    with pytest.raises(RetriesExhaustedError) as exc:
        client.fetch_page("orders", {}, 1)
    assert isinstance(exc.value.last_error, MalformedResponseError)
    assert "data" in archived[0].error


def test_malformed_not_retried_when_disabled():
    handler, seen = scripted([httpx.Response(200, text="<html>")])
    client, _, _ = make_client(handler, retry_malformed_responses=False)
    with pytest.raises(MalformedResponseError):
        client.fetch_page("orders", {}, 1)
    assert len(seen) == 1


def test_sanitize_base_url():
    assert sanitize_base_url("https://u:p@h.com:8443/api/?key=1") == "https://h.com:8443/api"
