"""Run tracking: durable run metadata and per-entity counters.

Counter semantics (record level, per delivery - see docs/DATA_DICTIONARY.md):

    records_fetched = inserted + updated + duplicate + stale + quarantined

``records_valid`` counts records that passed schema validation; conflicts and
unknown-order events are valid by schema but still quarantined, so
``records_valid >= inserted + updated + duplicate + stale``.

Entity counters are written inside each page transaction, so they always
agree with what was actually committed - a crash cannot leave counters that
claim more than the target tables contain.
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, Engine, insert, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import PIPELINE_NAME, PIPELINE_VERSION
from .api_client import HttpStats
from .logging_config import redact
from .models import pipeline_runs, run_entity_stats


@dataclass
class EntityStats:
    pages_requested: int = 0
    pages_processed: int = 0
    records_fetched: int = 0
    records_valid: int = 0
    records_inserted: int = 0
    records_updated: int = 0
    records_duplicate: int = 0
    records_stale: int = 0
    records_quarantined: int = 0
    unknown_fields: Counter = field(default_factory=Counter)
    start_cursor: dict[str, Any] | None = None
    end_cursor: dict[str, Any] | None = None

    def add_outcome(self, outcome: str, n: int = 1) -> None:
        attr = f"records_{outcome}"
        setattr(self, attr, getattr(self, attr) + n)

    def as_row(self) -> dict[str, Any]:
        return {
            "pages_requested": self.pages_requested,
            "pages_processed": self.pages_processed,
            "records_fetched": self.records_fetched,
            "records_valid": self.records_valid,
            "records_inserted": self.records_inserted,
            "records_updated": self.records_updated,
            "records_duplicate": self.records_duplicate,
            "records_stale": self.records_stale,
            "records_quarantined": self.records_quarantined,
            "unknown_fields": dict(self.unknown_fields),
            "start_cursor": self.start_cursor,
            "end_cursor": self.end_cursor,
        }


class RunTracker:
    def __init__(self, engine: Engine, *, source: str, target: str) -> None:
        self.engine = engine
        self.source = source
        self.target = target
        self.run_id = str(uuid.uuid4())
        self._t0 = 0.0

    def start(self, mode: str, params: dict[str, Any], checkpoint_before: dict[str, Any]) -> str:
        self._t0 = time.monotonic()
        with self.engine.begin() as conn:
            conn.execute(
                insert(pipeline_runs).values(
                    run_id=self.run_id,
                    pipeline_name=PIPELINE_NAME,
                    pipeline_version=PIPELINE_VERSION,
                    status="running",
                    mode=mode,
                    source=self.source,
                    target=self.target,
                    params=params,
                    started_at=datetime.now(UTC),
                    checkpoint_before=checkpoint_before,
                )
            )
        return self.run_id

    def save_entity(self, conn: Connection, entity: str, stats: EntityStats) -> None:
        row = stats.as_row()
        stmt = pg_insert(run_entity_stats).values(run_id=self.run_id, entity=entity, **row)
        conn.execute(stmt.on_conflict_do_update(index_elements=["run_id", "entity"], set_=row))

    def save_http(self, conn: Connection, http: HttpStats) -> None:
        conn.execute(
            update(pipeline_runs)
            .where(pipeline_runs.c.run_id == self.run_id)
            .values(**self._http_values(http))
        )

    @staticmethod
    def _http_values(http: HttpStats) -> dict[str, int]:
        return {
            "http_attempts": http.attempts,
            "http_retries": http.retries,
            "http_rate_limited": http.rate_limited,
            "http_server_errors": http.server_errors,
            "http_timeouts": http.timeouts,
        }

    def finish(
        self,
        status: str,
        http: HttpStats,
        checkpoint_after: dict[str, Any],
        *,
        failure_category: str | None = None,
        error_message: str | None = None,
    ) -> float:
        duration = time.monotonic() - self._t0
        with self.engine.begin() as conn:
            conn.execute(
                update(pipeline_runs)
                .where(pipeline_runs.c.run_id == self.run_id)
                .values(
                    status=status,
                    finished_at=datetime.now(UTC),
                    duration_seconds=Decimal(f"{duration:.3f}"),
                    checkpoint_after=checkpoint_after,
                    failure_category=failure_category,
                    error_message=redact(error_message)[:2000] if error_message else None,
                    **self._http_values(http),
                )
            )
        return duration


def mark_abandoned_runs(engine: Engine) -> int:
    """Runs still 'running' while we hold the pipeline lock must belong to a
    process that died (the lock is released when its session ends)."""
    with engine.begin() as conn:
        return conn.execute(
            update(pipeline_runs)
            .where(
                pipeline_runs.c.pipeline_name == PIPELINE_NAME, pipeline_runs.c.status == "running"
            )
            .values(
                status="abandoned",
                finished_at=datetime.now(UTC),
                failure_category="process_died",
                error_message="run was still 'running' when a later run acquired the lock",
            )
        ).rowcount
