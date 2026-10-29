"""Raw response archive (PostgreSQL JSONB).

Every HTTP attempt - including 429/5xx/timeouts/malformed bodies - is written
in its *own short transaction* before the page is processed, so the evidence
survives even if processing then fails and rolls back. The database row is the
authoritative copy; there is no separate file archive to reconcile.

Authorization headers are never stored: the client passes only the sanitized
endpoint and query parameters.
"""

from __future__ import annotations

from sqlalchemy import Engine, insert

from .api_client import AttemptRecord
from .models import raw_responses


def make_archiver(engine: Engine, run_id: str):
    def archive(record: AttemptRecord) -> int:
        with engine.begin() as conn:
            return conn.execute(
                insert(raw_responses)
                .values(
                    run_id=run_id,
                    entity=record.entity,
                    endpoint=record.endpoint,
                    request_params=record.params,
                    page_number=record.page_number,
                    attempt=record.attempt,
                    outcome=record.outcome,
                    status_code=record.status_code,
                    retrieved_at=record.retrieved_at,
                    elapsed_ms=record.elapsed_ms,
                    request_id=record.request_id,
                    body_sha256=record.body_sha256,
                    body=record.body,
                    body_text=record.body_text,
                    record_count=record.record_count,
                    error=record.error,
                )
                .returning(raw_responses.c.raw_response_id)
            ).scalar_one()

    return archive
