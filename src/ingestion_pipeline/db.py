"""Database engine, migrations and the single-runner lock."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, text

from .errors import ConcurrentRunError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def make_engine(database_url: str, **kwargs) -> Engine:
    # pool_pre_ping detects connections killed by a database restart.
    # Sessions run in UTC so timestamps read back are UTC regardless of server/host TZ.
    connect_args = kwargs.pop("connect_args", {})
    connect_args.setdefault("options", "-c timezone=UTC")
    return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args, **kwargs)


def run_migrations(database_url: str, revision: str = "head") -> None:
    """Apply Alembic migrations programmatically (used by the CLI and tests)."""
    from alembic import command
    from alembic.config import Config

    # macOS writes AppleDouble "._name.py" companions on exFAT/FAT drives; Alembic
    # would try to import them as revisions. They are never real migrations.
    for stray in (PROJECT_ROOT / "migrations" / "versions").glob("._*"):
        stray.unlink(missing_ok=True)
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.attributes["database_url"] = database_url
    command.upgrade(cfg, revision)


def _lock_key(name: str) -> int:
    # pg advisory locks take a signed 64-bit key; derive a stable one from the name.
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)


@contextmanager
def pipeline_lock(engine: Engine, pipeline_name: str) -> Iterator[Connection]:
    """Hold a session-level advisory lock so two runs of the same pipeline
    cannot interleave checkpoint updates. Released automatically if the
    process dies because the database session ends."""
    conn = engine.connect()
    try:
        got = conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _lock_key(pipeline_name)}
        ).scalar_one()
        conn.commit()
        if not got:
            raise ConcurrentRunError(f"another run of '{pipeline_name}' holds the pipeline lock")
        try:
            yield conn
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _lock_key(pipeline_name)})
            conn.commit()
    finally:
        conn.close()
