"""Durable incremental checkpoints.

A checkpoint is the composite keyset position ``(source_updated_at,
source_record_id)`` of the last source row whose page has been fully
processed *and committed*. It is written in the same database transaction as
that page's target rows, quarantine rows and run counters, so it can never
point past data that was not persisted.

Advances are monotonic (the SQL refuses to move a checkpoint backwards), so a
backfill or a lookback re-scan can never rewind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Connection, delete, select, text

from .models import checkpoints
from .transformations import iso


@dataclass(frozen=True, order=True)
class Cursor:
    updated_at: datetime
    record_id: str

    def as_params(self) -> dict[str, str]:
        return {"after_updated_at": iso(self.updated_at), "after_id": self.record_id}

    def as_dict(self) -> dict[str, Any]:
        return {"source_updated_at": iso(self.updated_at), "source_record_id": self.record_id}


def load(conn: Connection, pipeline: str, entity: str) -> Cursor | None:
    row = conn.execute(
        select(checkpoints.c.cursor_updated_at, checkpoints.c.cursor_record_id).where(
            checkpoints.c.pipeline_name == pipeline, checkpoints.c.entity == entity
        )
    ).one_or_none()
    return Cursor(row[0], row[1]) if row else None


def load_all(conn: Connection, pipeline: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        select(checkpoints).where(checkpoints.c.pipeline_name == pipeline)
    ).mappings()
    return {
        r["entity"]: Cursor(r["cursor_updated_at"], r["cursor_record_id"]).as_dict() for r in rows
    }


_ADVANCE_SQL = text(
    """
    INSERT INTO checkpoints AS c
        (pipeline_name, entity, cursor_updated_at, cursor_record_id, last_run_id, updated_at)
    VALUES (:pipeline, :entity, :ts, :rid, :run_id, now())
    ON CONFLICT (pipeline_name, entity) DO UPDATE
       SET cursor_updated_at = EXCLUDED.cursor_updated_at,
           cursor_record_id  = EXCLUDED.cursor_record_id,
           last_run_id       = EXCLUDED.last_run_id,
           updated_at        = now()
     -- monotonic: only move forward in keyset order
     WHERE (c.cursor_updated_at, c.cursor_record_id)
         < (EXCLUDED.cursor_updated_at, EXCLUDED.cursor_record_id)
    """
)


def advance(conn: Connection, pipeline: str, entity: str, cursor: Cursor, run_id: str) -> bool:
    """Returns True if the checkpoint moved."""
    result = conn.execute(
        _ADVANCE_SQL,
        {
            "pipeline": pipeline,
            "entity": entity,
            "ts": cursor.updated_at,
            "rid": cursor.record_id,
            "run_id": run_id,
        },
    )
    return result.rowcount == 1


def reset(conn: Connection, pipeline: str, entity: str | None = None) -> int:
    stmt = delete(checkpoints).where(checkpoints.c.pipeline_name == pipeline)
    if entity:
        stmt = stmt.where(checkpoints.c.entity == entity)
    return conn.execute(stmt).rowcount


def start_params(cursor: Cursor | None, lookback_seconds: int) -> dict[str, str]:
    """Where an incremental scan begins.

    * no checkpoint -> from the beginning of the feed
    * lookback 0     -> strictly after the checkpoint (exclusive keyset cursor)
    * lookback > 0   -> re-read the window [checkpoint - lookback, ...] to pick up
      late-arriving rows; idempotent publishing makes the overlap harmless.
    """
    if cursor is None:
        return {}
    if lookback_seconds <= 0:
        return cursor.as_params()
    return {"updated_since": iso(cursor.updated_at - timedelta(seconds=lookback_seconds))}
