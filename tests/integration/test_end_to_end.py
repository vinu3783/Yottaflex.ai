"""End-to-end runs against the in-process mock API and a real PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import make_mock, release_phase, rows, scalar
from ingestion_pipeline.datagen import generate, write_dataset
from ingestion_pipeline.reporting import reconcile, run_summary


def snapshot(db):
    return {
        "orders": rows(
            db,
            "SELECT order_id, source_record_id, record_hash, order_amount, "
            "order_status FROM orders ORDER BY 1",
        ),
        "events": rows(db, "SELECT event_id, record_hash FROM order_events ORDER BY 1"),
    }


def failing(report):
    return [(c["check"], c["detail"]) for c in report["checks"] if c["status"] != "PASS"]


def test_full_multi_page_load_matches_oracle(db, make_pipeline, review_dataset, tmp_path):
    write_dataset(review_dataset, tmp_path)
    pipeline, _ = make_pipeline(make_mock(review_dataset, phase=9))
    result = pipeline.run()

    assert result.status == "succeeded"
    orders = result.entity_stats["orders"]
    assert orders.pages_processed > 5  # page_size=50 -> genuinely multi-page
    assert orders.records_fetched == len(review_dataset.orders)
    assert result.entity_stats["order_events"].records_fetched == len(review_dataset.order_events)

    report = reconcile(db, tmp_path)
    assert report["result"] == "PASS", failing(report)
    assert scalar(db, "SELECT count(*) FROM orders") == len(review_dataset.expected_orders)


def test_incremental_phases_lookback_and_backfill(db, make_pipeline, review_dataset, tmp_path):
    write_dataset(review_dataset, tmp_path)
    api = make_mock(review_dataset, phase=1)
    pipeline, _ = make_pipeline(api)

    assert pipeline.run().status == "succeeded"
    release_phase(api, 2)
    second = pipeline.run()
    assert second.status == "succeeded"
    # the lookback re-read is visible as duplicates, not as new rows
    assert second.entity_stats["orders"].records_duplicate > 0

    report = reconcile(db, tmp_path)
    oracle = next(c for c in report["checks"] if c["check"].startswith("oracle: orders target"))
    beyond = sorted(
        lab.source_record_id
        for lab in review_dataset.labels
        if lab.scenario == "late:new_order_beyond_lookback"
    )
    # only the rows older than the lookback window are missing - and they are detected
    assert oracle["status"] == "FAIL"
    assert oracle["detail"]["missing_count"] == len(beyond)

    backfill = pipeline.run(since=datetime(2025, 12, 1, tzinfo=UTC))
    assert backfill.status == "succeeded"
    assert backfill.entity_stats["orders"].records_inserted == len(beyond)
    report = reconcile(db, tmp_path)
    assert report["result"] == "PASS", failing(report)


def test_rerunning_same_input_creates_no_duplicates(db, make_pipeline, review_dataset):
    api = make_mock(review_dataset, phase=9)
    pipeline, _ = make_pipeline(api)
    pipeline.run()
    before = snapshot(db)

    # full replay of the whole feed (backfill from the beginning) and a normal rerun
    for kwargs in ({"since": datetime(2000, 1, 1, tzinfo=UTC)}, {}):
        result = pipeline.run(**kwargs)
        assert result.status == "succeeded"
        for stats in result.entity_stats.values():
            assert stats.records_inserted == 0
            assert stats.records_updated == 0
        assert snapshot(db) == before


def test_empty_source(db, make_pipeline):
    pipeline, _ = make_pipeline(make_mock(generate(1, 0)))
    result = pipeline.run()
    assert result.status == "succeeded"
    assert all(s.records_fetched == 0 for s in result.entity_stats.values())
    assert scalar(db, "SELECT count(*) FROM checkpoints") == 0
    assert scalar(db, "SELECT count(*) FROM raw_responses") == 2  # one empty page per entity


def test_run_metadata_is_complete(db, make_pipeline, review_dataset):
    pipeline, _ = make_pipeline(make_mock(review_dataset))
    result = pipeline.run()
    summary = run_summary(db, result.run_id)
    run = summary["run"]
    assert run["status"] == "succeeded" and run["mode"] == "incremental"
    assert run["finished_at"] and run["duration_seconds"] is not None
    assert run["http_attempts"] == result.http.attempts > 0
    assert run["checkpoint_before"] == {} and set(run["checkpoint_after"]) == {
        "orders",
        "order_events",
    }
    assert "password" not in run["target"] and "pipeline:pipeline" not in run["target"]
    for ent in summary["entities"].values():
        assert ent["unexplained_difference"] == 0
    assert run_summary(db)["run"]["run_id"] == result.run_id  # latest


def test_max_page_size_fits_postgres_parameter_limit(db, make_pipeline):
    """page_size is capped at 4,000: one page = one multi-row upsert with 15 bind
    parameters per order, which must stay under PostgreSQL's 65,535 limit."""
    ds = generate(3, 1500, edge_cases=False, phases=False)
    assert len(ds.orders) > 4000
    pipeline, _ = make_pipeline(make_mock(ds, max_page_size=4000), page_size=4000)
    result = pipeline.run(entities=["orders"])
    assert result.status == "succeeded"
    assert result.entity_stats["orders"].pages_processed == -(-len(ds.orders) // 4000)
    assert scalar(db, "SELECT count(*) FROM orders") == 1500
