"""Command-line interface: ``ingest <command>``.

Exit codes: 0 success, 1 run/verification failed, 2 usage/configuration error.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .config import ENTITIES, PipelineSettings
from .db import PROJECT_ROOT, make_engine, run_migrations
from .errors import ConcurrentRunError
from .logging_config import configure_logging


def _ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{value!r} needs a time zone, e.g. 2026-01-01T00:00:00Z")
    return ts


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux kilobytes
    return round(rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024, 1)


# ------------------------------------------------------------------ commands
def cmd_db_upgrade(args: argparse.Namespace, settings: PipelineSettings) -> int:
    run_migrations(settings.database_url_value())
    print("database schema is at head")
    return 0


def cmd_db_reset(args: argparse.Namespace, settings: PipelineSettings) -> int:
    """Development helper: drop every table and re-apply migrations."""
    if not args.yes:
        print("refusing to drop the schema without --yes", file=sys.stderr)
        return 2
    from sqlalchemy import text

    engine = make_engine(settings.database_url_value())
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    engine.dispose()
    run_migrations(settings.database_url_value())
    print("database schema dropped and re-created at head")
    return 0


def cmd_generate(args: argparse.Namespace, settings: PipelineSettings) -> int:
    from .datagen import generate, write_dataset

    ds = generate(
        args.seed, args.orders, edge_cases=not args.no_edge_cases, phases=not args.no_phases
    )
    out = Path(args.out)
    write_dataset(ds, out)
    _print({"dataset_dir": str(out), **ds.manifest()})
    return 0


def cmd_mock_api(args: argparse.Namespace, settings: PipelineSettings) -> int:
    import uvicorn

    from .mock_api.app import MockSettings, create_app

    overrides: dict[str, Any] = {}
    if args.dataset_dir:
        overrides["dataset_dir"] = args.dataset_dir
    if args.phase:
        overrides["release_phase"] = args.phase
    if args.rate_limit is not None:
        overrides["rate_limit_rps"] = args.rate_limit
    mock_settings = MockSettings(**overrides)
    uvicorn.run(create_app(mock_settings), host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_mock_admin(args: argparse.Namespace, settings: PipelineSettings) -> int:
    base = args.url or settings.api_base_url
    with httpx.Client(base_url=base, timeout=10) as client:
        if args.action == "release-phase":
            resp = client.post("/_admin/release-phase", json={"phase": int(args.value)})
        elif args.action == "set-faults":
            resp = client.post("/_admin/faults", json=json.loads(args.value))
        elif args.action == "clear-faults":
            resp = client.delete("/_admin/faults")
        else:
            resp = client.get("/_admin/stats")
        resp.raise_for_status()
        _print(resp.json())
    return 0


def cmd_run(args: argparse.Namespace, settings: PipelineSettings) -> int:
    from .pipeline import Pipeline

    if args.page_size:
        settings.page_size = args.page_size
    if args.write_mode:
        settings.write_mode = args.write_mode
    if settings.api_token is None:
        print("PIPELINE_API_TOKEN is not set (see .env.example)", file=sys.stderr)
        return 2
    engine = make_engine(settings.database_url_value())
    try:
        result = Pipeline(settings, engine).run(
            args.entity or ENTITIES, since=args.since, until=args.until, max_pages=args.max_pages
        )
    except ConcurrentRunError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    summary = {
        "run_id": result.run_id,
        "status": result.status,
        "failure_category": result.failure_category,
        "error_message": result.error_message,
        "stopped_early": result.stopped_early,
        "duration_seconds": round(result.duration_seconds, 3),
        "http": vars(result.http),
        "entities": {e: s.as_row() for e, s in result.entity_stats.items()},
        "peak_rss_mb": peak_rss_mb(),
    }
    if args.json:
        print(json.dumps(summary, default=str))
    else:
        _print(summary)
    return 0 if result.status == "succeeded" else 1


def cmd_runs(args: argparse.Namespace, settings: PipelineSettings) -> int:
    from .reporting import list_runs

    rows = list_runs(make_engine(settings.database_url_value()), args.limit)
    print(
        f"{'run_id':36}  {'status':10} {'mode':11} {'started_at':32} {'secs':>8} "
        f"{'http':>5} {'retry':>5}  failure"
    )
    for r in rows:
        print(
            f"{r['run_id']:36}  {r['status']:10} {r['mode']:11} {r['started_at']:32} "
            f"{r['duration_seconds'] or 0:>8} {r['http_attempts']:>5} {r['http_retries']:>5}  "
            f"{r['failure_category'] or ''}"
        )
    return 0


def cmd_show_run(args: argparse.Namespace, settings: PipelineSettings) -> int:
    from .reporting import run_summary

    run_id = None if args.run_id in (None, "latest") else args.run_id
    try:
        _print(run_summary(make_engine(settings.database_url_value()), run_id))
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_checkpoints(args: argparse.Namespace, settings: PipelineSettings) -> int:
    from . import PIPELINE_NAME
    from . import checkpoint as ckpt

    engine = make_engine(settings.database_url_value())
    if args.action == "show":
        with engine.connect() as conn:
            _print(ckpt.load_all(conn, PIPELINE_NAME))
        return 0
    if not args.yes:
        print(
            "refusing to reset checkpoints without --yes (the next run re-reads everything)",
            file=sys.stderr,
        )
        return 2
    with engine.begin() as conn:
        n = ckpt.reset(conn, PIPELINE_NAME, args.entity)
    print(f"deleted {n} checkpoint(s)")
    return 0


def cmd_reconcile(args: argparse.Namespace, settings: PipelineSettings) -> int:
    from .reporting import reconcile, write_report

    report = reconcile(
        make_engine(settings.database_url_value()), Path(args.dataset) if args.dataset else None
    )
    if args.out:
        jp, mp = write_report(report, Path(args.out))
        print(f"wrote {jp} and {mp}", file=sys.stderr)
    for c in report["checks"]:
        print(f"{c['status']}  {c['check']}")
    print(f"RESULT: {report['result']}")
    return 0 if report["result"] == "PASS" else 1


def cmd_benchmark(args: argparse.Namespace, settings: PipelineSettings) -> int:
    from .benchmark import run_benchmark

    report = run_benchmark(
        database_url=args.database_url or settings.database_url_value(),
        orders=args.orders,
        seed=args.seed,
        page_size=args.page_size,
        modes=args.modes,
        port=args.port,
        out=Path(args.out),
        force_reset=args.force_reset,
    )
    for r in report["results"]:
        print(
            f"{r['write_mode']:6} fetched={r['records_fetched']} "
            f"seconds={r['duration_seconds']} rps={r['records_per_second']} "
            f"peak_rss_mb={r['peak_rss_mb']} reconciliation={r['reconciliation']}"
        )
    return 0


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ingest", description="Orders API ingestion pipeline")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("db-upgrade", help="apply database migrations").set_defaults(fn=cmd_db_upgrade)
    dr = sub.add_parser("db-reset", help="DEV ONLY: drop all tables and re-run migrations")
    dr.add_argument("--yes", action="store_true")
    dr.set_defaults(fn=cmd_db_reset)

    g = sub.add_parser("generate-data", help="generate a deterministic source dataset")
    g.add_argument("--out", default="data/generated/review")
    g.add_argument("--orders", type=int, default=200)
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--no-edge-cases", action="store_true")
    g.add_argument("--no-phases", action="store_true", help="release everything in phase 1")
    g.set_defaults(fn=cmd_generate)

    m = sub.add_parser("mock-api", help="start the mock source API (MOCK_* env vars)")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8000)
    m.add_argument("--dataset-dir")
    m.add_argument("--phase", type=int)
    m.add_argument("--rate-limit", type=float, help="server-side requests/second (0 = off)")
    m.set_defaults(fn=cmd_mock_api)

    a = sub.add_parser("mock-admin", help="control a running mock API")
    a.add_argument("action", choices=["release-phase", "set-faults", "clear-faults", "stats"])
    a.add_argument("value", nargs="?", help="phase number or JSON list of fault rules")
    a.add_argument("--url", help="mock base URL (default PIPELINE_API_BASE_URL)")
    a.set_defaults(fn=cmd_mock_admin)

    r = sub.add_parser("run", help="run the ingestion pipeline")
    r.add_argument("--entity", action="append", choices=ENTITIES)
    r.add_argument("--since", type=_ts, help="backfill from this timestamp (inclusive)")
    r.add_argument("--until", type=_ts, help="stop after the page that passes this timestamp")
    r.add_argument("--max-pages", type=int)
    r.add_argument("--page-size", type=int)
    r.add_argument("--write-mode", choices=["batch", "row"])
    r.add_argument("--json", action="store_true", help="single-line JSON summary")
    r.set_defaults(fn=cmd_run)

    ls = sub.add_parser("runs", help="list recent runs")
    ls.add_argument("--limit", type=int, default=10)
    ls.set_defaults(fn=cmd_runs)

    s = sub.add_parser("show-run", help="summary of one run (default: latest)")
    s.add_argument("run_id", nargs="?", default="latest")
    s.set_defaults(fn=cmd_show_run)

    c = sub.add_parser("checkpoints", help="show or reset checkpoints")
    c.add_argument("action", choices=["show", "reset"])
    c.add_argument("--entity", choices=ENTITIES)
    c.add_argument("--yes", action="store_true")
    c.set_defaults(fn=cmd_checkpoints)

    rc = sub.add_parser("reconcile", help="reconciliation checks (+ oracle if --dataset)")
    rc.add_argument("--dataset", help="dataset dir from generate-data (enables oracle checks)")
    rc.add_argument("--out", help="write <out>.json and <out>.md")
    rc.set_defaults(fn=cmd_reconcile)

    b = sub.add_parser("benchmark", help="repeatable throughput benchmark")
    b.add_argument("--orders", type=int, default=20_000)
    b.add_argument("--seed", type=int, default=7)
    b.add_argument("--page-size", type=int, default=1000)
    b.add_argument("--modes", nargs="+", default=["batch", "row"], choices=["batch", "row"])
    b.add_argument("--port", type=int, default=8765)
    b.add_argument("--database-url", help="defaults to PIPELINE_DATABASE_URL")
    b.add_argument("--out", default=str(PROJECT_ROOT / "reports" / "benchmark"))
    b.add_argument(
        "--force-reset",
        action="store_true",
        help="allow wiping a database whose name does not contain 'bench'",
    )
    b.set_defaults(fn=cmd_benchmark)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = PipelineSettings()
    configure_logging(settings.log_level, settings.log_format)
    return args.fn(args, settings)


if __name__ == "__main__":
    sys.exit(main())
