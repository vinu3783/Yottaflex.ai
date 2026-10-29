"""Mock source API.

Contract (documented in docs/ARCHITECTURE.md, "Source API contract"):

``GET /v1/{entity}?limit=N[&after_updated_at=TS&after_id=ID | &updated_since=TS]``
  with ``Authorization: Bearer <token>``.

* Rows are ordered by ``(source_updated_at, source_record_id)`` - a *keyset*
  ordering that is stable and unique, so ties on the timestamp are safe.
* ``after_updated_at`` + ``after_id`` = exclusive keyset cursor (continue after
  that row). ``updated_since`` = inclusive start used for incremental / backfill
  scans. Without either, the scan starts at the beginning.
* Response: ``{"data": [...], "has_more": bool, "next_cursor": {...} | null}``.
  ``next_cursor`` points at the last row returned (or echoes the request
  cursor for an empty page) so clients can resume after it later.
"""

from __future__ import annotations

import bisect
import json
import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..datagen import FeedRow, generate, load_feed
from ..transformations import iso
from .faults import FaultRule

ENTITIES = ("orders", "order_events")


class MockSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MOCK_", env_file=".env", extra="ignore")

    api_token: str = "dev-token"  # noqa: S105 - local mock default, override via MOCK_API_TOKEN
    dataset_dir: str | None = None
    seed: int = 42
    order_count: int = 200
    release_phase: int = 1
    rate_limit_rps: float = 0.0  # 0 = disabled
    enable_admin: bool = True
    max_page_size: int = 1000


class TokenBucket:
    """Classic token bucket: ``rate`` tokens/second, burst capacity ``rate``."""

    def __init__(self, rate: float, clock=time.monotonic) -> None:
        self.rate = rate
        self.capacity = max(1.0, rate)
        self.tokens = self.capacity
        self.clock = clock
        self.updated = clock()

    def try_acquire(self) -> float:
        """Return 0 if a token was taken, else seconds until one is available."""
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return 0.0
        return (1 - self.tokens) / self.rate


@dataclass
class FeedIndex:
    rows: list[FeedRow]
    keys: list[tuple[datetime, str]] = field(default_factory=list)

    @classmethod
    def build(cls, rows: list[FeedRow], phase: int) -> FeedIndex:
        visible = sorted(
            (r for r in rows if r.release_phase <= phase),
            key=lambda r: (r.source_updated_at, r.source_record_id),
        )
        return cls(visible, [(r.source_updated_at, r.source_record_id) for r in visible])


class MockState:
    def __init__(self, feeds: dict[str, list[FeedRow]], settings: MockSettings) -> None:
        self.settings = settings
        self.feeds = feeds
        self.lock = threading.Lock()
        self.phase = settings.release_phase
        self.indexes = {e: FeedIndex.build(feeds.get(e, []), self.phase) for e in ENTITIES}
        self.faults: list[FaultRule] = []
        self.stats: dict[str, int] = {}
        self.request_seq = 0
        self.bucket = TokenBucket(settings.rate_limit_rps) if settings.rate_limit_rps > 0 else None

    def set_phase(self, phase: int) -> None:
        with self.lock:
            self.phase = phase
            self.indexes = {e: FeedIndex.build(self.feeds.get(e, []), phase) for e in ENTITIES}

    def count(self, key: str) -> None:
        self.stats[key] = self.stats.get(key, 0) + 1


def _parse_ts(value: str, name: str) -> datetime:
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"{name} must be ISO-8601") from None
    if ts.tzinfo is None:
        raise HTTPException(400, f"{name} must include a time zone")
    return ts.astimezone(UTC)


class PhaseBody(BaseModel):
    phase: int = Field(ge=1)


def load_feeds(settings: MockSettings) -> dict[str, list[FeedRow]]:
    if settings.dataset_dir:
        return load_feed(Path(settings.dataset_dir) / "source")
    ds = generate(settings.seed, settings.order_count, edge_cases=settings.order_count >= 50)
    return {"orders": ds.orders, "order_events": ds.order_events}


def create_app(
    settings: MockSettings | None = None, feeds: dict[str, list[FeedRow]] | None = None
) -> FastAPI:
    settings = settings or MockSettings()
    state = MockState(feeds if feeds is not None else load_feeds(settings), settings)
    app = FastAPI(title="Mock Orders API", version="1.0.0")
    app.state.mock = state

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        with state.lock:
            state.request_seq += 1
            rid = f"req-{state.request_seq:08d}"
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "phase": state.phase,
            "rows": {e: len(state.indexes[e].rows) for e in ENTITIES},
        }

    @app.get("/v1/{entity}")
    def list_records(
        entity: str,
        limit: Annotated[int, Query(ge=1)] = 100,
        after_updated_at: str | None = None,
        after_id: str | None = None,
        updated_since: str | None = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        if entity not in ENTITIES:
            raise HTTPException(404, f"unknown entity {entity!r}")
        expected = f"Bearer {settings.api_token}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            state.count(f"{entity}:401")
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if state.bucket is not None:
            with state.lock:
                wait = state.bucket.try_acquire()
            if wait > 0:
                state.count(f"{entity}:429")
                return JSONResponse(
                    {"error": "rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(max(1, math.ceil(wait)))},
                )
        limit = min(limit, settings.max_page_size)
        if (after_updated_at is None) != (after_id is None):
            raise HTTPException(400, "after_updated_at and after_id must be used together")
        if after_updated_at is not None and updated_since is not None:
            raise HTTPException(400, "use either the keyset cursor or updated_since")

        with state.lock:
            index = state.indexes[entity]
            if after_updated_at is not None:
                key = (_parse_ts(after_updated_at, "after_updated_at"), after_id or "")
                start = bisect.bisect_right(index.keys, key)
            elif updated_since is not None:
                since = _parse_ts(updated_since, "updated_since")
                start = bisect.bisect_left(index.keys, (since, ""))
            else:
                start = 0
            # Page number used only to target fault rules: exact for scans that
            # start at the beginning of the feed, approximate otherwise.
            page = start // limit + 1
            rule = next((r for r in state.faults if r.matches(entity, page)), None)
            if rule is not None:
                rule.consume()
            rows = index.rows[start : start + limit]
            has_more = start + limit < len(index.rows)

        if rule is not None:
            state.count(f"{entity}:fault:{rule.kind}")
            if rule.kind == "status":
                headers = {}
                if rule.retry_after is not None:
                    headers["Retry-After"] = str(rule.retry_after)
                return JSONResponse(
                    {"error": f"injected {rule.status_code}"},
                    status_code=rule.status_code,
                    headers=headers,
                )
            if rule.kind == "malformed":
                return PlainTextResponse('{"data": [{"order_id": "ORD-0', status_code=200)
            if rule.kind == "invalid_envelope":
                return JSONResponse({"items": [], "more": True}, status_code=200)
            if rule.kind == "stuck_cursor":
                cursor = (
                    {"after_updated_at": after_updated_at, "after_id": after_id}
                    if after_updated_at
                    else None
                )
                return JSONResponse({"data": [], "has_more": True, "next_cursor": cursor})
            if rule.kind == "delay":
                time.sleep(rule.delay_seconds)

        data: list[Any] = [r.payload for r in rows]
        if rule is not None and rule.kind == "drop_field" and rule.field:
            data = [
                {k: v for k, v in p.items() if k != rule.field} if isinstance(p, dict) else p
                for p in data
            ]
        if rows:
            last = rows[-1]
            next_cursor = {
                "after_updated_at": iso(last.source_updated_at),
                "after_id": last.source_record_id,
            }
        elif after_updated_at is not None:
            next_cursor = {"after_updated_at": after_updated_at, "after_id": after_id}
        else:
            next_cursor = None
        state.count(f"{entity}:200")
        body = json.dumps({"data": data, "has_more": has_more, "next_cursor": next_cursor})
        return Response(body, media_type="application/json")

    if settings.enable_admin:

        @app.get("/_admin/faults")
        def get_faults() -> list[FaultRule]:
            return state.faults

        @app.post("/_admin/faults")
        def set_faults(rules: list[FaultRule]) -> dict[str, int]:
            with state.lock:
                state.faults = list(rules)
            return {"rules": len(rules)}

        @app.delete("/_admin/faults")
        def clear_faults() -> dict[str, int]:
            with state.lock:
                state.faults = []
            return {"rules": 0}

        @app.post("/_admin/release-phase")
        def release_phase(body: PhaseBody) -> dict[str, Any]:
            state.set_phase(body.phase)
            return {"phase": state.phase, "rows": {e: len(state.indexes[e].rows) for e in ENTITIES}}

        @app.get("/_admin/stats")
        def stats() -> dict[str, int]:
            return dict(sorted(state.stats.items()))

    return app


def create_app_from_env() -> FastAPI:
    """Factory for ``uvicorn --factory``."""
    return create_app(MockSettings())
