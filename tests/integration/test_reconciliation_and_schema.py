"""Reconciliation catches silent data loss; migrations match the models; CLI smoke."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from conftest import TEST_DATABASE_URL, make_mock
from ingestion_pipeline.cli import main
from ingestion_pipeline.datagen import write_dataset
from ingestion_pipeline.models import metadata
from ingestion_pipeline.reporting import reconcile, render_markdown


def statuses(report):
    return {c["check"]: c["status"] for c in report["checks"]}


def test_reconciliation_detects_silent_data_loss(db, make_pipeline, review_dataset, tmp_path):
    write_dataset(review_dataset, tmp_path)
    pipeline, _ = make_pipeline(make_mock(review_dataset, phase=9))
    pipeline.run()
    assert reconcile(db, tmp_path)["result"] == "PASS"

    # simulate a row silently disappearing from the target
    with db.begin() as conn:
        victim = conn.execute(
            text(
                "SELECT order_id FROM orders o WHERE NOT EXISTS "
                "(SELECT 1 FROM order_events e WHERE e.order_id = o.order_id) LIMIT 1"
            )
        ).scalar_one()
        conn.execute(text("DELETE FROM orders WHERE order_id = :o"), {"o": victim})
    report = reconcile(db, tmp_path)
    s = statuses(report)
    assert report["result"] == "FAIL"
    assert s["orders: target rows = 'inserted' outcomes (each key inserted once)"] == "FAIL"
    assert s["oracle: orders target = expected keys and latest valid version"] == "FAIL"
    assert "FAIL" in render_markdown(report)


def test_reconciliation_detects_counter_drift(db, make_pipeline, review_dataset):
    pipeline, _ = make_pipeline(make_mock(review_dataset))
    pipeline.run()
    with db.begin() as conn:
        conn.execute(
            text(
                "UPDATE run_entity_stats SET records_fetched = records_fetched + 1 "
                "WHERE entity = 'orders'"
            )
        )
    s = statuses(reconcile(db))
    key = "orders: per-run fetched = inserted+updated+duplicate+stale+quarantined = ledger rows"
    assert s[key] == "FAIL"


def test_migration_matches_models(engine):
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), metadata)
    assert diff == []


def test_cli_end_to_end(db, tmp_path, monkeypatch, capsys):
    out = tmp_path / "ds"
    assert main(["generate-data", "--out", str(out), "--orders", "60"]) == 0
    monkeypatch.setenv("PIPELINE_DATABASE_URL", TEST_DATABASE_URL)
    assert main(["checkpoints", "show"]) == 0
    assert main(["checkpoints", "reset"]) == 2  # refuses without --yes
    assert main(["reconcile", "--out", str(tmp_path / "rep")]) == 0
    assert (tmp_path / "rep.md").exists() and (tmp_path / "rep.json").exists()
    assert main(["runs"]) == 0
    assert main(["show-run", "latest"]) == 1  # no runs yet
    capsys.readouterr()
