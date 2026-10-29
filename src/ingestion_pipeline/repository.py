"""Idempotent publishing to the target tables.

Every *valid* record gets exactly one outcome:

==============  =============================================================
inserted        business key not seen before -> new target row
updated         newer version (greater ``source_updated_at``) -> row replaced
duplicate       same key, same version, same content (re-delivery / replay)
stale           older version than the target already holds -> ignored
quarantined     same key + same version but DIFFERENT content (conflict), or
                an event whose ``order_id`` does not exist (referential check)
==============  =============================================================

Orders keep the latest state only (history lives in ``raw_responses`` and the
``record_outcomes`` ledger). Events are immutable facts and are append-only.

Classification happens in Python against the current target state (read with
``FOR UPDATE`` inside the page transaction), then one batched upsert writes the
final state per key. The upsert's ``WHERE`` clause repeats the "newer wins"
rule in SQL as a second line of defence: even if two writers raced, an older
version could never overwrite a newer one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .models import order_events, orders
from .schemas import OrderEventRecord, OrderRecord
from .validation import Issue, ValidationResult


@dataclass
class PublishResult:
    outcomes: list[tuple[ValidationResult, str]] = field(default_factory=list)
    rejections: list[tuple[ValidationResult, list[Issue]]] = field(default_factory=list)
    rows_written: int = 0


@dataclass
class PublishContext:
    run_id: str
    raw_response_id: int
    now: datetime
    write_mode: str = "batch"  # "batch" (one statement per page) or "row" (per record)


def _conflict_issue(entity: str, key: str) -> Issue:
    return Issue(
        "CONFLICTING_DUPLICATE",
        "order_id" if entity == "orders" else "event_id",
        f"{key}: same version already published with different content",
    )


# ---------------------------------------------------------------- orders
def _order_row(rec: OrderRecord, ctx: PublishContext) -> dict:
    return {
        "order_id": rec.order_id,
        "customer_id": rec.customer_id,
        "order_status": rec.order_status,
        "order_amount": rec.order_amount,
        "currency": rec.currency,
        "created_at": rec.created_at,
        "updated_at": rec.updated_at,
        "source_updated_at": rec.source_updated_at,
        "source_record_id": rec.source_record_id,
        "record_hash": rec.content_hash(),
        "first_ingested_at": ctx.now,
        "last_ingested_at": ctx.now,
        "first_run_id": ctx.run_id,
        "last_run_id": ctx.run_id,
        "raw_response_id": ctx.raw_response_id,
    }


def _upsert_orders(conn: Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(orders).values(rows)
    ex = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=[orders.c.order_id],
        set_={
            "customer_id": ex.customer_id,
            "order_status": ex.order_status,
            "order_amount": ex.order_amount,
            "currency": ex.currency,
            "created_at": ex.created_at,
            "updated_at": ex.updated_at,
            "source_updated_at": ex.source_updated_at,
            "source_record_id": ex.source_record_id,
            "record_hash": ex.record_hash,
            "last_ingested_at": ex.last_ingested_at,
            "last_run_id": ex.last_run_id,
            "raw_response_id": ex.raw_response_id,
        },
        # newer version wins - never let an older replay overwrite newer state
        where=orders.c.source_updated_at < ex.source_updated_at,
    )
    # Count via RETURNING: with multi-row VALUES SQLAlchemy may batch the statement
    # ("insertmanyvalues"), and then cursor.rowcount is -1, not the rows affected.
    # A row skipped by the WHERE guard returns nothing.
    return len(conn.execute(stmt.returning(orders.c.order_id)).all())


def _existing_orders(conn: Connection, keys: list[str]) -> dict[str, tuple[datetime, str]]:
    if not keys:
        return {}
    rows = conn.execute(
        select(orders.c.order_id, orders.c.source_updated_at, orders.c.record_hash)
        .where(orders.c.order_id.in_(keys))
        .with_for_update()
    )
    return {r.order_id: (r.source_updated_at, r.record_hash) for r in rows}


def publish_orders(
    conn: Connection, results: list[ValidationResult], ctx: PublishContext
) -> PublishResult:
    out = PublishResult()
    if ctx.write_mode == "row":
        for res in results:
            _publish_orders_batch(conn, [res], ctx, out)
    else:
        _publish_orders_batch(conn, results, ctx, out)
    return out


def _publish_orders_batch(
    conn: Connection, results: list[ValidationResult], ctx: PublishContext, out: PublishResult
) -> None:
    keys = sorted({r.record.business_key for r in results})  # sorted -> stable lock order
    state = _existing_orders(conn, keys)
    final: dict[str, dict] = {}
    for res in results:
        rec = res.record
        assert isinstance(rec, OrderRecord)
        h = rec.content_hash()
        current = state.get(rec.order_id)
        if current is None:
            outcome = "inserted"
        elif rec.source_updated_at > current[0]:
            outcome = "updated"
        elif rec.source_updated_at == current[0]:
            if h == current[1]:
                outcome = "duplicate"
            else:
                out.rejections.append((res, [_conflict_issue("orders", rec.order_id)]))
                continue
        else:
            outcome = "stale"
        if outcome in ("inserted", "updated"):
            state[rec.order_id] = (rec.source_updated_at, h)
            final[rec.order_id] = _order_row(rec, ctx)
        out.outcomes.append((res, outcome))
    out.rows_written += _upsert_orders(conn, list(final.values()))


# ---------------------------------------------------------------- events
def publish_events(
    conn: Connection, results: list[ValidationResult], ctx: PublishContext
) -> PublishResult:
    out = PublishResult()
    if ctx.write_mode == "row":
        for res in results:
            _publish_events_batch(conn, [res], ctx, out)
    else:
        _publish_events_batch(conn, results, ctx, out)
    return out


def _publish_events_batch(
    conn: Connection, results: list[ValidationResult], ctx: PublishContext, out: PublishResult
) -> None:
    event_ids = sorted({r.record.business_key for r in results})
    order_ids = sorted({r.record.order_id for r in results})  # type: ignore[union-attr]
    existing = (
        {
            r.event_id: r.record_hash
            for r in conn.execute(
                select(order_events.c.event_id, order_events.c.record_hash).where(
                    order_events.c.event_id.in_(event_ids)
                )
            )
        }
        if event_ids
        else {}
    )
    known_orders = (
        set(
            conn.execute(
                select(orders.c.order_id).where(orders.c.order_id.in_(order_ids))
            ).scalars()
        )
        if order_ids
        else set()
    )
    new_rows: list[dict] = []
    for res in results:
        rec = res.record
        assert isinstance(rec, OrderEventRecord)
        if rec.order_id not in known_orders:
            out.rejections.append(
                (
                    res,
                    [
                        Issue(
                            "UNKNOWN_ORDER_ID",
                            "order_id",
                            f"order {rec.order_id} does not exist in the orders table",
                        )
                    ],
                )
            )
            continue
        h = rec.content_hash()
        current = existing.get(rec.event_id)
        if current is None:
            existing[rec.event_id] = h
            new_rows.append(
                {
                    "event_id": rec.event_id,
                    "order_id": rec.order_id,
                    "event_type": rec.event_type,
                    "event_timestamp": rec.event_timestamp,
                    "source_updated_at": rec.source_updated_at,
                    "source_record_id": rec.source_record_id,
                    "record_hash": h,
                    "ingested_at": ctx.now,
                    "run_id": ctx.run_id,
                    "raw_response_id": ctx.raw_response_id,
                }
            )
            out.outcomes.append((res, "inserted"))
        elif current == h:
            out.outcomes.append((res, "duplicate"))
        else:
            out.rejections.append((res, [_conflict_issue("order_events", rec.event_id)]))
    if new_rows:
        stmt = (
            pg_insert(order_events)
            .values(new_rows)
            .on_conflict_do_nothing(index_elements=[order_events.c.event_id])
        )
        out.rows_written += len(conn.execute(stmt.returning(order_events.c.event_id)).all())


PUBLISHERS = {"orders": publish_orders, "order_events": publish_events}
