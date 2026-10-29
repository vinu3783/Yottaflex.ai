"""Pipeline orchestration.

    for each entity (orders first, then order_events):
        start position = --since | checkpoint - lookback | beginning
        loop over pages:
            fetch page (retries, throttling)  -> every attempt archived (own txn)
            breaking-schema-change check      -> fail run, nothing published
            BEGIN
                validate + transform records
                publish valid records (idempotent upsert)
                quarantine invalid / conflicting records
                write record_outcomes ledger
                advance checkpoint (monotonic)
                save run counters
            COMMIT                              <- the only "unit of progress"

A crash anywhere before COMMIT loses nothing: the next run re-reads that page
and idempotent publishing absorbs the replay (at-least-once delivery +
idempotent writes = effectively-once results in the target tables).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from . import PIPELINE_NAME
from . import checkpoint as ckpt
from .api_client import ApiClient, HttpStats, sanitize_base_url
from .config import ENTITIES, PipelineSettings
from .db import pipeline_lock
from .errors import MalformedResponseError, NonAdvancingCursorError, PipelineError
from .logging_config import entity_var, run_id_var
from .metrics import EntityStats, RunTracker, mark_abandoned_runs
from .quarantine import insert_outcomes, insert_quarantine, resolve_quarantine
from .rate_limiter import RateLimiter
from .raw_archive import make_archiver
from .repository import PUBLISHERS, PublishContext
from .retry_policy import RetryPolicy
from .transformations import iso
from .validation import check_breaking_change, validate_record

log = logging.getLogger(__name__)

PageHook = Callable[[str, int], None]


@dataclass
class Hooks:
    """Test seams used to simulate crashes at precise points."""

    before_commit: PageHook | None = None  # inside the page transaction
    after_commit: PageHook | None = None


@dataclass
class RunResult:
    run_id: str
    status: str
    duration_seconds: float
    entity_stats: dict[str, EntityStats] = field(default_factory=dict)
    http: HttpStats = field(default_factory=HttpStats)
    failure_category: str | None = None
    error_message: str | None = None
    stopped_early: bool = False


class Pipeline:
    def __init__(
        self,
        settings: PipelineSettings,
        engine: Engine,
        *,
        http: httpx.Client | None = None,
        retry_policy: RetryPolicy | None = None,
        rate_limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        hooks: Hooks | None = None,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self._http = http
        self._retry_policy = retry_policy
        self._rate_limiter = rate_limiter
        self._sleep = sleep
        self.hooks = hooks or Hooks()

    # ------------------------------------------------------------ public
    def run(
        self,
        entities: Sequence[str] = ENTITIES,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        max_pages: int | None = None,
    ) -> RunResult:
        unknown = set(entities) - set(ENTITIES)
        if unknown:
            raise PipelineError(f"unknown entities: {sorted(unknown)}")
        entities = [e for e in ENTITIES if e in entities]  # dependency order
        mode = "backfill" if since is not None else "incremental"
        params = {
            "entities": entities,
            "since": iso(since) if since else None,
            "until": iso(until) if until else None,
            "max_pages": max_pages or self.settings.max_pages_per_run,
            "page_size": self.settings.page_size,
            "lookback_seconds": self.settings.lookback_seconds,
            "write_mode": self.settings.write_mode,
        }
        target = make_url(self.settings.database_url_value()).render_as_string(hide_password=True)

        with pipeline_lock(self.engine, PIPELINE_NAME):
            abandoned = mark_abandoned_runs(self.engine)
            if abandoned:
                log.warning("marked runs abandoned", extra={"count": abandoned})
            with self.engine.connect() as conn:
                before = ckpt.load_all(conn, PIPELINE_NAME)
            tracker = RunTracker(
                self.engine, source=sanitize_base_url(self.settings.api_base_url), target=target
            )
            run_id = tracker.start(mode, params, before)
            token = run_id_var.set(run_id)
            client = ApiClient(
                self.settings,
                http=self._http,
                retry_policy=self._retry_policy,
                rate_limiter=self._rate_limiter,
                archive=make_archiver(self.engine, run_id),
                sleep=self._sleep,
            )
            result = RunResult(run_id=run_id, status="running", duration_seconds=0.0)
            log.info("run started", extra={"mode": mode, "params": params})
            try:
                budget = params["max_pages"]
                for entity in entities:
                    stats = EntityStats()
                    result.entity_stats[entity] = stats
                    pages_used, stopped = self._ingest_entity(
                        entity, client, tracker, stats, since=since, until=until, budget=budget
                    )
                    budget -= pages_used
                    if stopped:
                        result.stopped_early = True
                        break
            except PipelineError as exc:
                result.status = "failed"
                result.failure_category = exc.category
                result.error_message = str(exc)
                log.error("run failed", extra={"failure_category": exc.category, "error": str(exc)})
            except Exception as exc:  # database outage or programming error: record, re-raise
                result.status = "failed"
                result.failure_category = (
                    "database_error" if isinstance(exc, DBAPIError) else "unexpected_error"
                )
                result.error_message = f"{type(exc).__name__}: {exc}"
                log.exception("run aborted", extra={"failure_category": result.failure_category})
                try:
                    self._finish(tracker, client, result)
                except DBAPIError:
                    log.error("could not record run failure (database unreachable)")
                client.close()
                run_id_var.reset(token)
                raise
            else:
                result.status = "succeeded"
            # SimulatedCrash (a BaseException) deliberately skips everything below,
            # leaving the run 'running' exactly like a killed process would.
            self._finish(tracker, client, result)
            client.close()
            log.info(
                "run finished",
                extra={
                    "status": result.status,
                    "duration_seconds": round(result.duration_seconds, 3),
                },
            )
            run_id_var.reset(token)
            return result

    # ------------------------------------------------------------ internals
    def _finish(self, tracker: RunTracker, client: ApiClient, result: RunResult) -> None:
        with self.engine.connect() as conn:
            after = ckpt.load_all(conn, PIPELINE_NAME)
        result.http = client.stats
        result.duration_seconds = tracker.finish(
            result.status,
            client.stats,
            after,
            failure_category=result.failure_category,
            error_message=result.error_message,
        )

    def _start_position(
        self, entity: str, since: datetime | None
    ) -> tuple[dict[str, str], ckpt.Cursor | None, bool]:
        """Returns (request params, cursor the scan starts after, may_advance_checkpoint)."""
        with self.engine.connect() as conn:
            current = ckpt.load(conn, PIPELINE_NAME, entity)
        if since is not None:
            # A backfill may only move the checkpoint if it is contiguous with it;
            # otherwise rows between the checkpoint and `since` were never read.
            contiguous = current is not None and since <= current.updated_at
            return {"updated_since": iso(since)}, ckpt.Cursor(since, ""), contiguous
        params = ckpt.start_params(current, self.settings.lookback_seconds)
        if "updated_since" in params:
            start = ckpt.Cursor(datetime.fromisoformat(params["updated_since"]), "")
        else:
            start = current
        return params, start, True

    def _ingest_entity(
        self,
        entity: str,
        client: ApiClient,
        tracker: RunTracker,
        stats: EntityStats,
        *,
        since: datetime | None,
        until: datetime | None,
        budget: int,
    ) -> tuple[int, bool]:
        etoken = entity_var.set(entity)
        try:
            params, prev_cursor, may_advance = self._start_position(entity, since)
            stats.start_cursor = {"params": params}
            log.info("entity scan started", extra={"start_params": params})
            page_number = 0
            while True:
                if page_number >= budget:
                    log.warning(
                        "page budget exhausted; stopping early", extra={"pages": page_number}
                    )
                    return page_number, True
                page_number += 1
                stats.pages_requested += 1
                page = client.fetch_page(
                    entity, {**params, "limit": self.settings.page_size}, page_number
                )
                env = page.envelope
                check_breaking_change(
                    entity,
                    env.data,
                    min_records=self.settings.breaking_change_min_records,
                    missing_ratio=self.settings.breaking_change_missing_ratio,
                )
                next_cursor = (
                    ckpt.Cursor(env.next_cursor.after_updated_at, env.next_cursor.after_id)
                    if env.next_cursor
                    else None
                )
                self._process_page(
                    entity,
                    page.raw_response_id,
                    env.data,
                    next_cursor,
                    may_advance,
                    page_number,
                    tracker,
                    client,
                    stats,
                )
                if not env.has_more:
                    return page_number, False
                if next_cursor is None:
                    raise MalformedResponseError("has_more=true but next_cursor is missing")
                if prev_cursor is not None and next_cursor <= prev_cursor:
                    raise NonAdvancingCursorError(
                        f"cursor did not advance past {prev_cursor.as_dict()}"
                    )
                if until is not None and next_cursor.updated_at > until:
                    log.info("reached --until boundary", extra={"until": iso(until)})
                    return page_number, False
                prev_cursor = next_cursor
                params = next_cursor.as_params()
        finally:
            entity_var.reset(etoken)

    def _process_page(
        self,
        entity: str,
        raw_response_id: int,
        records: list[Any],
        next_cursor: ckpt.Cursor | None,
        may_advance: bool,
        page_number: int,
        tracker: RunTracker,
        client: ApiClient,
        stats: EntityStats,
    ) -> None:
        now = datetime.now(UTC)
        results = [validate_record(entity, i, payload) for i, payload in enumerate(records)]
        valid = [r for r in results if r.is_valid]
        invalid = [(r, r.issues) for r in results if not r.is_valid]
        for r in valid:
            stats.unknown_fields.update(r.record.unknown_fields)  # type: ignore[union-attr]
        new_unknown = {f for r in valid for f in r.record.unknown_fields}  # type: ignore[union-attr]
        if new_unknown:
            log.warning(
                "additive schema drift: unknown fields accepted",
                extra={"fields": sorted(new_unknown), "page": page_number},
            )

        ctx = PublishContext(
            run_id=tracker.run_id,
            raw_response_id=raw_response_id,
            now=now,
            write_mode=self.settings.write_mode,
        )
        with self.engine.begin() as conn:
            published = PUBLISHERS[entity](conn, valid, ctx)
            rejected = invalid + published.rejections
            insert_quarantine(
                conn,
                entity,
                rejected,
                run_id=tracker.run_id,
                raw_response_id=raw_response_id,
                now=now,
            )
            ledger = published.outcomes + [(r, "quarantined") for r, _ in rejected]
            ledger.sort(key=lambda item: item[0].position)
            insert_outcomes(
                conn, entity, ledger, run_id=tracker.run_id, raw_response_id=raw_response_id
            )
            accepted_ids = [
                r.source_record_id
                for r, o in published.outcomes
                if r.source_record_id and o in ("inserted", "updated", "duplicate")
            ]
            resolve_quarantine(conn, entity, accepted_ids, run_id=tracker.run_id, now=now)

            advanced = False
            if records and next_cursor is not None and may_advance:
                advanced = ckpt.advance(conn, PIPELINE_NAME, entity, next_cursor, tracker.run_id)

            stats.pages_processed += 1
            stats.records_fetched += len(records)
            stats.records_valid += len(valid)
            for _, outcome in ledger:
                stats.add_outcome(outcome)
            if next_cursor is not None:
                stats.end_cursor = next_cursor.as_dict()
            tracker.save_entity(conn, entity, stats)
            tracker.save_http(conn, client.stats)
            if self.hooks.before_commit:
                self.hooks.before_commit(entity, page_number)
        if self.hooks.after_commit:
            self.hooks.after_commit(entity, page_number)
        log.info(
            "page committed",
            extra={
                "page": page_number,
                "raw_response_id": raw_response_id,
                "fetched": len(records),
                "quarantined": len(rejected),
                "checkpoint_advanced": advanced,
            },
        )
