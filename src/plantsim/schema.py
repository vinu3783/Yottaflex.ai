"""Single source of truth for every table: columns, types, units, rules and keys.

The SQL DDL, the CSV type parser, the validator and docs/DATA_DICTIONARY.md are all
generated from these definitions, so they cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from plantsim import ids


@dataclass(frozen=True)
class Col:
    name: str
    type: str  # TEXT INTEGER REAL BOOLEAN DATE TIMESTAMP JSON
    required: bool = True
    unit: str | None = None
    allowed: tuple[str, ...] | None = None
    unique: bool = False
    fk: str | None = None  # "table.column"
    pattern: str | None = None  # key of ids.PATTERNS
    min: float | None = None
    max: float | None = None
    derived: str | None = None  # how the value is calculated from other fields
    rule: str = ""
    desc: str = ""


@dataclass(frozen=True)
class Table:
    name: str
    kind: str  # MASTER, BRIDGE, REFERENCE, TRANSACTION, EVENT, DERIVED
    pk: tuple[str, ...]
    cols: tuple[Col, ...]
    desc: str
    unique_together: tuple[tuple[str, ...], ...] = ()
    exactly_one_of: tuple[tuple[str, ...], ...] = ()
    at_most_one_of: tuple[tuple[str, ...], ...] = ()

    def col(self, name: str) -> Col:
        for c in self.cols:
            if c.name == name:
                return c
        raise KeyError(name)

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.cols]


LIFECYCLE = ("INSTALLED", "ACTIVE", "MAINTENANCE", "RETIRED")
ALARM_TYPES = (
    "LEAK_SUSPECTED",
    "BLOCKAGE_SUSPECTED",
    "PUMP_DEGRADATION",
    "LEVEL_SENSOR_FAULT",
    "FLOW_SENSOR_FAULT",
    "MASS_BALANCE_UNEXPLAINED",
    "DQ_LEVEL_EXCEEDS_CAPACITY",
)


def _install(desc: str = "Date the component was installed") -> Col:
    return Col("install_date", "DATE", desc=desc, rule="facility.commissioned_date <= install_date")


def _status() -> Col:
    return Col(
        "status",
        "TEXT",
        allowed=LIFECYCLE,
        derived="to_status of the latest component_status_event at REFERENCE_DATE",
        desc="Current lifecycle state",
    )


TABLES: tuple[Table, ...] = (
    Table(
        "facility",
        "MASTER",
        ("facility_id",),
        (
            Col("facility_id", "TEXT", unique=True, pattern="FAC", desc="Facility identifier"),
            Col("name", "TEXT", unique=True, desc="Facility name"),
            Col(
                "facility_type",
                "TEXT",
                allowed=("WATER_TREATMENT", "PROCESS_PLANT", "UTILITY"),
                desc="Kind of site",
            ),
            Col("region", "TEXT", desc="Region label (synthetic)"),
            Col("timezone", "TEXT", allowed=("UTC",), desc="All timestamps are UTC"),
            Col("commissioned_date", "DATE", desc="Date the site went live"),
            Col("status", "TEXT", allowed=("ACTIVE", "INACTIVE"), desc="Site state"),
        ),
        "A plant site. Every component belongs to exactly one facility.",
    ),
    Table(
        "node",
        "MASTER",
        ("node_id",),
        (
            Col("node_id", "TEXT", unique=True, pattern="NODE", desc="Pump, tank or valve ID"),
            Col("node_type", "TEXT", allowed=("PUMP", "TANK", "VALVE"), desc="Node subtype"),
            Col("facility_id", "TEXT", fk="facility.facility_id", desc="Owning facility"),
        ),
        "Supertype of every graph node, so that pipes and sensors can hold real foreign keys.",
    ),
    Table(
        "tank",
        "MASTER",
        ("tank_id",),
        (
            Col("tank_id", "TEXT", unique=True, pattern="TANK", fk="node.node_id", desc="Tank ID"),
            Col("facility_id", "TEXT", fk="facility.facility_id", desc="Owning facility"),
            Col(
                "tank_role",
                "TEXT",
                allowed=("INTAKE", "INTERMEDIATE", "DISTRIBUTION"),
                derived="INTAKE = has the inlet valve; DISTRIBUTION = has an outlet valve",
                desc="Position in the process",
            ),
            Col("height_m", "REAL", unit="m", min=1, max=30, desc="Usable liquid height"),
            Col("area_m2", "REAL", unit="m2", min=1, desc="Cross-section area (vertical walls)"),
            Col(
                "capacity_m3",
                "REAL",
                unit="m3",
                derived="round(area_m2 * height_m, 2)",
                desc="Usable volume",
            ),
            Col("base_elevation_m", "REAL", unit="m", desc="Elevation of the tank floor"),
            Col("low_level_m", "REAL", unit="m", derived="round(0.10 * height_m, 3)", desc="Low"),
            Col("high_level_m", "REAL", unit="m", derived="round(0.90 * height_m, 3)", desc="High"),
            _install(),
            _status(),
        ),
        "Storage tank with vertical walls (volume = area x level).",
    ),
    Table(
        "pump",
        "MASTER",
        ("pump_id",),
        (
            Col("pump_id", "TEXT", unique=True, pattern="PUMP", fk="node.node_id", desc="Pump ID"),
            Col("facility_id", "TEXT", fk="facility.facility_id", desc="Owning facility"),
            Col("model_code", "TEXT", desc="Synthetic model code"),
            Col("pump_type", "TEXT", allowed=("CENTRIFUGAL",), desc="Pump type"),
            Col("rated_flow_m3h", "REAL", unit="m3/h", min=1, desc="Flow at the rated point"),
            Col("rated_head_m", "REAL", unit="m", min=1, desc="Head at the rated point"),
            Col(
                "shutoff_head_m",
                "REAL",
                unit="m",
                rule="shutoff_head_m > rated_head_m",
                desc="Head at zero flow, full speed",
            ),
            Col(
                "curve_k_m_per_m3h2",
                "REAL",
                unit="m/(m3/h)^2",
                derived="(shutoff_head_m - rated_head_m) / rated_flow_m3h^2",
                desc="Curve coefficient: H = shutoff_head * s^2 - k Q^2",
            ),
            Col("bep_efficiency", "REAL", min=0.3, max=0.95, desc="Efficiency at best point"),
            Col(
                "motor_rated_kw",
                "REAL",
                unit="kW",
                derived="ceil(1.15 * rho g Q_rated H_rated / bep_efficiency)",
                desc="Motor rating",
            ),
            Col("has_vfd", "BOOLEAN", desc="Variable-speed drive fitted"),
            _install(),
            _status(),
        ),
        "Centrifugal pump with a quadratic head curve.",
    ),
    Table(
        "valve",
        "MASTER",
        ("valve_id",),
        (
            Col("valve_id", "TEXT", unique=True, pattern="VALVE", fk="node.node_id", desc="Valve"),
            Col("facility_id", "TEXT", fk="facility.facility_id", desc="Owning facility"),
            Col("valve_type", "TEXT", allowed=("GATE", "CONTROL"), desc="Isolation or modulating"),
            Col(
                "valve_role",
                "TEXT",
                allowed=("INLET", "OUTLET", "SUCTION", "DISCHARGE", "GRAVITY_CONTROL"),
                rule="must agree with the topology (see validation: topology_roles)",
                desc="Function in the network",
            ),
            Col("size_mm", "INTEGER", unit="mm", min=25, max=600, desc="Nominal size"),
            Col("kv_m3h", "REAL", unit="m3/h", min=1, desc="Flow at 1 bar drop, fully open"),
            Col("normal_state", "TEXT", allowed=("OPEN", "CLOSED"), desc="Normal position"),
            Col(
                "design_flow_m3h",
                "REAL",
                required=False,
                unit="m3/h",
                rule="required for INLET (max supply) and OUTLET (base demand)",
                desc="Boundary flow",
            ),
            _install(),
            _status(),
        ),
        "Valve. INLET valves take water from outside the plant; OUTLET valves deliver it.",
    ),
    Table(
        "pipe",
        "MASTER",
        ("pipe_id",),
        (
            Col("pipe_id", "TEXT", unique=True, pattern="PIPE", desc="Pipe ID"),
            Col("facility_id", "TEXT", fk="facility.facility_id", desc="Owning facility"),
            Col("from_node_id", "TEXT", fk="node.node_id", desc="Upstream node"),
            Col("to_node_id", "TEXT", fk="node.node_id", rule="!= from_node_id", desc="Downstream"),
            Col("length_m", "REAL", unit="m", min=0.5, desc="Length"),
            Col("diameter_mm", "INTEGER", unit="mm", min=25, max=600, desc="Inner diameter"),
            Col("darcy_friction_factor", "REAL", min=0.005, max=0.08, desc="Darcy friction f"),
            Col(
                "resistance_m_per_m3h2",
                "REAL",
                unit="m/(m3/h)^2",
                derived="f (L/D) / 2g / (A * 3600)^2 (Darcy-Weisbach)",
                desc="Head loss coefficient",
            ),
            _install(),
            _status(),
        ),
        "Directed edge between two nodes of the same facility (flow direction).",
        unique_together=(("from_node_id", "to_node_id"),),
    ),
    Table(
        "sensor",
        "MASTER",
        ("sensor_id",),
        (
            Col("sensor_id", "TEXT", unique=True, pattern="SNS", desc="Sensor ID"),
            Col("facility_id", "TEXT", fk="facility.facility_id", desc="Owning facility"),
            Col(
                "measurement_type",
                "TEXT",
                allowed=("LEVEL", "FLOW", "DIFF_PRESSURE"),
                desc="What is measured",
            ),
            Col(
                "unit",
                "TEXT",
                allowed=("m", "m3/h", "kPa"),
                rule="LEVEL->m, FLOW->m3/h, DIFF_PRESSURE->kPa",
                desc="Engineering unit",
            ),
            Col(
                "attached_node_id",
                "TEXT",
                required=False,
                fk="node.node_id",
                desc="Tank or pump the sensor sits on",
            ),
            Col(
                "attached_pipe_id",
                "TEXT",
                required=False,
                fk="pipe.pipe_id",
                desc="Pipe a flow meter sits on",
            ),
            Col("sensor_role", "TEXT", allowed=("PRIMARY", "REDUNDANT"), desc="Redundancy role"),
            Col("range_min", "REAL", desc="Instrument span low (sensor unit)"),
            Col("range_max", "REAL", rule="range_max > range_min", desc="Span high (sensor unit)"),
            Col("accuracy_pct", "REAL", unit="% of reading", min=0, max=5, desc="Rated accuracy"),
            _install(),
            Col("calibration_due_date", "DATE", rule="> install_date", desc="Next calibration"),
            _status(),
        ),
        "Instrument. LEVEL on tanks, DIFF_PRESSURE on pumps, FLOW on pipes.",
        exactly_one_of=(("attached_node_id", "attached_pipe_id"),),
    ),
    Table(
        "operating_mode",
        "MASTER",
        ("mode_id",),
        (
            Col("mode_id", "TEXT", unique=True, pattern="MODE", desc="Mode ID"),
            Col("facility_id", "TEXT", fk="facility.facility_id", desc="Owning facility"),
            Col(
                "mode_code",
                "TEXT",
                allowed=("NORMAL", "HIGH_DEMAND", "LOW_DEMAND", "MAINTENANCE"),
                desc="Mode name",
            ),
            Col("description", "TEXT", desc="What the mode does"),
            Col("demand_factor", "REAL", min=0.1, max=2.0, desc="Multiplier on base demand"),
            Col(
                "maintenance_pump_id",
                "TEXT",
                required=False,
                fk="pump.pump_id",
                rule="required when mode_code = MAINTENANCE",
                desc="Pump isolated in this mode",
            ),
            Col("effective_from", "DATE", desc="First day the mode may be used"),
            Col("effective_to", "DATE", required=False, rule=">= effective_from", desc="Last day"),
        ),
        "Named operating configuration. Settings live in the two bridge tables.",
        unique_together=(("facility_id", "mode_code"),),
    ),
    Table(
        "operating_mode_valve_setting",
        "BRIDGE",
        ("mode_id", "valve_id"),
        (
            Col("mode_id", "TEXT", fk="operating_mode.mode_id", desc="Mode"),
            Col("valve_id", "TEXT", fk="valve.valve_id", rule="same facility as mode", desc="Valve"),
            Col("opening_pct", "REAL", unit="%", min=0, max=100, desc="Opening (limit for CONTROL)"),
            Col(
                "valve_state",
                "TEXT",
                allowed=("OPEN", "CLOSED", "THROTTLED"),
                derived="0 -> CLOSED, 100 -> OPEN, otherwise THROTTLED",
                desc="Named state",
            ),
        ),
        "N:M between operating modes and valves.",
    ),
    Table(
        "operating_mode_pump_setting",
        "BRIDGE",
        ("mode_id", "pump_id"),
        (
            Col("mode_id", "TEXT", fk="operating_mode.mode_id", desc="Mode"),
            Col("pump_id", "TEXT", fk="pump.pump_id", rule="same facility as mode", desc="Pump"),
            Col("enabled", "BOOLEAN", desc="Pump may run in this mode"),
            Col("speed_pct", "REAL", unit="%", min=50, max=100, desc="Drive speed"),
        ),
        "N:M between operating modes and pumps.",
    ),
    Table(
        "simulation_assumption",
        "REFERENCE",
        ("assumption_id",),
        (
            Col("assumption_id", "TEXT", unique=True, pattern="ASM", desc="Assumption ID"),
            Col("category", "TEXT", desc="Topic"),
            Col("statement", "TEXT", desc="The assumption in plain words"),
            Col("value", "TEXT", required=False, desc="Parameter value, if any"),
            Col("unit", "TEXT", required=False, desc="Unit of value"),
        ),
        "Modelling assumptions referenced by simulation runs.",
    ),
    Table(
        "simulation_run",
        "TRANSACTION",
        ("simulation_run_id",),
        (
            Col("simulation_run_id", "TEXT", unique=True, pattern="SIM", desc="Run ID"),
            Col("facility_id", "TEXT", fk="facility.facility_id", desc="Simulated facility"),
            Col("run_type", "TEXT", allowed=("BASELINE", "WHATIF"), desc="Dataset run or what-if"),
            Col("start_ts", "TIMESTAMP", desc="Start of first interval (UTC)"),
            Col("end_ts", "TIMESTAMP", derived="start_ts + steps * timestep_s", desc="End (UTC)"),
            Col("timestep_s", "INTEGER", unit="s", allowed=None, min=60, desc="Telemetry interval"),
            Col("steps", "INTEGER", min=1, desc="Number of intervals"),
            Col("seed", "INTEGER", desc="Seed for this run's noise and demand"),
            Col("model_version", "TEXT", desc="Simulator version"),
            Col(
                "input_config_json",
                "JSON",
                rule="required: a run without its input configuration cannot be reproduced",
                desc="Operating configuration: mode schedule, initial levels, demand, overrides",
            ),
            Col(
                "config_hash",
                "TEXT",
                derived="sha256 of input_config_json",
                desc="Fingerprint of the input configuration",
            ),
            Col(
                "output_hash",
                "TEXT",
                required=False,
                desc="sha256 of the simulator's clean telemetry (before data-quality injections)",
            ),
            Col(
                "output_summary_json",
                "JSON",
                derived="measured totals from this run's telemetry (see summary.py)",
                desc="Measured outcomes",
            ),
            Col("status", "TEXT", allowed=("COMPLETED", "FAILED"), desc="Run state"),
        ),
        "One simulation of one facility. Fault injections are NOT stored here (ground truth).",
    ),
    Table(
        "simulation_run_assumption",
        "BRIDGE",
        ("simulation_run_id", "assumption_id"),
        (
            Col("simulation_run_id", "TEXT", fk="simulation_run.simulation_run_id", desc="Run"),
            Col("assumption_id", "TEXT", fk="simulation_assumption.assumption_id", desc="Used"),
        ),
        "N:M between runs and the assumptions they relied on.",
    ),
    Table(
        "simulation_run_mode_segment",
        "EVENT",
        ("simulation_run_id", "segment_seq"),
        (
            Col("simulation_run_id", "TEXT", fk="simulation_run.simulation_run_id", desc="Run"),
            Col("segment_seq", "INTEGER", min=1, desc="Order within the run"),
            Col("mode_id", "TEXT", fk="operating_mode.mode_id", rule="same facility", desc="Mode"),
            Col("start_step", "INTEGER", min=1, desc="First interval in this mode"),
            Col("end_step", "INTEGER", rule=">= start_step", desc="Last interval in this mode"),
            Col("start_ts", "TIMESTAMP", desc="Segment start"),
            Col("end_ts", "TIMESTAMP", desc="Segment end"),
        ),
        "Operating-mode schedule of a run. Segments are contiguous and cover the run.",
    ),
    Table(
        "telemetry",
        "TRANSACTION",
        ("telemetry_id",),
        (
            Col("telemetry_id", "TEXT", unique=True, pattern="TEL", desc="Reading ID"),
            Col("simulation_run_id", "TEXT", fk="simulation_run.simulation_run_id", desc="Run"),
            Col("sensor_id", "TEXT", fk="sensor.sensor_id", desc="Sensor"),
            Col("ts_utc", "TIMESTAMP", rule="start_ts < ts_utc <= end_ts", desc="Interval end"),
            Col("step", "INTEGER", min=1, desc="Interval number"),
            Col(
                "value",
                "REAL",
                rule="LEVEL readings must be <= tank height (data-quality rule DQ-001)",
                desc="FLOW/DIFF_PRESSURE: interval mean; LEVEL: value at interval end",
            ),
            Col("unit", "TEXT", allowed=("m", "m3/h", "kPa"), rule="= sensor.unit", desc="Unit"),
            Col(
                "quality_flag",
                "TEXT",
                allowed=("GOOD", "BAD_RANGE"),
                derived="BAD_RANGE when value is outside the sensor span",
                desc="Instrument quality",
            ),
        ),
        "Sensor readings. Contains no labels.",
        unique_together=(("simulation_run_id", "sensor_id", "ts_utc"),),
    ),
    Table(
        "pump_state_event",
        "EVENT",
        ("event_id",),
        (
            Col("event_id", "TEXT", unique=True, pattern="PSE", desc="Event ID"),
            Col("simulation_run_id", "TEXT", fk="simulation_run.simulation_run_id", desc="Run"),
            Col("pump_id", "TEXT", fk="pump.pump_id", desc="Pump"),
            Col("ts_utc", "TIMESTAMP", desc="When the state took effect"),
            Col("from_state", "TEXT", required=False, allowed=("ON", "OFF"), desc="Before"),
            Col("to_state", "TEXT", allowed=("ON", "OFF"), rule="!= from_state", desc="After"),
            Col(
                "reason",
                "TEXT",
                allowed=(
                    "INITIAL",
                    "LEVEL_LOW_START",
                    "LEVEL_HIGH_STOP",
                    "SUCTION_LOW_STOP",
                    "MODE_DISABLED",
                ),
                desc="Control rule that fired",
            ),
        ),
        "Pump run/stop history (SCADA run status). Append-only.",
    ),
    Table(
        "valve_state_event",
        "EVENT",
        ("event_id",),
        (
            Col("event_id", "TEXT", unique=True, pattern="VSE", desc="Event ID"),
            Col("simulation_run_id", "TEXT", fk="simulation_run.simulation_run_id", desc="Run"),
            Col("valve_id", "TEXT", fk="valve.valve_id", desc="Valve"),
            Col("ts_utc", "TIMESTAMP", desc="When the setting took effect"),
            Col("from_opening_pct", "REAL", required=False, unit="%", desc="Before"),
            Col("to_opening_pct", "REAL", unit="%", min=0, max=100, desc="After"),
            Col("from_state", "TEXT", required=False, allowed=("OPEN", "CLOSED", "THROTTLED")),
            Col("to_state", "TEXT", allowed=("OPEN", "CLOSED", "THROTTLED"), desc="After"),
            Col("reason", "TEXT", allowed=("INITIAL", "MODE_CHANGE", "OVERRIDE"), desc="Cause"),
        ),
        "Valve setting history. Append-only.",
    ),
    Table(
        "component_status_event",
        "EVENT",
        ("event_id",),
        (
            Col("event_id", "TEXT", unique=True, pattern="CSE", desc="Event ID"),
            Col("component_type", "TEXT", allowed=("PUMP", "TANK", "VALVE", "PIPE", "SENSOR")),
            Col("node_id", "TEXT", required=False, fk="node.node_id", desc="If a node"),
            Col("pipe_id", "TEXT", required=False, fk="pipe.pipe_id", desc="If a pipe"),
            Col("sensor_id", "TEXT", required=False, fk="sensor.sensor_id", desc="If a sensor"),
            Col("from_status", "TEXT", required=False, allowed=LIFECYCLE, desc="Before"),
            Col("to_status", "TEXT", allowed=LIFECYCLE, desc="After"),
            Col("effective_at", "TIMESTAMP", desc="When the change took effect"),
            Col(
                "reason",
                "TEXT",
                allowed=(
                    "INSTALLATION",
                    "COMMISSIONING",
                    "PLANNED_MAINTENANCE",
                    "RETURN_TO_SERVICE",
                ),
            ),
            Col(
                "simulation_run_id",
                "TEXT",
                required=False,
                fk="simulation_run.simulation_run_id",
                desc="Set when the change happened inside a run",
            ),
        ),
        "Lifecycle history. Allowed: none->INSTALLED->ACTIVE<->MAINTENANCE, ->RETIRED.",
        exactly_one_of=(("node_id", "pipe_id", "sensor_id"),),
    ),
    Table(
        "alarm",
        "DERIVED",
        ("alarm_id",),
        (
            Col("alarm_id", "TEXT", unique=True, pattern="ALM", desc="Alarm ID"),
            Col("simulation_run_id", "TEXT", fk="simulation_run.simulation_run_id", desc="Run"),
            Col("facility_id", "TEXT", fk="facility.facility_id", desc="Facility"),
            Col("alarm_source", "TEXT", allowed=("DETECTOR", "OPERATOR"), desc="Who raised it"),
            Col("alarm_type", "TEXT", allowed=ALARM_TYPES, desc="Diagnosis"),
            Col("severity", "TEXT", allowed=("LOW", "MEDIUM", "HIGH"), desc="Severity"),
            Col("rule_id", "TEXT", required=False, rule="required for DETECTOR", desc="Rule"),
            Col("node_id", "TEXT", required=False, fk="node.node_id", desc="Main component"),
            Col("pipe_id", "TEXT", required=False, fk="pipe.pipe_id", desc="Main pipe"),
            Col("sensor_id", "TEXT", required=False, fk="sensor.sensor_id", desc="Main sensor"),
            Col(
                "telemetry_id",
                "TEXT",
                required=False,
                fk="telemetry.telemetry_id",
                desc="Offending reading (data-quality alarms)",
            ),
            Col(
                "suspected_components",
                "TEXT",
                required=False,
                desc="Semicolon-separated IDs on the suspected path",
            ),
            Col("raised_at", "TIMESTAMP", desc="When the rule first held"),
            Col("cleared_at", "TIMESTAMP", required=False, rule=">= raised_at", desc="When ended"),
            Col("state", "TEXT", allowed=("ACTIVE", "CLEARED"), desc="Current state"),
            Col("evidence_value", "REAL", required=False, desc="Peak rule statistic"),
            Col("threshold_value", "REAL", required=False, desc="Rule threshold"),
            Col("evidence_unit", "TEXT", required=False, desc="Unit of evidence/threshold"),
            Col("message", "TEXT", desc="Human-readable explanation"),
        ),
        "Alarms. DETECTOR alarms are derived from telemetry by documented rules; OPERATOR "
        "alarms are manual log entries (inputs).",
        at_most_one_of=(("node_id", "pipe_id", "sensor_id"),),
    ),
    Table(
        "alarm_event",
        "EVENT",
        ("event_id",),
        (
            Col("event_id", "TEXT", unique=True, pattern="AEV", desc="Event ID"),
            Col("alarm_id", "TEXT", fk="alarm.alarm_id", desc="Alarm"),
            Col("ts_utc", "TIMESTAMP", desc="When"),
            Col("event_type", "TEXT", allowed=("RAISED", "CLEARED"), desc="Transition"),
            Col("from_state", "TEXT", required=False, allowed=("ACTIVE",), desc="Before"),
            Col("to_state", "TEXT", allowed=("ACTIVE", "CLEARED"), desc="After"),
        ),
        "Alarm lifecycle history. Append-only.",
    ),
    Table(
        "alarm_review",
        "DERIVED",
        ("review_id",),
        (
            Col("review_id", "TEXT", unique=True, pattern="ARV", desc="Review ID"),
            Col("alarm_id", "TEXT", fk="alarm.alarm_id", desc="Operator alarm reviewed"),
            Col(
                "decision",
                "TEXT",
                allowed=("CONFIRMED", "RECLASSIFY_AS_SENSOR_FAULT", "UNCONFIRMED"),
                desc="Outcome of comparing with detector evidence",
            ),
            Col("supporting_alarm_ids", "TEXT", required=False, desc="Detector alarms used"),
            Col("explanation", "TEXT", desc="Why"),
        ),
        "Automatic review of operator alarms against detector evidence.",
    ),
)

TABLE_BY_NAME = {t.name: t for t in TABLES}

# Ground truth is kept apart from the input tables on purpose.
GROUND_TRUTH_COLUMNS = (
    "gt_id",
    "category",
    "issue_type",
    "simulation_run_id",
    "facility_id",
    "component_id",
    "sensor_id",
    "record_table",
    "record_id",
    "onset_step",
    "onset_ts",
    "severity",
    "expected_alarm_type",
    "expected_decision",
    "rule_basis",
)

LABEL_WORDS = ("ground_truth", "issue_type", "scenario", "fault", "label", "expected_decision")


def check_pattern(col: Col, value: str) -> bool:
    return col.pattern is None or ids.matches(col.pattern, value)
