"""Failure injection: retries, permanent failures, crashes and restarts."""

from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest
import uvicorn
from sqlalchemy import text

from conftest import TOKEN, make_mock, rows, scalar, set_faults
from ingestion_pipeline import PIPELINE_NAME
from ingestion_pipeline.datagen import generate
from ingestion_pipeline.db import pipeline_lock
from ingestion_pipeline.errors import ConcurrentRunError, SimulatedCrash
from ingestion_pipeline.mock_api import MockSettings, create_app
from ingestion_pipeline.pipeline import Hooks


@pytest.fixture(scope="module")
def dataset():
    # no edge cases: every page is clean, so page/row arithmetic is exact
    return generate(5, 150, edge_cases=False, phases=False)


def clean_state(db):
    return rows(db, "SELECT order_id, source_record_id FROM orders ORDER BY 1"), rows(
        db, "SELECT event_id FROM order_events ORDER BY 1"
    )


@pytest.fixture
def reference_state(db, make_pipeline, dataset):
    """Final target state of an uninterrupted run, for comparison."""
    pipeline, _ = make_pipeline(make_mock(dataset))
    assert pipeline.run().status == "succeeded"
    state = clean_state(db)
    with db.begin() as conn:
        conn.execute(text("TRUNCATE pipeline_runs RESTART IDENTITY CASCADE"))
    return state


def test_transient_503_then_recovery(db, make_pipeline, dataset):
    api = make_mock(dataset)
    set_faults(api, [{"entity": "orders", "page": 2, "status_code": 503, "times": 2}])
    pipeline, sleep = make_pipeline(api)
    result = pipeline.run()
    assert result.status == "succeeded"
    assert result.http.server_errors == 2 and result.http.retries == 2 and len(sleep.calls) == 2
    statuses = rows(
        db,
        "SELECT status_code FROM raw_responses WHERE entity='orders' "
        "AND page_number=2 ORDER BY attempt",
    )
    assert [s[0] for s in statuses] == [503, 503, 200]


def test_429_with_retry_after(db, make_pipeline, dataset):
    api = make_mock(dataset)
    set_faults(api, [{"entity": "order_events", "status_code": 429, "retry_after": 3, "times": 1}])
    pipeline, sleep = make_pipeline(api)
    result = pipeline.run()
    assert result.status == "succeeded"
    assert result.http.rate_limited == 1
    assert sleep.calls == [pytest.approx(3, abs=0.06)]
    assert scalar(db, "SELECT http_rate_limited FROM pipeline_runs") == 1


def test_exhausted_retries_fail_run_and_restart_resumes(
    db, make_pipeline, dataset, reference_state
):
    api = make_mock(dataset)
    set_faults(api, [{"entity": "orders", "page": 3, "status_code": 503, "times": None}])
    pipeline, _ = make_pipeline(api, lookback_seconds=0)

    failed = pipeline.run()
    assert failed.status == "failed" and failed.failure_category == "retries_exhausted"
    # pages 1-2 committed, page 3 never published; events never started
    assert scalar(db, "SELECT count(*) FROM record_outcomes") == 100
    assert scalar(db, "SELECT count(*) FROM order_events") == 0
    ckpt = rows(db, "SELECT entity, cursor_record_id FROM checkpoints")
    page2_last = sorted(dataset.orders, key=lambda r: (r.source_updated_at, r.source_record_id))[99]
    assert ckpt == [("orders", page2_last.source_record_id)]
    run = rows(db, "SELECT status, failure_category, error_message FROM pipeline_runs")[0]
    assert run[0] == "failed" and "page 3" in run[2]

    api.delete("/_admin/faults")
    resumed = pipeline.run()
    assert resumed.status == "succeeded"
    # resumed strictly after the checkpoint: nothing from pages 1-2 re-fetched
    assert resumed.entity_stats["orders"].records_fetched == len(dataset.orders) - 100
    assert clean_state(db) == reference_state


def test_invalid_credentials(db, make_pipeline, dataset):
    pipeline, sleep = make_pipeline(make_mock(dataset), api_token="wrong-token")
    result = pipeline.run()
    assert result.status == "failed" and result.failure_category == "authentication"
    assert result.http.attempts == 1 and sleep.calls == []
    assert scalar(db, "SELECT count(*) FROM orders") == 0
    assert scalar(db, "SELECT count(*) FROM checkpoints") == 0
    # the rejected attempt is archived without the token
    dump = scalar(db, "SELECT string_agg(r::text, '') FROM raw_responses r")
    assert "wrong-token" not in dump and TOKEN not in dump


def test_malformed_response_retried_then_fails_clearly(db, make_pipeline, dataset):
    api = make_mock(dataset)
    set_faults(api, [{"entity": "orders", "kind": "malformed", "times": 1}])
    pipeline, _ = make_pipeline(api)
    assert pipeline.run().status == "succeeded"
    assert (
        scalar(
            db,
            "SELECT count(*) FROM raw_responses WHERE outcome='malformed' "
            "AND body_text IS NOT NULL",
        )
        == 1
    )

    set_faults(api, [{"entity": "order_events", "kind": "invalid_envelope", "times": None}])
    result = pipeline.run()
    assert result.status == "failed" and result.failure_category == "retries_exhausted"
    assert "malformed_response" in result.error_message


def test_breaking_schema_change_stops_before_publishing(db, make_pipeline, dataset):
    api = make_mock(dataset)
    set_faults(
        api,
        [
            {
                "entity": "orders",
                "page": 2,
                "kind": "drop_field",
                "field": "order_amount",
                "times": None,
            }
        ],
    )
    pipeline, _ = make_pipeline(api, lookback_seconds=0)
    result = pipeline.run()
    assert result.status == "failed" and result.failure_category == "breaking_schema_change"
    assert scalar(db, "SELECT count(*) FROM record_outcomes") == 50  # page 1 only
    assert scalar(db, "SELECT count(*) FROM quarantine") == 0  # page 2 NOT quarantined
    # ...but its raw response is retained for diagnosis
    assert (
        scalar(db, "SELECT count(*) FROM raw_responses WHERE page_number=2 AND outcome='ok'") == 1
    )


def test_stuck_cursor_is_detected_not_looped(db, make_pipeline, dataset):
    api = make_mock(dataset)
    set_faults(api, [{"entity": "orders", "page": 2, "kind": "stuck_cursor", "times": None}])
    pipeline, _ = make_pipeline(api)
    result = pipeline.run()
    assert result.status == "failed" and result.failure_category == "non_advancing_cursor"
    assert result.http.attempts == 2


def test_crash_after_publish_before_commit_loses_nothing(
    db, make_pipeline, dataset, reference_state
):
    def crash(entity, page):
        if entity == "orders" and page == 2:
            raise SimulatedCrash("kill -9 during page 2 transaction")

    pipeline, _ = make_pipeline(
        make_mock(dataset), hooks=Hooks(before_commit=crash), lookback_seconds=0
    )
    with pytest.raises(SimulatedCrash):
        pipeline.run()
    # page 2's upserts, ledger, counters and checkpoint all rolled back together
    assert scalar(db, "SELECT count(*) FROM record_outcomes") == 50
    assert scalar(db, "SELECT records_fetched FROM run_entity_stats") == 50
    assert scalar(db, "SELECT status FROM pipeline_runs") == "running"  # like a dead process

    pipeline, _ = make_pipeline(make_mock(dataset), lookback_seconds=0)
    result = pipeline.run()
    assert result.status == "succeeded"
    assert rows(db, "SELECT status FROM pipeline_runs ORDER BY started_at") == [
        ("abandoned",),
        ("succeeded",),
    ]
    assert clean_state(db) == reference_state


def test_crash_after_commit_resumes_from_checkpoint(db, make_pipeline, dataset, reference_state):
    def crash(entity, page):
        if entity == "orders" and page == 2:
            raise SimulatedCrash("crash right after commit")

    pipeline, _ = make_pipeline(
        make_mock(dataset), hooks=Hooks(after_commit=crash), lookback_seconds=0
    )
    with pytest.raises(SimulatedCrash):
        pipeline.run()
    assert scalar(db, "SELECT count(*) FROM record_outcomes") == 100

    pipeline, _ = make_pipeline(make_mock(dataset), lookback_seconds=0)
    result = pipeline.run()
    assert result.entity_stats["orders"].records_fetched == len(dataset.orders) - 100
    assert clean_state(db) == reference_state


def test_page_budget_stops_early_and_next_run_continues(
    db, make_pipeline, dataset, reference_state
):
    pipeline, _ = make_pipeline(make_mock(dataset), lookback_seconds=0)
    first = pipeline.run(max_pages=2)
    assert first.status == "succeeded" and first.stopped_early
    assert scalar(db, "SELECT count(*) FROM record_outcomes") == 100
    assert pipeline.run().status == "succeeded"
    assert clean_state(db) == reference_state


def test_only_one_run_at_a_time(db, make_pipeline, dataset):
    pipeline, _ = make_pipeline(make_mock(dataset))
    with pipeline_lock(db, PIPELINE_NAME), pytest.raises(ConcurrentRunError):
        pipeline.run()
    assert scalar(db, "SELECT count(*) FROM pipeline_runs") == 0


# ------------------------------------------------------------------ real HTTP timeout
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(dataset):
    port = _free_port()
    app = create_app(
        MockSettings(api_token=TOKEN),
        feeds={"orders": dataset.orders, "order_events": dataset.order_events},
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_real_timeout_is_retried(db, make_pipeline, live_server):
    httpx.post(
        f"{live_server}/_admin/faults",
        json=[{"entity": "orders", "page": 1, "kind": "delay", "delay_seconds": 1.0, "times": 1}],
    ).raise_for_status()
    pipeline, _ = make_pipeline(None, api_base_url=live_server, request_timeout_seconds=0.3)
    result = pipeline.run()
    assert result.status == "succeeded"
    assert result.http.timeouts == 1
    assert scalar(db, "SELECT count(*) FROM raw_responses WHERE outcome='timeout'") == 1
