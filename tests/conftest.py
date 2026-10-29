"""Shared fixtures.

Integration tests use a real, disposable PostgreSQL database
(``TEST_DATABASE_URL``, default ``postgresql+psycopg://pipeline:pipeline@localhost:5432/pipeline_test``).
The schema is rebuilt from the Alembic migration once per session and every
table is truncated before each test, so tests never depend on leftover state
and the suite can be run repeatedly.

The mock API runs in-process through Starlette's TestClient (an ``httpx.Client``
subclass), so no ports, no network and no real credentials are needed.
"""

from __future__ import annotations

import os
import warnings
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

warnings.filterwarnings("ignore", message=".*httpx2.*")  # starlette TestClient notice

from fastapi.testclient import TestClient  # noqa: E402

from ingestion_pipeline.config import PipelineSettings  # noqa: E402
from ingestion_pipeline.datagen import Dataset, generate  # noqa: E402
from ingestion_pipeline.db import make_engine, run_migrations  # noqa: E402
from ingestion_pipeline.mock_api import MockSettings, create_app  # noqa: E402
from ingestion_pipeline.models import ALL_TABLES  # noqa: E402
from ingestion_pipeline.pipeline import Hooks, Pipeline  # noqa: E402
from ingestion_pipeline.retry_policy import RetryPolicy  # noqa: E402

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://pipeline:pipeline@localhost:5432/pipeline_test"
)
TOKEN = "test-token"


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "/integration/" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


# ------------------------------------------------------------------ database
@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    eng = make_engine(TEST_DATABASE_URL)
    try:
        with eng.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL not reachable at TEST_DATABASE_URL: {exc.orig}")
    run_migrations(TEST_DATABASE_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Engine:
    names = ", ".join(t.name for t in ALL_TABLES)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    return engine


def scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def rows(engine: Engine, sql: str, **params: Any) -> list[Any]:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).all()


# ------------------------------------------------------------------ mock API
@pytest.fixture(scope="session")
def review_dataset() -> Dataset:
    return generate(42, 200)


def make_mock(dataset: Dataset, *, phase: int = 1, **settings: Any) -> TestClient:
    app = create_app(
        MockSettings(api_token=TOKEN, release_phase=phase, **settings),
        feeds={"orders": dataset.orders, "order_events": dataset.order_events},
    )
    return TestClient(app)


def set_faults(client: TestClient, rules: list[dict[str, Any]]) -> None:
    client.post("/_admin/faults", json=rules).raise_for_status()


def release_phase(client: TestClient, phase: int) -> None:
    client.post("/_admin/release-phase", json={"phase": phase}).raise_for_status()


# ------------------------------------------------------------------ pipeline
class FakeSleep:
    """Records requested sleeps instead of sleeping, so retry tests are instant."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_settings(**overrides: Any) -> PipelineSettings:
    values: dict[str, Any] = {
        "database_url": SecretStr(TEST_DATABASE_URL),
        "api_base_url": "http://testserver",
        "api_token": SecretStr(TOKEN),
        "page_size": 50,
        "requests_per_second": 0,
        "max_attempts": 4,
        "backoff_base_seconds": 0.01,
        "backoff_max_seconds": 0.05,
        "lookback_seconds": 3600,
    }
    values.update(overrides)
    return PipelineSettings(_env_file=None, **values)  # never read a developer's .env


@pytest.fixture
def make_pipeline(db: Engine) -> Callable[..., tuple[Pipeline, FakeSleep]]:
    def factory(client, *, hooks: Hooks | None = None, **overrides: Any):
        sleep = FakeSleep()
        settings = make_settings(**overrides)
        policy = RetryPolicy(
            max_attempts=settings.max_attempts,
            base_delay=settings.backoff_base_seconds,
            max_delay=settings.backoff_max_seconds,
            max_retry_after=settings.max_retry_after_seconds,
        )
        return (
            Pipeline(settings, db, http=client, retry_policy=policy, sleep=sleep, hooks=hooks),
            sleep,
        )

    return factory
