"""Quarantine writes and the per-record outcome ledger.

Quarantine keeps the *original* payload, every error code, readable messages
and the ``raw_response_id`` + position, so any rejected record can be traced
back to the exact HTTP response it came from.

``record_outcomes`` is the audit ledger: one row per fetched record per
delivery, whatever happened to it. It is what makes count reconciliation exact.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Connection, insert, update

from .models import quarantine, record_outcomes
from .validation import Issue, ValidationResult


def insert_quarantine(
    conn: Connection,
    entity: str,
    items: list[tuple[ValidationResult, list[Issue]]],
    *,
    run_id: str,
    raw_response_id: int,
    now: datetime,
) -> int:
    if not items:
        return 0
    rows = [
        {
            "run_id": run_id,
            "entity": entity,
            "raw_response_id": raw_response_id,
            "record_position": res.position,
            "source_record_id": res.source_record_id,
            "business_key": res.business_key(entity),
            "payload": res.payload,
            "error_codes": sorted({i.code for i in issues}),
            "errors": [i.as_dict() for i in issues],
            "rejected_at": now,
        }
        for res, issues in items
    ]
    conn.execute(insert(quarantine), rows)
    return len(rows)


def resolve_quarantine(
    conn: Connection, entity: str, source_record_ids: list[str], *, run_id: str, now: datetime
) -> int:
    """A previously quarantined source row that is now accepted (e.g. an event
    whose order arrived later, re-read by a backfill) is marked resolved."""
    if not source_record_ids:
        return 0
    return conn.execute(
        update(quarantine)
        .where(
            quarantine.c.entity == entity,
            quarantine.c.source_record_id.in_(source_record_ids),
            quarantine.c.resolved_at.is_(None),
        )
        .values(resolved_at=now, resolved_by_run_id=run_id)
    ).rowcount


def insert_outcomes(
    conn: Connection,
    entity: str,
    outcomes: list[tuple[ValidationResult, str]],
    *,
    run_id: str,
    raw_response_id: int,
) -> None:
    if not outcomes:
        return
    conn.execute(
        insert(record_outcomes),
        [
            {
                "run_id": run_id,
                "entity": entity,
                "raw_response_id": raw_response_id,
                "record_position": res.position,
                "source_record_id": res.source_record_id,
                "business_key": res.business_key(entity),
                "outcome": outcome,
            }
            for res, outcome in outcomes
        ],
    )
