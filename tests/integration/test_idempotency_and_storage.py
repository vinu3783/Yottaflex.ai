"""Publishing rules, conflict policy, DB constraints, raw retention, quarantine traceability."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError

from conftest import TOKEN, make_mock, rows, scalar
from ingestion_pipeline.datagen import FeedRow, generate
from ingestion_pipeline.models import pipeline_runs, raw_responses
from ingestion_pipeline.repository import PublishContext, _order_row, _upsert_orders
from ingestion_pipeline.validation import validate_record


def order(n=1, ts="2026-01-01T00:10:00Z", rid="ORDV-1", **kw):
    p = {
        "order_id": f"ORD-{n:08d}",
        "customer_id": "CUST-000001",
        "order_status": "paid",
        "order_amount": "10.00",
        "currency": "USD",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": ts,
        "source_updated_at": ts,
        "source_record_id": rid,
    }
    p.update(kw)
    return p


def event(eid=1, order_id="ORD-00000001", rid="EVTV-1", **kw):
    p = {
        "event_id": f"EVT-{eid:010d}",
        "order_id": order_id,
        "event_type": "order_created",
        "event_timestamp": "2026-01-01T00:00:00Z",
        "source_updated_at": "2026-01-01T00:20:00Z",
        "source_record_id": rid,
    }
    p.update(kw)
    return p


def feed(entity, payloads):
    return [
        FeedRow(entity, p["source_record_id"], datetime.fromisoformat(p["source_updated_at"]), 1, p)
        for p in payloads
    ]


def run_with(make_pipeline, orders=(), events=(), **kw):
    ds = generate(1, 0)
    ds.orders = feed("orders", orders)
    ds.order_events = feed("order_events", events)
    pipeline, _ = make_pipeline(make_mock(ds), **kw)
    return pipeline.run(since=datetime(2000, 1, 1, tzinfo=UTC))


def outcomes(db, entity="orders"):
    return [
        r[0]
        for r in rows(
            db, "SELECT outcome FROM record_outcomes WHERE entity=:e ORDER BY outcome_id", e=entity
        )
    ]


@pytest.mark.parametrize("write_mode", ["batch", "row"])
def test_versions_duplicates_and_conflicts_in_one_page(db, make_pipeline, write_mode):
    run_with(
        make_pipeline,
        write_mode=write_mode,
        orders=[
            order(rid="ORDV-1", ts="2026-01-01T00:10:00Z"),
            order(rid="ORDV-2", ts="2026-01-01T00:10:00Z"),  # identical re-delivery
            order(rid="ORDV-3", ts="2026-01-01T00:10:00Z", order_amount="99.00"),  # conflict
            order(rid="ORDV-4", ts="2026-01-01T00:20:00Z", order_status="shipped"),  # newer
        ],
    )
    assert outcomes(db) == ["inserted", "duplicate", "quarantined", "updated"]
    assert rows(db, "SELECT source_record_id, order_status, order_amount FROM orders") == [
        ("ORDV-4", "shipped", 10)
    ]
    q = rows(db, "SELECT source_record_id, error_codes FROM quarantine")
    assert q == [("ORDV-3", ["CONFLICTING_DUPLICATE"])]


def test_older_version_never_overwrites_newer(db, make_pipeline):
    run_with(
        make_pipeline,
        orders=[order(rid="ORDV-9", ts="2026-01-02T00:00:00Z", order_status="delivered")],
    )
    first_run = scalar(db, "SELECT first_run_id::text FROM orders")
    run_with(
        make_pipeline,
        orders=[order(rid="ORDV-1", ts="2026-01-01T00:00:00Z", order_status="pending")],
    )
    assert outcomes(db)[-1] == "stale"
    assert rows(db, "SELECT source_record_id, order_status FROM orders") == [
        ("ORDV-9", "delivered")
    ]
    # provenance of the first insert is preserved
    assert scalar(db, "SELECT first_run_id::text FROM orders") == first_run


def test_sql_guard_blocks_older_version_even_without_python_check(db, make_pipeline):
    run_with(make_pipeline, orders=[order(rid="ORDV-9", ts="2026-01-02T00:00:00Z")])
    run_id = scalar(db, "SELECT run_id::text FROM pipeline_runs")
    raw_id = scalar(db, "SELECT min(raw_response_id) FROM raw_responses")
    older = validate_record("orders", 0, order(rid="ORDV-1", ts="2026-01-01T00:00:00Z")).record

    ctx = PublishContext(run_id=run_id, raw_response_id=raw_id, now=datetime.now(UTC))
    with db.begin() as conn:
        assert _upsert_orders(conn, [_order_row(older, ctx)]) == 0
    assert scalar(db, "SELECT source_record_id FROM orders") == "ORDV-9"


def test_replaying_same_page_is_idempotent(db, make_pipeline):
    payloads = [order(n, rid=f"ORDV-{n}") for n in range(1, 6)]
    run_with(make_pipeline, orders=payloads)
    snap = rows(db, "SELECT * FROM orders ORDER BY order_id")
    run_with(make_pipeline, orders=payloads)
    assert outcomes(db)[5:] == ["duplicate"] * 5
    after = rows(db, "SELECT * FROM orders ORDER BY order_id")
    # only bookkeeping columns may differ; business columns identical
    assert [r[:11] for r in after] == [r[:11] for r in snap]


def test_events_out_of_order_unknown_order_and_conflicts(db, make_pipeline):
    run_with(
        make_pipeline,
        orders=[order(1)],
        events=[
            event(1, rid="EVTV-1", event_timestamp="2026-01-01T05:00:00Z"),
            event(2, rid="EVTV-2", event_timestamp="2025-12-31T05:00:00Z"),  # occurred earlier
            event(2, rid="EVTV-3", event_timestamp="2025-12-31T05:00:00Z"),  # re-delivery
            event(2, rid="EVTV-4", event_type="order_cancelled"),  # same id, new content
            event(3, rid="EVTV-5", order_id="ORD-99999999"),  # unknown order
        ],
    )
    assert outcomes(db, "order_events") == [
        "inserted",
        "inserted",
        "duplicate",
        "quarantined",
        "quarantined",
    ]
    assert rows(db, "SELECT source_record_id, error_codes FROM quarantine ORDER BY 1") == [
        ("EVTV-4", ["CONFLICTING_DUPLICATE"]),
        ("EVTV-5", ["UNKNOWN_ORDER_ID"]),
    ]
    # both distinct events preserved even though they arrived out of event-time order
    assert scalar(db, "SELECT count(*) FROM order_events") == 2


def test_quarantined_event_resolved_when_parent_order_arrives_later(db, make_pipeline):
    run_with(make_pipeline, orders=[], events=[event(1, rid="EVTV-1")])
    assert scalar(db, "SELECT count(*) FROM quarantine WHERE resolved_at IS NULL") == 1
    run_with(make_pipeline, orders=[order(1)], events=[event(1, rid="EVTV-1")])
    assert scalar(db, "SELECT count(*) FROM order_events") == 1
    assert scalar(db, "SELECT count(*) FROM quarantine WHERE resolved_at IS NULL") == 0


def test_quarantine_traces_back_to_exact_raw_response(db, make_pipeline):
    bad = order(2, rid="ORDV-BAD", order_amount="-3.00")
    run_with(make_pipeline, orders=[order(1, rid="ORDV-1"), bad])
    q = rows(
        db,
        "SELECT q.payload, q.error_codes, q.errors, r.body -> 'data' -> q.record_position, "
        "r.request_params, r.run_id = q.run_id FROM quarantine q "
        "JOIN raw_responses r USING (raw_response_id)",
    )
    payload, codes, errors, raw_record, params, same_run = q[0]
    assert payload == bad == raw_record
    assert params["updated_since"] and params["limit"] == 50
    assert codes == ["NEGATIVE_AMOUNT"] and errors[0]["field"] == "order_amount"
    assert "message" in errors[0] and same_run


def test_raw_response_retained_verbatim_and_without_secrets(db, make_pipeline):
    payloads = [order(1, rid="ORDV-1"), order(2, rid="ORDV-2")]
    run_with(make_pipeline, orders=payloads)
    body, sha, status, req_id = rows(
        db,
        "SELECT body, body_sha256, status_code, request_id FROM raw_responses "
        "WHERE entity='orders'",
    )[0]
    assert body["data"] == payloads and status == 200 and req_id.startswith("req-")
    assert len(sha) == 64
    everything = scalar(db, "SELECT string_agg(r::text, '') FROM raw_responses r")
    assert TOKEN not in everything and "Bearer" not in everything


def test_database_constraints_back_up_application_rules(db):
    run_id = str(uuid.uuid4())
    with db.begin() as conn:
        conn.execute(
            insert(pipeline_runs).values(
                run_id=run_id,
                pipeline_name="t",
                pipeline_version="1",
                status="running",
                mode="incremental",
                source="s",
                target="t",
                params={},
                started_at=datetime.now(UTC),
            )
        )
        raw_id = conn.execute(
            insert(raw_responses)
            .values(
                run_id=run_id,
                entity="orders",
                endpoint="/",
                request_params={},
                page_number=1,
                attempt=1,
                outcome="ok",
                retrieved_at=datetime.now(UTC),
            )
            .returning(raw_responses.c.raw_response_id)
        ).scalar_one()
    base = (
        "INSERT INTO orders VALUES (:id, 'CUST-1', :status, :amount, 'USD', "
        "'2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z', '2026-01-02T00:00:00Z', "
        "'r', 'h', now(), now(), :run, :run, :raw)"
    )
    ok = {"id": "ORD-1", "status": "paid", "amount": 1, "run": run_id, "raw": raw_id}
    with db.begin() as conn:
        conn.execute(text(base), ok)
    for bad in ({"id": "ORD-1"}, {"id": "ORD-2", "amount": -1}, {"id": "ORD-3", "status": "x"}):
        with pytest.raises(IntegrityError), db.begin() as conn:
            conn.execute(text(base), {**ok, **bad})
    with pytest.raises(IntegrityError), db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO order_events VALUES ('EVT-1', 'ORD-404', 'order_created', now(), "
                "now(), 'r', 'h', now(), :run, :raw)"
            ),
            {"run": run_id, "raw": raw_id},
        )


def test_failed_publish_rolls_back_whole_page(db, make_pipeline, monkeypatch):
    """A DB error mid-page must not leave half a page, a moved checkpoint or counters."""
    import ingestion_pipeline.repository as repo

    real = repo.publish_orders

    def boom(conn, results, ctx):
        real(conn, results, ctx)  # rows written inside the transaction...
        raise IntegrityError("simulated", {}, Exception("constraint"))  # ...then a DB error

    monkeypatch.setitem(repo.PUBLISHERS, "orders", boom)
    with pytest.raises(IntegrityError):
        run_with(make_pipeline, orders=[order(1)])
    assert scalar(db, "SELECT count(*) FROM orders") == 0
    assert scalar(db, "SELECT count(*) FROM checkpoints") == 0
    assert scalar(db, "SELECT count(*) FROM record_outcomes") == 0
    assert rows(db, "SELECT status, failure_category FROM pipeline_runs") == [
        ("failed", "database_error")
    ]
