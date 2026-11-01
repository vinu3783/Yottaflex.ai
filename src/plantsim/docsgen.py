"""Write docs/DATA_DICTIONARY.md from schema.py (run: plantsim docs)."""

from __future__ import annotations

from pathlib import Path

from plantsim import config, detector
from plantsim.schema import GROUND_TRUTH_COLUMNS, TABLES

# (parent, child, cardinality, optional?, via)
RELATIONSHIPS = (
    ("facility", "node", "1:N", "no", "node.facility_id"),
    ("node", "tank / pump / valve", "1:1 (subtype)", "no", "tank.tank_id = node.node_id, ..."),
    ("facility", "pipe", "1:N", "no", "pipe.facility_id"),
    ("node", "pipe (upstream end)", "1:N", "no", "pipe.from_node_id"),
    ("node", "pipe (downstream end)", "1:N", "no", "pipe.to_node_id"),
    ("facility", "sensor", "1:N", "no", "sensor.facility_id"),
    ("tank", "sensor (LEVEL)", "1:1..2", "redundant sensor optional", "sensor.attached_node_id"),
    ("pump", "sensor (DIFF_PRESSURE)", "1:1", "no", "sensor.attached_node_id"),
    ("pipe", "sensor (FLOW)", "1:0..1", "yes", "sensor.attached_pipe_id"),
    ("operating_mode", "valve", "N:M", "no", "operating_mode_valve_setting"),
    ("operating_mode", "pump", "N:M", "no", "operating_mode_pump_setting"),
    ("pump", "operating_mode (MAINTENANCE)", "1:0..N", "yes", "operating_mode.maintenance_pump_id"),
    ("facility", "simulation_run", "1:N", "no", "simulation_run.facility_id"),
    ("simulation_run", "simulation_assumption", "N:M", "no", "simulation_run_assumption"),
    ("simulation_run", "simulation_run_mode_segment", "1:N", "no", "segment.simulation_run_id"),
    ("operating_mode", "simulation_run_mode_segment", "1:N", "no", "segment.mode_id"),
    ("simulation_run", "telemetry", "1:N", "no", "telemetry.simulation_run_id"),
    ("sensor", "telemetry", "1:N", "no", "telemetry.sensor_id"),
    ("simulation_run", "pump_state_event / valve_state_event", "1:N", "no", "*.simulation_run_id"),
    (
        "node / pipe / sensor",
        "component_status_event",
        "1:N",
        "no",
        "exactly one of node_id, pipe_id, sensor_id",
    ),
    (
        "simulation_run",
        "component_status_event",
        "1:0..N",
        "yes",
        "set when the change happened in a run",
    ),
    ("simulation_run", "alarm", "1:N", "no", "alarm.simulation_run_id"),
    (
        "node / pipe / sensor / telemetry",
        "alarm",
        "1:0..N",
        "yes",
        "at most one of node_id, pipe_id, sensor_id",
    ),
    ("alarm", "alarm_event", "1:1..2", "no", "alarm_event.alarm_id"),
    ("alarm (OPERATOR)", "alarm_review", "1:1", "no", "alarm_review.alarm_id"),
)


def write_data_dictionary(path: Path) -> Path:
    L = [
        "# Data dictionary",
        "",
        "Generated from `src/plantsim/schema.py` by `plantsim docs`; the SQL DDL, the CSV parser and the",
        "validator use the same definitions. All timestamps are UTC ISO 8601 (`YYYY-MM-DDTHH:MM:SSZ`).",
        "Units are part of the column name and listed per field.",
        "",
        "## Entities and keys",
        "",
        "| Table | Kind | Primary key | Description |",
        "|---|---|---|---|",
    ]
    for t in TABLES:
        L.append(f"| [{t.name}](#{t.name}) | {t.kind} | {', '.join(t.pk)} | {t.desc} |")
    L += [
        "",
        "Kinds: MASTER = reference entities generated first; BRIDGE = many-to-many; TRANSACTION = run",
        "records; EVENT = append-only state history; DERIVED = calculated by documented rules.",
        "",
        "## Relationships and cardinality",
        "",
        "| Parent | Child | Cardinality | Optional | Implemented by |",
        "|---|---|---|---|---|",
    ]
    L += [f"| {a} | {b} | {c} | {o} | `{v}` |" for a, b, c, o, v in RELATIONSHIPS]
    L += [
        "",
        "## Effective dates and lifecycle",
        "",
        "- Components (tank, pump, valve, pipe, sensor) carry `install_date` and a `status` that is",
        "  **derived** from `component_status_event` as of the reference date "
        f"({config.REFERENCE_DATE}). Allowed transitions:",
        "  `(none) -> INSTALLED -> ACTIVE <-> MAINTENANCE`, `ACTIVE|MAINTENANCE -> RETIRED`.",
        "- `operating_mode` is valid from `effective_from` to `effective_to` (open-ended when null).",
        "- A run's mode schedule is `simulation_run_mode_segment` (contiguous, covers every step).",
        "  A MAINTENANCE segment writes ACTIVE -> MAINTENANCE -> ACTIVE events for its pump.",
        "- Pump run/stop, valve settings and alarm states are append-only event tables; nothing is",
        "  updated in place.",
        "",
        "## ID conventions",
        "",
        "| Entity | Format | Example |",
        "|---|---|---|",
        "| Facility | `FAC-NNN` | FAC-001 |",
        "| Pump / Tank / Valve | `PUMP-NNN` / `TANK-NNN` / `VALVE-NNN` | PUMP-001 |",
        "| Pipe | `PIPE-NNN` | PIPE-001 |",
        "| Sensor | `SNS-NNN` | SNS-001 |",
        "| Operating mode | `MODE-NNN` | MODE-001 |",
        "| Simulation run | `SIM-2026-NNNNNN` | SIM-2026-000001 |",
        "| Telemetry | `TEL-NNNNNNN` | TEL-0000001 |",
        "| Events | `PSE-` / `VSE-` / `CSE-` / `AEV-NNNNNN` | PSE-000001 |",
        "| Alarm / review | `ALM-NNNNNN` / `ARV-NNNN` | ALM-000001 |",
        "| Assumption / label | `ASM-NNN` / `GT-NNNN` | ASM-001 |",
        "",
        "Counters are zero-padded, assigned in generation order, never reused.",
        "",
    ]
    for t in TABLES:
        L += [f"## {t.name}", "", t.desc, "", f"Primary key: `{', '.join(t.pk)}`."]
        if t.unique_together:
            L.append("Unique: " + "; ".join(f"`({', '.join(u)})`" for u in t.unique_together) + ".")
        for g in t.exactly_one_of:
            L.append(f"Exactly one of `{', '.join(g)}` is set.")
        for g in t.at_most_one_of:
            L.append(f"At most one of `{', '.join(g)}` is set.")
        L += [
            "",
            "| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |",  # noqa: E501
            "|---|---|---|---|---|---|---|---|",
        ]
        for c in t.cols:
            allowed = ", ".join(c.allowed) if c.allowed else ""
            if c.min is not None or c.max is not None:
                rng = f"{'' if c.min is None else c.min} .. {'' if c.max is None else c.max}"
                allowed = f"{allowed} {rng}".strip()
            rule = []
            if c.pattern:
                rule.append(f"format {c.pattern}")
            if c.fk:
                rule.append(f"FK -> {c.fk}")
            if c.derived:
                rule.append(f"derived: {c.derived}")
            if c.rule:
                rule.append(c.rule)
            L.append(
                f"| {c.name} | {c.type} | {'yes' if c.required else 'no'} | {c.unit or ''} | {allowed} | "
                f"{'yes' if c.unique or (c.name,) == t.pk else ''} | {'; '.join(rule)} | {c.desc} |"
            )
        L.append("")
    L += [
        "## ground_truth (separate folder, never an input)",
        "",
        "`ground_truth/ground_truth.csv` has one row per label; `ground_truth.json` adds the fault",
        "injections and true (noise-free) outcomes per run.",
        "",
        "| Field | Description |",
        "|---|---|",
    ]
    desc = {
        "gt_id": "Label ID (GT-NNNN)",
        "category": "NONE, PROCESS_FAULT, SENSOR_FAULT, EDGE_CASE, LABEL_CHECK",
        "issue_type": "e.g. LEAK, BLOCKAGE, PUMP_DEGRADATION, SENSOR_STUCK, PIPE_REFERENCES_MISSING_NODE",
        "simulation_run_id": "Run the label applies to (empty for master-data cases)",
        "facility_id": "Facility",
        "component_id": "Faulty component (pipe, pump, sensor or tank)",
        "sensor_id": "Sensor involved, if any",
        "record_table": "Table of the labelled record",
        "record_id": "Key of the labelled record",
        "onset_step": "First faulty interval",
        "onset_ts": "Start of the first faulty interval (UTC)",
        "severity": "Fault size (leak fraction, flow ratio, final health, bias, drift m/h, level/height)",
        "expected_alarm_type": "Alarm a correct detector raises",
        "expected_decision": "ALARM, NO_ALARM, FLAG_DATA_QUALITY, REJECT_RECORD, REJECT_NOT_REPRODUCIBLE, ...",  # noqa: E501
        "rule_basis": "The documented rule the label was derived from",
    }
    L += [f"| {c} | {desc[c]} |" for c in GROUND_TRUTH_COLUMNS]
    L += ["", "## Detector thresholds", "", "| Parameter | Value |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in detector.THRESHOLDS.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", "utf-8")
    return path
