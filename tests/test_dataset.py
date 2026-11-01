import json
import sqlite3

from plantsim import io, validation
from plantsim.schema import LABEL_WORDS, TABLES


def test_full_volume_is_exactly_100k_telemetry_points(full_data):
    t = io.load_dataset(full_data)
    assert len(t["telemetry"]) == 100_000
    m = json.loads((full_data / "manifest.json").read_text())
    assert m["seed"] == 20261022
    assert next(f for f in m["files"] if f["path"] == "csv/telemetry.csv")["rows"] == 100_000


def test_validation_passes_with_only_the_labelled_exceptions(full_data):
    v = validation.validate(full_data)
    assert v["overall_status"] == "PASS"
    statuses = {c["check"]: c["status"] for c in v["checks"]}
    assert statuses["foreign_keys"] == "EXPECTED_EXCEPTIONS"
    assert statuses["required_fields"] == "EXPECTED_EXCEPTIONS"
    assert statuses["level_within_capacity"] == "EXPECTED_EXCEPTIONS"
    for name in (
        "primary_key_unique",
        "duplicates",
        "topology_connected",
        "units_explicit",
        "date_ordering",
        "arithmetic_reconciliation",
        "lifecycle_transitions",
        "ground_truth_separation",
    ):
        assert statuses[name] == "PASS", name


def test_ground_truth_never_leaks_into_inputs(full_data):
    for t in TABLES:
        header = io.csv_header(full_data / "csv" / f"{t.name}.csv")
        assert not [h for h in header if any(w in h for w in LABEL_WORDS)]
    runs = io.load_dataset(full_data)["simulation_run"]
    for r in runs:
        cfg = (r["input_config_json"] or "").lower()
        assert "leak" not in cfg and "fault" not in cfg and "blockage" not in cfg


def test_every_required_edge_case_is_labelled(full_data):
    gt = json.loads((full_data / "ground_truth" / "ground_truth.json").read_text())
    issues = {g["issue_type"] for g in gt["labels"]}
    for required in (
        "PIPE_REFERENCES_MISSING_NODE",
        "TANK_LEVEL_EXCEEDS_CAPACITY",
        "FLOW_CONSERVATION_VIOLATED",
        "SIMULATION_OUTPUT_WITHOUT_INPUT_CONFIG",
        "SENSOR_FAULT_LABELLED_AS_PROCESS_FAULT",
    ):
        assert required in issues


def test_sql_seed_loads_into_sqlite_with_foreign_keys_enforced(full_data):
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((full_data / "sql" / "schema.sql").read_text())
    con.executescript((full_data / "sql" / "seed.sql").read_text())
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert con.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0] == 100_000
    rejected = {r[0] + ":" + r[1] for r in con.execute("SELECT table_name, record_key FROM rejected_record")}
    assert "pipe:PIPE-108" in rejected
    assert "simulation_run:SIM-2026-000016" in rejected
    # A join across the model: readings per facility
    n = con.execute(
        "SELECT COUNT(*) FROM telemetry t JOIN sensor s ON s.sensor_id = t.sensor_id "
        "JOIN facility f ON f.facility_id = s.facility_id"
    ).fetchone()[0]
    assert n == 100_000


def test_derived_outputs_reconcile_with_telemetry(full_data):
    v = validation.validate(full_data)
    ar = next(c for c in v["checks"] if c["check"] == "arithmetic_reconciliation")
    assert ar["violations"] == 0
