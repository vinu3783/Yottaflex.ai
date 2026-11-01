"""CSV / JSON / SQL writers and the typed CSV loader, all driven by schema.py."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from plantsim.schema import TABLE_BY_NAME, TABLES, Col, Table


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    return str(v)


def parse(col: Col, s: str):
    if s == "":
        return None
    if col.type == "INTEGER":
        return int(s)
    if col.type == "REAL":
        return float(s)
    if col.type == "BOOLEAN":
        if s not in ("true", "false"):
            raise ValueError(f"{col.name}: bad boolean {s!r}")
        return s == "true"
    return s


def write_csv(path: Path, table: Table, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(table.names)
        for r in rows:
            w.writerow([fmt(r.get(c)) for c in table.names])


def read_csv(path: Path, table: Table) -> list[dict]:
    """Typed read. Unknown columns are kept as text so validation can report them."""
    with path.open(newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        known = {c.name: c for c in table.cols}
        rows = []
        for raw in rd:
            row = {}
            for k, v in raw.items():
                row[k] = parse(known[k], v) if k in known else (v or None)
            rows.append(row)
        return rows


def csv_header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as fh:
        return next(csv.reader(fh))


def load_dataset(data_dir: Path) -> dict[str, list[dict]]:
    out = {}
    for t in TABLES:
        p = data_dir / "csv" / f"{t.name}.csv"
        out[t.name] = read_csv(p, t) if p.exists() else []
    return out


def write_json(path: Path, table: Table, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("[\n")
        for k, r in enumerate(rows):
            obj = {c: r.get(c) for c in table.names}
            fh.write(("  " if k == 0 else ",\n  ") + json.dumps(obj, ensure_ascii=False))
        fh.write("\n]\n")


def write_plain_csv(path: Path, columns, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(columns)
        for r in rows:
            w.writerow([fmt(r.get(c)) for c in columns])


def dump_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=False, ensure_ascii=False) + "\n", "utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- SQL

SQL_TYPES = {
    "TEXT": "TEXT",
    "INTEGER": "INTEGER",
    "REAL": "DOUBLE PRECISION",
    "BOOLEAN": "BOOLEAN",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMPTZ",
    "JSON": "TEXT",
}


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def sql_literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    return _q(str(v))


def ddl() -> str:
    lines = [
        "-- Generated from src/plantsim/schema.py. Portable across PostgreSQL 13+ and SQLite 3.35+.",
        "",
    ]
    for t in reversed(TABLES):
        lines.append(f"DROP TABLE IF EXISTS {t.name};")
    lines.append("DROP TABLE IF EXISTS rejected_record;")
    lines.append("")
    for t in TABLES:
        parts = []
        for c in t.cols:
            d = f"  {c.name} {SQL_TYPES[c.type]}"
            if c.required:
                d += " NOT NULL"
            checks = []
            if c.allowed:
                checks.append(f"{c.name} IN ({', '.join(_q(a) for a in c.allowed)})")
            if c.min is not None:
                checks.append(f"{c.name} >= {c.min!r}")
            if c.max is not None:
                checks.append(f"{c.name} <= {c.max!r}")
            if checks:
                d += " CHECK (" + " AND ".join(checks) + ")"
            parts.append(d)
        parts.append(f"  PRIMARY KEY ({', '.join(t.pk)})")
        for c in t.cols:
            if c.unique and (c.name,) != t.pk:
                parts.append(f"  UNIQUE ({c.name})")
        for u in t.unique_together:
            parts.append(f"  UNIQUE ({', '.join(u)})")
        for c in t.cols:
            if c.fk:
                ref_t, ref_c = c.fk.split(".")
                parts.append(f"  FOREIGN KEY ({c.name}) REFERENCES {ref_t} ({ref_c})")
        for group in t.exactly_one_of:
            expr = " + ".join(f"(CASE WHEN {g} IS NULL THEN 0 ELSE 1 END)" for g in group)
            parts.append(f"  CHECK ({expr} = 1)")
        for group in t.at_most_one_of:
            expr = " + ".join(f"(CASE WHEN {g} IS NULL THEN 0 ELSE 1 END)" for g in group)
            parts.append(f"  CHECK ({expr} <= 1)")
        lines.append(f"CREATE TABLE {t.name} (\n" + ",\n".join(parts) + "\n);")
        lines.append("")
    lines.append(
        "CREATE TABLE rejected_record (\n  table_name TEXT NOT NULL,\n  record_key TEXT NOT NULL,\n"
        "  reason TEXT NOT NULL,\n  payload_json TEXT NOT NULL,\n  PRIMARY KEY (table_name, record_key)\n);"
    )
    lines.append("")
    lines.append("CREATE INDEX ix_telemetry_run_sensor ON telemetry (simulation_run_id, sensor_id, step);")
    lines.append("CREATE INDEX ix_alarm_run ON alarm (simulation_run_id);")
    return "\n".join(lines) + "\n"


def split_loadable(tables: dict[str, list[dict]]):
    """Rows that satisfy the declared constraints, plus rejects (cascading to children)."""
    accepted: dict[str, list[dict]] = {}
    keys: dict[str, set] = {}
    rejected = []
    for t in TABLES:
        good = []
        seen = set()
        for r in tables.get(t.name, []):
            key = tuple(r.get(k) for k in t.pk)
            reason = None
            for c in t.cols:
                if c.required and r.get(c.name) is None:
                    reason = f"NOT NULL violated: {c.name}"
                    break
                if c.fk and r.get(c.name) is not None:
                    ref_t, ref_c = c.fk.split(".")
                    if (r[c.name],) not in keys.get(ref_t, set()):
                        reason = f"FOREIGN KEY violated: {c.name}={r[c.name]} not in {ref_t}"
                        break
            if reason is None and key in seen:
                reason = "PRIMARY KEY duplicate"
            if reason:
                rejected.append(
                    {
                        "table_name": t.name,
                        "record_key": "|".join(str(k) for k in key),
                        "reason": reason,
                        "payload_json": json.dumps({c: r.get(c) for c in t.names}),
                    }
                )
                continue
            seen.add(key)
            good.append(r)
        accepted[t.name] = good
        if len(t.pk) == 1:
            keys[t.name] = seen
        else:
            keys[t.name] = seen
    return accepted, rejected


def write_seed_sql(path: Path, tables: dict[str, list[dict]]) -> dict:
    accepted, rejected = split_loadable(tables)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("-- Seed data. Rows that break a declared constraint go to rejected_record.\n")
        fh.write("BEGIN;\n")
        for t in TABLES:
            rows = accepted[t.name]
            for start in range(0, len(rows), 500):
                chunk = rows[start : start + 500]
                fh.write(f"INSERT INTO {t.name} ({', '.join(t.names)}) VALUES\n")
                fh.write(
                    ",\n".join("(" + ", ".join(sql_literal(r.get(c)) for c in t.names) + ")" for r in chunk)
                )
                fh.write(";\n")
        if rejected:
            fh.write("INSERT INTO rejected_record (table_name, record_key, reason, payload_json) VALUES\n")
            fh.write(
                ",\n".join(
                    "("
                    + ", ".join(
                        sql_literal(r[c]) for c in ("table_name", "record_key", "reason", "payload_json")
                    )
                    + ")"
                    for r in rejected
                )
            )
            fh.write(";\n")
        fh.write("COMMIT;\n")
    return {
        "loaded": {k: len(v) for k, v in accepted.items()},
        "rejected": [{k: r[k] for k in ("table_name", "record_key", "reason")} for r in rejected],
    }


def table(name: str) -> Table:
    return TABLE_BY_NAME[name]
