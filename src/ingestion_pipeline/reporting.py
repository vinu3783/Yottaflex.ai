"""Run summaries and reconciliation.

All numbers come from the database (run metadata, ledger, target tables) and,
optionally, from the generator's oracle files. Nothing here is estimated.

Why raw counts can differ from target counts (also in docs):
* a record can be delivered many times (retries are separate raw rows; lookback
  windows and backfills re-read records) -> ``duplicate`` / ``stale`` outcomes;
* invalid and conflicting records are quarantined, not published;
* several versions of one order collapse into one ``orders`` row;
* a page archived but never committed (crash, breaking schema change) has raw
  rows but no ledger rows.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, desc, select, text

from . import PIPELINE_NAME
from . import checkpoint as ckpt
from .datagen import load_expected
from .models import pipeline_runs, run_entity_stats

OUTCOME_COLUMNS = ("inserted", "updated", "duplicate", "stale", "quarantined")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


# ------------------------------------------------------------------ runs
def list_runs(engine: Engine, limit: int = 10) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(
                    pipeline_runs.c.run_id,
                    pipeline_runs.c.status,
                    pipeline_runs.c.mode,
                    pipeline_runs.c.started_at,
                    pipeline_runs.c.duration_seconds,
                    pipeline_runs.c.http_attempts,
                    pipeline_runs.c.http_retries,
                    pipeline_runs.c.failure_category,
                )
                .order_by(desc(pipeline_runs.c.started_at))
                .limit(limit)
            )
            .mappings()
            .all()
        )
    return [_jsonable(dict(r)) for r in rows]


def run_summary(engine: Engine, run_id: str | None = None) -> dict[str, Any]:
    with engine.connect() as conn:
        q = select(pipeline_runs)
        q = (
            q.where(pipeline_runs.c.run_id == run_id)
            if run_id
            else q.order_by(desc(pipeline_runs.c.started_at)).limit(1)
        )
        run = conn.execute(q).mappings().one_or_none()
        if run is None:
            raise LookupError(f"run {run_id or 'latest'} not found")
        rid = run["run_id"]
        entities = (
            conn.execute(select(run_entity_stats).where(run_entity_stats.c.run_id == rid))
            .mappings()
            .all()
        )
        raw = (
            conn.execute(
                text(
                    "SELECT entity, outcome, status_code, count(*) AS n FROM raw_responses "
                    "WHERE run_id = :r GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
                ),
                {"r": rid},
            )
            .mappings()
            .all()
        )
        errors = (
            conn.execute(
                text(
                    "SELECT entity, unnest(error_codes) AS code, count(*) AS n FROM quarantine "
                    "WHERE run_id = :r GROUP BY 1, 2 ORDER BY 1, 3 DESC"
                ),
                {"r": rid},
            )
            .mappings()
            .all()
        )
    ent = {}
    for e in entities:
        d = dict(e)
        d.pop("run_id")
        explained = sum(d[f"records_{o}"] for o in OUTCOME_COLUMNS)
        d["unexplained_difference"] = d["records_fetched"] - explained
        ent[d.pop("entity")] = d
    return _jsonable(
        {
            "run": dict(run),
            "entities": ent,
            "http_attempts_by_outcome": [dict(r) for r in raw],
            "quarantine_by_code": [dict(r) for r in errors],
        }
    )


# ------------------------------------------------------------------ reconciliation
_ENTITY_SQL = {
    "fetched_per_runs": "SELECT coalesce(sum(records_fetched),0) FROM run_entity_stats "
    "WHERE entity = :e",
    "ledger_rows": "SELECT count(*) FROM record_outcomes WHERE entity = :e",
    "raw_ok_records": "SELECT coalesce(sum(record_count),0) FROM raw_responses "
    "WHERE entity = :e AND outcome = 'ok'",
    "raw_ok_records_not_processed": (
        "SELECT coalesce(sum(r.record_count),0) FROM raw_responses r WHERE r.entity = :e "
        "AND r.outcome = 'ok' AND r.record_count > 0 AND NOT EXISTS "
        "(SELECT 1 FROM record_outcomes o WHERE o.raw_response_id = r.raw_response_id)"
    ),
    "distinct_source_rows_observed": "SELECT count(DISTINCT source_record_id) "
    "FROM record_outcomes WHERE entity = :e",
    "observed_rows_without_id": "SELECT count(*) FROM record_outcomes "
    "WHERE entity = :e AND source_record_id IS NULL",
    "source_rows_delivered_more_than_once": (
        "SELECT count(*) FROM (SELECT source_record_id FROM record_outcomes WHERE entity = :e "
        "AND source_record_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1) t"
    ),
    "quarantine_rows": "SELECT count(*) FROM quarantine WHERE entity = :e",
    "quarantine_distinct_source_rows": "SELECT count(DISTINCT coalesce(source_record_id, "
    "md5(payload::text))) FROM quarantine WHERE entity = :e",
    "quarantine_unresolved_distinct": "SELECT count(DISTINCT coalesce(source_record_id, "
    "md5(payload::text))) FROM quarantine "
    "WHERE entity = :e AND resolved_at IS NULL",
}


def _outcome_totals(conn, entity: str) -> dict[str, int]:
    rows = conn.execute(
        text("SELECT outcome, count(*) FROM record_outcomes WHERE entity = :e GROUP BY 1"),
        {"e": entity},
    ).all()
    totals = dict.fromkeys(OUTCOME_COLUMNS, 0)
    totals.update({k: v for k, v in rows})
    return totals


def reconcile(engine: Engine, dataset_dir: Path | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any) -> None:
        checks.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    report: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "entities": {}}
    with engine.connect() as conn:
        for entity in ("orders", "order_events"):
            e: dict[str, Any] = {
                k: conn.execute(text(sql), {"e": entity}).scalar_one()
                for k, sql in _ENTITY_SQL.items()
            }
            e["outcomes"] = _outcome_totals(conn, entity)
            e["quarantine_by_code"] = dict(
                conn.execute(
                    text(
                        "SELECT code, "
                        "count(DISTINCT coalesce(source_record_id, md5(payload::text))) "
                        "FROM quarantine, unnest(error_codes) code WHERE entity = :e "
                        "AND resolved_at IS NULL GROUP BY 1 ORDER BY 1"
                    ),
                    {"e": entity},
                ).all()
            )
            table = "orders" if entity == "orders" else "order_events"
            e["target_rows"] = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            report["entities"][entity] = e

            per_run = conn.execute(
                text(
                    "SELECT s.run_id::text, s.records_fetched, "
                    "s.records_inserted + s.records_updated + s.records_duplicate "
                    "+ s.records_stale + s.records_quarantined AS explained, "
                    "(SELECT count(*) FROM record_outcomes o WHERE o.run_id = s.run_id "
                    " AND o.entity = s.entity) AS ledger "
                    "FROM run_entity_stats s WHERE s.entity = :e"
                ),
                {"e": entity},
            ).all()
            bad = [r[0] for r in per_run if not (r[1] == r[2] == r[3])]
            check(
                f"{entity}: per-run fetched = inserted+updated+duplicate+stale+quarantined "
                "= ledger rows",
                not bad,
                {"runs_checked": len(per_run), "mismatched_runs": bad},
            )
            check(
                f"{entity}: target rows = 'inserted' outcomes (each key inserted once)",
                e["target_rows"] == e["outcomes"]["inserted"],
                {"target_rows": e["target_rows"], "inserted": e["outcomes"]["inserted"]},
            )
            check(
                f"{entity}: raw 'ok' records = ledger rows + unprocessed raw records",
                e["raw_ok_records"] == e["ledger_rows"] + e["raw_ok_records_not_processed"],
                {
                    k: e[k]
                    for k in ("raw_ok_records", "ledger_rows", "raw_ok_records_not_processed")
                },
            )

        fk = conn.execute(
            text(
                "SELECT count(*) FROM order_events ev LEFT JOIN orders o USING (order_id) "
                "WHERE o.order_id IS NULL"
            )
        ).scalar_one()
        check("referential integrity: every order_event has an order", fk == 0, {"violations": fk})
        rules = conn.execute(
            text(
                "SELECT count(*) FROM orders WHERE order_amount < 0 OR updated_at < created_at "
                "OR source_updated_at IS NULL"
            )
        ).scalar_one()
        check("orders: business rules hold in target", rules == 0, {"violations": rules})

        report["checkpoints"] = ckpt.load_all(conn, PIPELINE_NAME)
        report["runs"] = _jsonable(
            [
                dict(r)
                for r in conn.execute(
                    text(
                        "SELECT status, count(*) AS runs, sum(http_attempts) AS http_attempts, "
                        "sum(http_retries) AS retries, sum(http_rate_limited) AS rate_limited, "
                        "sum(http_server_errors) AS server_errors, sum(http_timeouts) AS timeouts, "
                        "sum(duration_seconds) AS seconds FROM pipeline_runs GROUP BY status"
                    )
                ).mappings()
            ]
        )
        report["raw_attempts_by_outcome"] = dict(
            conn.execute(
                text("SELECT outcome, count(*) FROM raw_responses GROUP BY 1 ORDER BY 1")
            ).all()
        )

        if dataset_dir is not None:
            _oracle_checks(conn, dataset_dir, report, check)

    report["checks"] = checks
    report["result"] = "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"
    return report


def _oracle_checks(conn, dataset_dir: Path, report: dict[str, Any], check) -> None:
    """Compare the database with the generator's expected results."""
    exp = load_expected(dataset_dir)
    final = exp["final_state"]
    report["oracle"] = {"dataset_dir": str(dataset_dir), "manifest": exp["manifest"]}

    actual_orders = dict(conn.execute(text("SELECT order_id, source_record_id FROM orders")).all())
    missing = sorted(set(final["orders"]) - set(actual_orders))
    extra = sorted(set(actual_orders) - set(final["orders"]))
    wrong = sorted(
        k
        for k in set(final["orders"]) & set(actual_orders)
        if final["orders"][k] != actual_orders[k]
    )
    check(
        "oracle: orders target = expected keys and latest valid version",
        not (missing or extra or wrong),
        {
            "expected": len(final["orders"]),
            "actual": len(actual_orders),
            "missing": missing[:10],
            "missing_count": len(missing),
            "unexpected": extra[:10],
            "wrong_version": wrong[:10],
            "wrong_version_count": len(wrong),
        },
    )

    actual_events = dict(conn.execute(text("SELECT event_id, event_type FROM order_events")).all())
    missing_e = sorted(set(final["order_events"]) - set(actual_events))
    extra_e = sorted(set(actual_events) - set(final["order_events"]))
    wrong_e = sorted(
        k
        for k in set(final["order_events"]) & set(actual_events)
        if final["order_events"][k] != actual_events[k]
    )
    check(
        "oracle: order_events target = expected events",
        not (missing_e or extra_e or wrong_e),
        {
            "expected": len(final["order_events"]),
            "actual": len(actual_events),
            "missing_count": len(missing_e),
            "unexpected": extra_e[:10],
            "wrong": wrong_e[:10],
        },
    )

    labels = exp["labels"]
    for entity in ("orders", "order_events"):
        expected_q = {
            lab["source_record_id"]: lab["expected_error_code"]
            for lab in labels
            if lab["entity"] == entity and lab["expected_outcome"] == "quarantined"
        }
        rows = conn.execute(
            text(
                "SELECT DISTINCT coalesce(source_record_id, payload #>> '{}') AS rid, error_codes "
                "FROM quarantine WHERE entity = :e AND resolved_at IS NULL"
            ),
            {"e": entity},
        ).all()
        actual_q: dict[str, set[str]] = {}
        for rid, codes in rows:
            # non-object payloads carry no id; the generator embeds it in the string
            rid = rid.removeprefix("corrupted-record-")
            actual_q.setdefault(rid, set()).update(codes)
        missing_q = sorted(set(expected_q) - set(actual_q))
        unexpected_q = sorted(set(actual_q) - set(expected_q))
        wrong_code = sorted(
            r for r in set(expected_q) & set(actual_q) if expected_q[r] not in actual_q[r]
        )
        check(
            f"oracle: {entity} quarantine = labelled invalid rows with expected codes",
            not (missing_q or unexpected_q or wrong_code),
            {
                "expected": len(expected_q),
                "actual": len(actual_q),
                "missing": missing_q[:10],
                "unexpected": unexpected_q[:10],
                "wrong_code": wrong_code[:10],
            },
        )

        first = dict(
            conn.execute(
                text(
                    "SELECT DISTINCT ON (source_record_id) source_record_id, outcome "
                    "FROM record_outcomes WHERE entity = :e AND source_record_id IS NOT NULL "
                    "ORDER BY source_record_id, outcome_id"
                ),
                {"e": entity},
            ).all()
        )
        mismatches = [
            {
                "source_record_id": lab["source_record_id"],
                "scenario": lab["scenario"],
                "expected": lab["expected_outcome"],
                "actual": first.get(lab["source_record_id"]),
            }
            for lab in labels
            if lab["entity"] == entity
            and lab["source_record_id"] in first
            and not lab["note"].startswith("order-dependent")
            and first[lab["source_record_id"]] != lab["expected_outcome"]
        ]
        check(
            f"oracle: {entity} first-delivery outcome matches scenario label",
            not mismatches,
            {
                "labelled_rows_observed": sum(
                    1
                    for lab in labels
                    if lab["entity"] == entity and lab["source_record_id"] in first
                ),
                "mismatches": mismatches[:10],
                "mismatch_count": len(mismatches),
            },
        )


# ------------------------------------------------------------------ rendering
def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Reconciliation report\n\nGenerated: {report['generated_at']}  ",
        f"Overall result: **{report['result']}**\n",
        "## Checks\n",
        "| Result | Check | Detail |",
        "|---|---|---|",
    ]
    for c in report["checks"]:
        detail = json.dumps(c["detail"], default=str)
        if len(detail) > 220:
            detail = detail[:217] + "..."
        lines.append(f"| {c['status']} | {c['check']} | `{detail}` |")
    lines.append("\n## Per-entity counts\n")
    keys = [
        "fetched_per_runs",
        "ledger_rows",
        "raw_ok_records",
        "raw_ok_records_not_processed",
        "distinct_source_rows_observed",
        "observed_rows_without_id",
        "source_rows_delivered_more_than_once",
        "target_rows",
        "quarantine_rows",
        "quarantine_distinct_source_rows",
        "quarantine_unresolved_distinct",
    ]
    ents = list(report["entities"])
    lines += ["| Metric | " + " | ".join(ents) + " |", "|---|" + "---|" * len(ents)]
    for k in keys:
        lines.append(f"| {k} | " + " | ".join(str(report["entities"][e][k]) for e in ents) + " |")
    for o in OUTCOME_COLUMNS:
        lines.append(
            f"| outcome: {o} | "
            + " | ".join(str(report["entities"][e]["outcomes"][o]) for e in ents)
            + " |"
        )
    lines.append("\n## Unresolved quarantine by error code (distinct source rows)\n")
    for e in ents:
        codes = report["entities"][e]["quarantine_by_code"]
        lines.append(f"- **{e}**: " + (", ".join(f"{k}={v}" for k, v in codes.items()) or "none"))
    lines.append("\n## Runs\n")
    lines.append("| status | runs | http_attempts | retries | 429s | 5xx | timeouts | seconds |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in report["runs"]:
        lines.append(
            f"| {r['status']} | {r['runs']} | {r['http_attempts']} | {r['retries']} | "
            f"{r['rate_limited']} | {r['server_errors']} | {r['timeouts']} | "
            f"{r['seconds']} |"
        )
    lines.append(
        "\n## Checkpoints\n\n```json\n" + json.dumps(report["checkpoints"], indent=2) + "\n```\n"
    )
    return "\n".join(lines)


def write_report(report: dict[str, Any], out_base: Path) -> tuple[Path, Path]:
    out_base.parent.mkdir(parents=True, exist_ok=True)
    jp = out_base.with_suffix(".json")
    mp = out_base.with_suffix(".md")
    jp.write_text(json.dumps(_jsonable(report), indent=2, default=str) + "\n", encoding="utf-8")
    mp.write_text(render_markdown(_jsonable(report)), encoding="utf-8")
    return jp, mp
