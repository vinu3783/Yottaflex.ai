"""Database schema (SQLAlchemy Core).

Two groups of tables:

* Control / audit: ``pipeline_runs``, ``run_entity_stats``, ``checkpoints``,
  ``raw_responses``, ``record_outcomes``, ``quarantine``.
* Target (analytical): ``orders`` (latest state per order) and ``order_events``
  (append-only, one row per distinct event).

The Alembic migration in ``migrations/versions`` creates exactly these objects;
``tests/integration/test_migrations.py`` checks the two stay in sync.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

ORDER_STATUSES = ("pending", "paid", "shipped", "delivered", "cancelled", "refunded")
EVENT_TYPES = (
    "order_created",
    "payment_authorized",
    "order_shipped",
    "order_delivered",
    "order_cancelled",
    "order_refunded",
)
CURRENCIES = ("USD", "EUR", "GBP", "INR")
RUN_STATUSES = ("running", "succeeded", "failed", "abandoned")
OUTCOMES = ("inserted", "updated", "duplicate", "stale", "quarantined")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


metadata = MetaData(
    naming_convention={
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "ix": "ix_%(table_name)s_%(column_0_name)s",
    }
)

TS = DateTime(timezone=True)

pipeline_runs = Table(
    "pipeline_runs",
    metadata,
    Column("run_id", UUID(as_uuid=False), primary_key=True),
    Column("pipeline_name", String(100), nullable=False),
    Column("pipeline_version", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("mode", String(20), nullable=False),  # incremental | backfill
    Column("source", Text, nullable=False),
    Column("target", Text, nullable=False),
    Column("params", JSONB, nullable=False),
    Column("started_at", TS, nullable=False),
    Column("finished_at", TS),
    Column("duration_seconds", Numeric(12, 3)),
    Column("checkpoint_before", JSONB),
    Column("checkpoint_after", JSONB),
    Column("http_attempts", Integer, nullable=False, server_default="0"),
    Column("http_retries", Integer, nullable=False, server_default="0"),
    Column("http_rate_limited", Integer, nullable=False, server_default="0"),
    Column("http_server_errors", Integer, nullable=False, server_default="0"),
    Column("http_timeouts", Integer, nullable=False, server_default="0"),
    Column("failure_category", String(50)),
    Column("error_message", Text),
    CheckConstraint(_in("status", RUN_STATUSES), name="status"),
    CheckConstraint(_in("mode", ("incremental", "backfill")), name="mode"),
    Index("ix_pipeline_runs_started_at", "started_at"),
)

run_entity_stats = Table(
    "run_entity_stats",
    metadata,
    Column("run_id", UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False),
    Column("entity", String(30), nullable=False),
    Column("pages_requested", Integer, nullable=False, server_default="0"),
    Column("pages_processed", Integer, nullable=False, server_default="0"),
    Column("records_fetched", Integer, nullable=False, server_default="0"),
    Column("records_valid", Integer, nullable=False, server_default="0"),
    Column("records_inserted", Integer, nullable=False, server_default="0"),
    Column("records_updated", Integer, nullable=False, server_default="0"),
    Column("records_duplicate", Integer, nullable=False, server_default="0"),
    Column("records_stale", Integer, nullable=False, server_default="0"),
    Column("records_quarantined", Integer, nullable=False, server_default="0"),
    Column("unknown_fields", JSONB, nullable=False, server_default="{}"),
    Column("start_cursor", JSONB),
    Column("end_cursor", JSONB),
    PrimaryKeyConstraint("run_id", "entity"),
)

checkpoints = Table(
    "checkpoints",
    metadata,
    Column("pipeline_name", String(100), nullable=False),
    Column("entity", String(30), nullable=False),
    Column("cursor_updated_at", TS, nullable=False),
    # "C" collation = byte order, matching the source's keyset ordering exactly
    Column("cursor_record_id", String(64, collation="C"), nullable=False),
    Column("last_run_id", UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False),
    Column("updated_at", TS, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("pipeline_name", "entity"),
)

raw_responses = Table(
    "raw_responses",
    metadata,
    Column("raw_response_id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False),
    Column("entity", String(30), nullable=False),
    Column("endpoint", Text, nullable=False),
    Column("request_params", JSONB, nullable=False),
    Column("page_number", Integer, nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("outcome", String(30), nullable=False),
    Column("status_code", Integer),
    Column("retrieved_at", TS, nullable=False),
    Column("elapsed_ms", Integer),
    Column("request_id", String(100)),
    Column("body_sha256", String(64)),
    Column("body", JSONB),
    Column("body_text", Text),
    Column("record_count", Integer),
    Column("error", Text),
    CheckConstraint(
        _in("outcome", ("ok", "http_error", "malformed", "timeout", "transport_error")),
        name="outcome",
    ),
    Index("ix_raw_responses_run_id", "run_id", "entity", "page_number"),
)

orders = Table(
    "orders",
    metadata,
    Column("order_id", String(32), primary_key=True),
    Column("customer_id", String(32), nullable=False),
    Column("order_status", String(20), nullable=False),
    Column("order_amount", Numeric(12, 2), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("created_at", TS, nullable=False),
    Column("updated_at", TS, nullable=False),
    Column("source_updated_at", TS, nullable=False),
    Column("source_record_id", String(64), nullable=False),
    Column("record_hash", String(64), nullable=False),
    Column("first_ingested_at", TS, nullable=False),
    Column("last_ingested_at", TS, nullable=False),
    Column("first_run_id", UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False),
    Column("last_run_id", UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False),
    Column(
        "raw_response_id", BigInteger, ForeignKey("raw_responses.raw_response_id"), nullable=False
    ),
    CheckConstraint(_in("order_status", ORDER_STATUSES), name="order_status"),
    CheckConstraint(_in("currency", CURRENCIES), name="currency"),
    CheckConstraint("order_amount >= 0", name="order_amount_non_negative"),
    CheckConstraint("updated_at >= created_at", name="updated_after_created"),
    Index("ix_orders_customer_id", "customer_id"),
    Index("ix_orders_source_updated_at", "source_updated_at"),
)

order_events = Table(
    "order_events",
    metadata,
    Column("event_id", String(40), primary_key=True),
    Column("order_id", String(32), ForeignKey("orders.order_id"), nullable=False),
    Column("event_type", String(30), nullable=False),
    Column("event_timestamp", TS, nullable=False),
    Column("source_updated_at", TS, nullable=False),
    Column("source_record_id", String(64), nullable=False),
    Column("record_hash", String(64), nullable=False),
    Column("ingested_at", TS, nullable=False),
    Column("run_id", UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False),
    Column(
        "raw_response_id", BigInteger, ForeignKey("raw_responses.raw_response_id"), nullable=False
    ),
    CheckConstraint(_in("event_type", EVENT_TYPES), name="event_type"),
    Index("ix_order_events_order_id", "order_id", "event_timestamp"),
)

quarantine = Table(
    "quarantine",
    metadata,
    Column("quarantine_id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False),
    Column("entity", String(30), nullable=False),
    Column(
        "raw_response_id", BigInteger, ForeignKey("raw_responses.raw_response_id"), nullable=False
    ),
    Column("record_position", Integer, nullable=False),
    Column("source_record_id", String(64)),
    Column("business_key", String(64)),
    Column("payload", JSONB, nullable=False),
    Column("error_codes", ARRAY(Text), nullable=False),
    Column("errors", JSONB, nullable=False),
    Column("rejected_at", TS, nullable=False),
    Column("resolved_at", TS),
    Column("resolved_by_run_id", UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id")),
    Index("ix_quarantine_run_id", "run_id", "entity"),
    Index("ix_quarantine_source_record_id", "source_record_id"),
)

record_outcomes = Table(
    "record_outcomes",
    metadata,
    Column("outcome_id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False),
    Column("entity", String(30), nullable=False),
    Column(
        "raw_response_id", BigInteger, ForeignKey("raw_responses.raw_response_id"), nullable=False
    ),
    Column("record_position", Integer, nullable=False),
    Column("source_record_id", String(64)),
    Column("business_key", String(64)),
    Column("outcome", String(20), nullable=False),
    CheckConstraint(_in("outcome", OUTCOMES), name="outcome"),
    UniqueConstraint("raw_response_id", "record_position", name="uq_record_outcomes_position"),
    Index("ix_record_outcomes_run_id", "run_id", "entity", "outcome"),
    Index("ix_record_outcomes_source_record_id", "source_record_id"),
)

ALL_TABLES = [
    pipeline_runs,
    run_entity_stats,
    checkpoints,
    raw_responses,
    orders,
    order_events,
    quarantine,
    record_outcomes,
]
