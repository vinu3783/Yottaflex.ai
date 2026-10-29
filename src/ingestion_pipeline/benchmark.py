"""Repeatable throughput benchmark.

Method
------
1. Generate (or reuse) a deterministic dataset for ``--orders`` / ``--seed``.
2. Start the mock API as a separate process serving that dataset over real HTTP
   (no rate limit, no faults, all phases released).
3. For each write mode: wipe and re-migrate the benchmark database, then run
   ``ingest run --json`` as a separate process so its peak RSS is measured on
   its own. Duration is the run's monotonic-clock duration.
4. Count the rows written and run the reconciliation + oracle checks, so a
   fast-but-wrong run cannot pass as a result.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url

from .datagen import generate, write_dataset
from .db import PROJECT_ROOT, make_engine, run_migrations
from .reporting import reconcile

TOKEN = "benchmark-token"  # noqa: S105 - throwaway token for the local mock only


def _reset_database(url: str) -> None:
    engine = make_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    engine.dispose()
    run_migrations(url)


def _wait_healthy(base: str, proc: subprocess.Popen, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("mock API exited during startup")
        try:
            if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError("mock API did not become healthy")


def run_benchmark(
    *,
    database_url: str,
    orders: int,
    seed: int,
    page_size: int,
    modes: list[str],
    port: int,
    out: Path,
    force_reset: bool = False,
) -> dict[str, Any]:
    db_name = make_url(database_url).database or ""
    if "bench" not in db_name and not force_reset:
        raise SystemExit(
            f"benchmark wipes its database; '{db_name}' does not contain 'bench'. "
            "Pass --database-url for a dedicated database or --force-reset."
        )
    dataset_dir = PROJECT_ROOT / "data" / "generated" / f"bench-{orders}-{seed}"
    if not (dataset_dir / "expected" / "manifest.json").exists():
        write_dataset(generate(seed, orders, edge_cases=orders >= 50, phases=False), dataset_dir)
    manifest = json.loads((dataset_dir / "expected" / "manifest.json").read_text())

    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "MOCK_DATASET_DIR": str(dataset_dir),
        "MOCK_API_TOKEN": TOKEN,
        "MOCK_RELEASE_PHASE": "9",
        "MOCK_RATE_LIMIT_RPS": "0",
        "MOCK_MAX_PAGE_SIZE": str(max(page_size, 1000)),
    }
    mock = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "ingestion_pipeline.cli", "mock-api", "--port", str(port)],
        env=env,
        cwd=PROJECT_ROOT,
    )
    results = []
    try:
        _wait_healthy(base, mock)
        for mode in modes:
            _reset_database(database_url)
            run_env = {
                **os.environ,
                "PIPELINE_DATABASE_URL": database_url,
                "PIPELINE_API_BASE_URL": base,
                "PIPELINE_API_TOKEN": TOKEN,
                "PIPELINE_PAGE_SIZE": str(page_size),
                "PIPELINE_REQUESTS_PER_SECOND": "0",
                "PIPELINE_WRITE_MODE": mode,
                "PIPELINE_LOG_LEVEL": "WARNING",
                "PIPELINE_MAX_PAGES_PER_RUN": "100000",
            }
            wall0 = time.monotonic()
            proc = subprocess.run(
                [sys.executable, "-m", "ingestion_pipeline.cli", "run", "--json"],
                env=run_env,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            wall = time.monotonic() - wall0
            if proc.returncode != 0:
                raise RuntimeError(
                    f"benchmark run failed: {proc.stdout[-2000:]} {proc.stderr[-2000:]}"
                )
            summary = json.loads(proc.stdout.strip().splitlines()[-1])
            engine = make_engine(database_url)
            with engine.connect() as conn:
                counts = {
                    t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
                    for t in (
                        "raw_responses",
                        "orders",
                        "order_events",
                        "quarantine",
                        "record_outcomes",
                    )
                }
                pg_version = conn.execute(text("SHOW server_version")).scalar_one()
            rec = reconcile(engine, dataset_dir)
            engine.dispose()
            fetched = sum(e["records_fetched"] for e in summary["entities"].values())
            pages = sum(e["pages_processed"] for e in summary["entities"].values())
            results.append(
                {
                    "write_mode": mode,
                    "records_fetched": fetched,
                    "pages": pages,
                    "http_requests": summary["http"]["attempts"],
                    "retries": summary["http"]["retries"],
                    "duration_seconds": summary["duration_seconds"],
                    "process_wall_seconds": round(wall, 3),
                    "records_per_second": round(fetched / summary["duration_seconds"], 1),
                    "rows_written": counts,
                    "page_transactions": pages,
                    "peak_rss_mb": summary["peak_rss_mb"],
                    "reconciliation": rec["result"],
                    "postgres_version": pg_version,
                }
            )
    finally:
        mock.terminate()
        mock.wait(timeout=10)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "workload": {
            "orders": orders,
            "seed": seed,
            "page_size": page_size,
            "source_rows": manifest["source_rows"],
        },
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
            "postgres": results[0]["postgres_version"] if results else None,
            "note": "mock API, pipeline and PostgreSQL all on the same machine",
        },
        "results": results,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
