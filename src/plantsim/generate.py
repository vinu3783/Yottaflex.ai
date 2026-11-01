"""End-to-end dataset generation.

Order: master data -> scenario plan -> simulation -> labelled edge cases -> derived values
-> export inputs -> detector (reads the exported CSVs) -> alarms -> JSON/SQL -> validation
-> evaluation -> manifest.
"""

from __future__ import annotations

import json
import random
import shutil
from datetime import timedelta
from pathlib import Path

from plantsim import MODEL_VERSION, __version__, config, ids, io, masterdata, physics, topology
from plantsim.detector import MB_REL_TOL, THRESHOLDS, detect
from plantsim.scenarios import build_plan, canonical_json, config_hash
from plantsim.schema import GROUND_TRUTH_COLUMNS, TABLES
from plantsim.simulator import mode_settings, parse_ts, simulate, step_ts
from plantsim.summary import run_summary

ASSUMPTIONS = (
    ("HYDRAULICS", "Flow is quasi-steady within each integration substep", "60", "s"),
    ("HYDRAULICS", "Water density 1000 kg/m3 and g = 9.80665 m/s2", "9.80665", "kPa/m"),
    (
        "PUMPS",
        "Quadratic pump curve H = H0*s^2 - k*Q^2 with affinity-law speed scaling",
        None,
        None,
    ),
    ("PUMPS", "Efficiency is parabolic around the rated point (floor 20 %)", "0.20", None),
    ("PIPES", "Darcy-Weisbach losses with a constant friction factor per pipe", None, None),
    ("VALVES", "Kv sizing with a linear trim; 0 % opening blocks the flow", None, None),
    ("VALVES", "Check valves prevent reverse flow on every transfer", None, None),
    (
        "TANKS",
        "Vertical walls (volume = area x level); overflow above capacity is lost",
        None,
        None,
    ),
    (
        "CONTROL",
        "Pumps start/stop on destination level at telemetry boundaries",
        "0.45/0.85",
        "fill",
    ),
    ("CONTROL", "Gravity and inlet control valves modulate on level every substep", None, None),
    ("DEMAND", "Diurnal demand profile with one harmonic per outlet", "0.25", "amplitude"),
    (
        "TELEMETRY",
        "Flow and pressure are interval means; level is the interval-end value",
        "900",
        "s",
    ),
    ("TELEMETRY", "Gaussian sensor noise; meters read 0 below the low-flow cut-off", "0.5", "m3/h"),
    ("FAULTS", "Leaks occur downstream of the transfer flow meter and scale with flow", None, None),
)


def build(
    out: Path,
    seed: int = config.DEFAULT_SEED,
    telemetry_points: int = 100_000,
    profile: str = "custom",
) -> dict:
    steps = config.steps_for(telemetry_points)
    if out.exists():
        shutil.rmtree(out)
    (out / "csv").mkdir(parents=True)
    rng = random.Random(f"edge-{seed}")

    t = masterdata.build_master(seed)
    modes = mode_settings(t)
    plants = {f["facility_id"]: topology.build(t, f["facility_id"]) for f in t["facility"]}
    plans = build_plan(seed, steps, plants, modes)

    t["simulation_assumption"] = [
        {"assumption_id": ids.make("ASM", k), "category": c, "statement": s, "value": v, "unit": u}
        for k, (c, s, v, u) in enumerate(ASSUMPTIONS, start=1)
    ]
    for name in (
        "simulation_run",
        "simulation_run_assumption",
        "simulation_run_mode_segment",
        "telemetry",
        "pump_state_event",
        "valve_state_event",
        "alarm",
    ):
        t[name] = []
    cnt = ids.Counter({"CSE": len(t["component_status_event"])})
    gt: list[dict] = []
    gt_runs = []

    def add_gt(**kw):
        row = {c: kw.get(c) for c in GROUND_TRUTH_COLUMNS}
        row["gt_id"] = ids.make("GT", len(gt) + 1)
        gt.append(row)

    tel_by_run = {}
    for plan in plans:
        plant = plants[plan.facility_id]
        res = simulate(plant, modes, plan.config, plan.faults, plan.seed)
        cfg_json = canonical_json(plan.config)
        start = plan.config["start_ts"]
        dt = plan.config["timestep_s"]
        run = {
            "simulation_run_id": plan.run_id,
            "facility_id": plan.facility_id,
            "run_type": "BASELINE",
            "start_ts": start,
            "end_ts": step_ts(start, steps, dt),
            "timestep_s": dt,
            "steps": steps,
            "seed": plan.seed,
            "model_version": MODEL_VERSION,
            "input_config_json": cfg_json,
            "config_hash": config_hash(cfg_json),
            "output_hash": res.output_hash,
            "output_summary_json": None,  # filled after edge-case injection
            "status": "COMPLETED",
        }
        t["simulation_run"].append(run)
        for a in t["simulation_assumption"]:
            t["simulation_run_assumption"].append(
                {"simulation_run_id": plan.run_id, "assumption_id": a["assumption_id"]}
            )
        for k, seg in enumerate(plan.config["mode_schedule"], start=1):
            t["simulation_run_mode_segment"].append(
                {
                    "simulation_run_id": plan.run_id,
                    "segment_seq": k,
                    "mode_id": seg["mode_id"],
                    "start_step": seg["start_step"],
                    "end_step": seg["end_step"],
                    "start_ts": step_ts(start, seg["start_step"] - 1, dt),
                    "end_ts": step_ts(start, seg["end_step"], dt),
                }
            )
            mp = modes[seg["mode_id"]].maintenance_pump_id
            if mp:  # lifecycle history: the pump is in MAINTENANCE for the segment
                for ts, frm, to, why in (
                    (
                        step_ts(start, seg["start_step"] - 1, dt),
                        "ACTIVE",
                        "MAINTENANCE",
                        "PLANNED_MAINTENANCE",
                    ),
                    (
                        step_ts(start, seg["end_step"], dt),
                        "MAINTENANCE",
                        "ACTIVE",
                        "RETURN_TO_SERVICE",
                    ),
                ):
                    t["component_status_event"].append(
                        masterdata.status_event_row(
                            cnt.next("CSE"), "PUMP", mp, frm, to, ts, why, plan.run_id
                        )
                    )
        rows = []
        for step, sid, val in res.telemetry:
            rows.append(
                {
                    "telemetry_id": None,
                    "simulation_run_id": plan.run_id,
                    "sensor_id": sid,
                    "ts_utc": step_ts(start, step, dt),
                    "step": step,
                    "value": val,
                    "unit": plant.sensors[sid]["unit"],
                    "quality_flag": None,
                }
            )
        tel_by_run[plan.run_id] = rows
        t["telemetry"].extend(rows)
        for step, pid, frm, to, why in res.pump_events:
            t["pump_state_event"].append(
                {
                    "event_id": cnt.next("PSE"),
                    "simulation_run_id": plan.run_id,
                    "pump_id": pid,
                    "ts_utc": step_ts(start, step - 1, dt),
                    "from_state": frm,
                    "to_state": to,
                    "reason": why,
                }
            )
        for step, vid, frm, to, why in res.valve_events:
            t["valve_state_event"].append(
                {
                    "event_id": cnt.next("VSE"),
                    "simulation_run_id": plan.run_id,
                    "valve_id": vid,
                    "ts_utc": step_ts(start, step - 1, dt),
                    "from_opening_pct": frm,
                    "to_opening_pct": to,
                    "from_state": None if frm is None else masterdata.valve_state(frm),
                    "to_state": masterdata.valve_state(to),
                    "reason": why,
                }
            )

        # Ground truth for the scenario (never written to the input tables)
        if not plan.faults:
            add_gt(
                category="NONE",
                issue_type="NO_FAULT",
                simulation_run_id=plan.run_id,
                facility_id=plan.facility_id,
                record_table="simulation_run",
                record_id=plan.run_id,
                expected_decision="NO_ALARM",
                rule_basis="Baseline run with operating-mode changes only; no fault injected",
            )
        for f in plan.faults:
            onset_ts = step_ts(start, f["onset_step"] - 1, dt)
            common = dict(
                simulation_run_id=plan.run_id,
                facility_id=plan.facility_id,
                onset_step=f["onset_step"],
                onset_ts=onset_ts,
            )
            if f["type"] == "LEAK":
                detectable = f["leak_fraction"] >= MB_REL_TOL
                decision = "ALARM" if detectable else "NO_ALARM"
                add_gt(
                    category="PROCESS_FAULT",
                    issue_type="LEAK",
                    component_id=f["pipe_id"],
                    record_table="pipe",
                    record_id=f["pipe_id"],
                    severity=f["leak_fraction"],
                    expected_alarm_type="LEAK_SUSPECTED" if detectable else None,
                    expected_decision=decision,
                    rule_basis=(
                        f"leak_fraction {f['leak_fraction']} vs mass-balance tolerance {MB_REL_TOL}: "
                        + (
                            "above, so it must be detected"
                            if detectable
                            else "below, so no alarm is expected"
                        )
                    ),
                    **common,
                )
                add_gt(
                    category="EDGE_CASE",
                    issue_type="FLOW_CONSERVATION_VIOLATED"
                    if detectable
                    else "FLOW_CONSERVATION_WITHIN_TOLERANCE",
                    component_id=f["tank_id"],
                    record_table="tank",
                    record_id=f["tank_id"],
                    severity=f["leak_fraction"],
                    expected_alarm_type="LEAK_SUSPECTED" if detectable else None,
                    expected_decision=decision,
                    rule_basis=f"inflow into {f['tank_id']} minus storage change differs by "
                    f"{f['leak_fraction']:.0%} of measured inflow (tolerance {MB_REL_TOL:.0%})",
                    **common,
                )
            elif f["type"] == "BLOCKAGE":
                add_gt(
                    category="PROCESS_FAULT",
                    issue_type="BLOCKAGE",
                    component_id=f["pipe_id"],
                    record_table="pipe",
                    record_id=f["pipe_id"],
                    severity=f["target_flow_ratio"],
                    expected_alarm_type="BLOCKAGE_SUSPECTED",
                    expected_decision="ALARM",
                    rule_basis=f"resistance x{f['resistance_multiplier']} cuts design flow to "
                    f"{f['target_flow_ratio']:.0%} (< {THRESHOLDS['BLOCK_RATIO']:.0%} threshold)",
                    **common,
                )
            elif f["type"] == "PUMP_DEGRADATION":
                add_gt(
                    category="PROCESS_FAULT",
                    issue_type="PUMP_DEGRADATION",
                    component_id=f["pump_id"],
                    record_table="pump",
                    record_id=f["pump_id"],
                    severity=f["final_health"],
                    expected_alarm_type="PUMP_DEGRADATION",
                    expected_decision="ALARM",
                    rule_basis=f"shut-off head falls linearly to {f['final_health']:.0%} by run end",
                    **common,
                )
            else:
                atype = "FLOW_SENSOR_FAULT" if f["fault_type"] == "BIAS" else "LEVEL_SENSOR_FAULT"
                add_gt(
                    category="SENSOR_FAULT",
                    issue_type=f"SENSOR_{f['fault_type']}",
                    component_id=f["sensor_id"],
                    sensor_id=f["sensor_id"],
                    record_table="sensor",
                    record_id=f["sensor_id"],
                    severity=f.get("bias_fraction", f.get("drift_m_per_h")),
                    expected_alarm_type=atype,
                    expected_decision="ALARM",
                    rule_basis=f"{f['fault_type']} applied to {f['sensor_id']} readings from onset; "
                    "the process itself is healthy",
                    **common,
                )
        gt_runs.append(
            {
                "simulation_run_id": plan.run_id,
                "facility_id": plan.facility_id,
                "scenario": plan.scenario,
                "faults": plan.faults,
                "true_outcomes": res.truth,
            }
        )

    # ---------------- labelled edge cases
    plan_by_run = {p.run_id: p for p in plans}

    # 1. Tank level above capacity (data-quality): non-redundant primary level sensors
    faulted = {f.get("sensor_id") for p in plans for f in p.faults}
    candidates = []
    for p in plans:
        if p.scenario not in ("NORMAL", "BLOCKAGE", "PUMP_DEGRADATION"):
            continue
        plant = plants[p.facility_id]
        lone = {s[0] for s in plant.level_sensors.values() if len(s) == 1} - faulted
        candidates += [
            r for r in tel_by_run[p.run_id] if r["sensor_id"] in lone and 2 < r["step"] < steps - 1
        ]
    candidates.sort(key=lambda r: (r["simulation_run_id"], r["step"], r["sensor_id"]))
    for r in rng.sample(candidates, config.LEVEL_OVER_CAPACITY_CASES):
        plant = plants[plan_by_run[r["simulation_run_id"]].facility_id]
        tank_id = plant.sensors[r["sensor_id"]]["attached_node_id"]
        h = plant.tanks[tank_id].height
        r["value"] = round(h * rng.uniform(1.05, 1.18), 3)
        r["_gt"] = dict(
            category="EDGE_CASE",
            issue_type="TANK_LEVEL_EXCEEDS_CAPACITY",
            simulation_run_id=r["simulation_run_id"],
            facility_id=plant.facility_id,
            component_id=tank_id,
            sensor_id=r["sensor_id"],
            record_table="telemetry",
            onset_step=r["step"],
            onset_ts=r["ts_utc"],
            severity=round(r["value"] / h, 3),
            expected_alarm_type="DQ_LEVEL_EXCEEDS_CAPACITY",
            expected_decision="FLAG_DATA_QUALITY",
            rule_basis=f"reading {r['value']} m > tank height {h} m (impossible: tank overflows first)",
        )

    # Telemetry IDs are assigned after injection so they stay sequential and immutable.
    for n, r in enumerate(t["telemetry"], start=1):
        r["telemetry_id"] = ids.make("TEL", n)
        sensor = plants[plan_by_run[r["simulation_run_id"]].facility_id].sensors[r["sensor_id"]]
        r["quality_flag"] = (
            "GOOD" if sensor["range_min"] <= r["value"] <= sensor["range_max"] else "BAD_RANGE"
        )
        if "_gt" in r:
            add_gt(record_id=r["telemetry_id"], **r.pop("_gt"))

    # 2. Pipe references a node that does not exist
    fac2 = sorted(plants)[1]
    outlet = plants[fac2].outlets[0]
    bad_pipe = dict(next(p for p in t["pipe"] if p["pipe_id"] == outlet.pipe_id))
    bad_pipe.update(
        pipe_id=ids.make("PIPE", len(t["pipe"]) + 1),
        from_node_id=outlet.valve_id,
        to_node_id=ids.make("TANK", len(t["tank"]) + 1),
        length_m=42.0,
    )
    bad_pipe["resistance_m_per_m3h2"] = round(
        physics.pipe_resistance(42.0, bad_pipe["diameter_mm"], bad_pipe["darcy_friction_factor"]),
        12,
    )
    t["pipe"].append(bad_pipe)
    # The bad record arrives with normal-looking history; only its node reference is wrong.
    for ts, frm, to, why in (
        (f"{bad_pipe['install_date']}T08:00:00Z", None, "INSTALLED", "INSTALLATION"),
        (f"{bad_pipe['install_date']}T09:00:00Z", "INSTALLED", "ACTIVE", "COMMISSIONING"),
    ):
        t["component_status_event"].append(
            masterdata.status_event_row(cnt.next("CSE"), "PIPE", bad_pipe["pipe_id"], frm, to, ts, why, None)
        )
    add_gt(
        category="EDGE_CASE",
        issue_type="PIPE_REFERENCES_MISSING_NODE",
        facility_id=fac2,
        component_id=bad_pipe["pipe_id"],
        record_table="pipe",
        record_id=bad_pipe["pipe_id"],
        expected_decision="REJECT_RECORD",
        rule_basis=f"to_node_id {bad_pipe['to_node_id']} does not exist in node",
    )

    # 3. Operator alarms (inputs); one of them mislabels a sensor fault as a process fault
    alarm_no = 0
    for p in plans:
        for f in p.faults:
            right = f["type"] == "LEAK" and f["leak_fraction"] == max(config.LEAK_FRACTIONS)
            wrong = f["type"] == "SENSOR_FAULT" and f["fault_type"] == "BIAS"
            if not (right or wrong):
                continue
            alarm_no += 1
            aid = ids.make("ALM", alarm_no)
            raised = step_ts(p.config["start_ts"], min(steps, f["onset_step"] + 12), p.config["timestep_s"])
            t["alarm"].append(
                {
                    "alarm_id": aid,
                    "simulation_run_id": p.run_id,
                    "facility_id": p.facility_id,
                    "alarm_source": "OPERATOR",
                    "alarm_type": "LEAK_SUSPECTED",
                    "severity": "HIGH",
                    "rule_id": None,
                    "node_id": f["tank_id"],
                    "pipe_id": None,
                    "sensor_id": None,
                    "telemetry_id": None,
                    "suspected_components": f["tank_id"],
                    "raised_at": raised,
                    "cleared_at": None,
                    "state": "ACTIVE",
                    "evidence_value": None,
                    "threshold_value": None,
                    "evidence_unit": None,
                    "message": f"Operator log: {f['tank_id']} filling slower than the meter suggests",
                }
            )
            add_gt(
                category="EDGE_CASE" if wrong else "LABEL_CHECK",
                issue_type="SENSOR_FAULT_LABELLED_AS_PROCESS_FAULT" if wrong else "OPERATOR_ALARM_CORRECT",
                simulation_run_id=p.run_id,
                facility_id=p.facility_id,
                component_id=f["tank_id"],
                sensor_id=f.get("sensor_id"),
                record_table="alarm",
                record_id=aid,
                expected_decision="RECLASSIFY_AS_SENSOR_FAULT" if wrong else "CONFIRMED",
                rule_basis=(
                    f"cause is a +{f.get('bias_fraction', 0):.0%} bias on {f.get('sensor_id')}, not a leak"
                    if wrong
                    else f"a {f.get('leak_fraction', 0):.0%} leak really exists on {f.get('pipe_id')}"
                ),
            )

    # Derived run outcomes (from the telemetry as published)
    pev = {}
    for e in t["pump_state_event"]:
        pev.setdefault(e["simulation_run_id"], []).append(e)
    for run in t["simulation_run"]:
        plant = plants[run["facility_id"]]
        s = run_summary(
            plant,
            run["steps"],
            run["timestep_s"],
            tel_by_run[run["simulation_run_id"]],
            pev.get(run["simulation_run_id"], []),
        )
        run["output_summary_json"] = canonical_json(s)

    # 4. Simulation output without its input configuration
    last = t["simulation_run"][-1]
    orphan_src = next(r for r in t["simulation_run"] if r["facility_id"] == fac2)
    start = parse_ts(last["end_ts"]) + timedelta(hours=config.RUN_SPACING_HOURS)
    orphan_id = ids.make("SIM", len(t["simulation_run"]) + 1)
    t["simulation_run"].append(
        {
            "simulation_run_id": orphan_id,
            "facility_id": fac2,
            "run_type": "BASELINE",
            "start_ts": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_ts": step_ts(start.strftime("%Y-%m-%dT%H:%M:%SZ"), steps, config.STEP_SECONDS),
            "timestep_s": config.STEP_SECONDS,
            "steps": steps,
            "seed": None,
            "model_version": MODEL_VERSION,
            "input_config_json": None,
            "config_hash": None,
            "output_hash": None,
            "output_summary_json": orphan_src["output_summary_json"],
            "status": "COMPLETED",
        }
    )
    add_gt(
        category="EDGE_CASE",
        issue_type="SIMULATION_OUTPUT_WITHOUT_INPUT_CONFIG",
        simulation_run_id=orphan_id,
        facility_id=fac2,
        record_table="simulation_run",
        record_id=orphan_id,
        expected_decision="REJECT_NOT_REPRODUCIBLE",
        rule_basis="output_summary_json present but input_config_json, config_hash and seed are null",
    )

    # ---------------- export inputs, detect from the files, export everything
    input_tables = [x.name for x in TABLES if x.name not in ("alarm_event", "alarm_review")]
    for name in input_tables:
        io.write_csv(out / "csv" / f"{name}.csv", io.table(name), t[name])
    loaded = io.load_dataset(out)
    alarms, events, reviews = detect(loaded)
    t["alarm"] = t["alarm"] + alarms
    t["alarm_event"], t["alarm_review"] = events, reviews
    for name in ("alarm", "alarm_event", "alarm_review"):
        io.write_csv(out / "csv" / f"{name}.csv", io.table(name), t[name])
    for x in TABLES:
        io.write_json(out / "json" / f"{x.name}.json", x, t[x.name])
    (out / "sql").mkdir()
    (out / "sql" / "schema.sql").write_text(io.ddl(), "utf-8")
    sql_report = io.write_seed_sql(out / "sql" / "seed.sql", io.load_dataset(out))

    gtd = out / "ground_truth"
    io.write_plain_csv(gtd / "ground_truth.csv", GROUND_TRUTH_COLUMNS, gt)
    io.dump_json(
        gtd / "ground_truth.json",
        {
            "note": "Labels and fault injections. Never used as detector input.",
            "seed": seed,
            "labels": gt,
            "runs": gt_runs,
        },
    )

    from plantsim import evaluation, validation  # local import: both read the files

    vsum = validation.validate(out)
    io.dump_json(out / "reports" / "validation_summary.json", vsum)
    (out / "reports" / "validation_summary.md").write_text(validation.to_markdown(vsum), "utf-8")
    ev = evaluation.evaluate(out)
    io.dump_json(out / "reports" / "evaluation.json", ev)
    (out / "reports" / "evaluation.md").write_text(evaluation.to_markdown(ev), "utf-8")
    io.dump_json(out / "reports" / "sql_load_report.json", sql_report)

    manifest = write_manifest(out, seed, telemetry_points, steps, profile)
    return {
        "manifest": manifest,
        "validation": vsum["overall_status"],
        "evaluation": ev["headline"],
    }


def write_manifest(out: Path, seed: int, telemetry_points: int, steps: int, profile: str) -> dict:
    files = []
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name != "manifest.json" and not p.name.startswith("._"):
            rel = p.relative_to(out).as_posix()
            rows = None
            if p.suffix == ".csv":
                with p.open(encoding="utf-8") as fh:
                    rows = sum(1 for _ in fh) - 1
            files.append({"path": rel, "rows": rows, "bytes": p.stat().st_size, "sha256": io.sha256_file(p)})
    manifest = {
        "dataset": "plantsim synthetic plant hydraulics",
        "generator_version": __version__,
        "model_version": MODEL_VERSION,
        "profile": profile,
        "seed": seed,
        "telemetry_points": telemetry_points,
        "steps_per_run": steps,
        "command": f"plantsim generate --profile {profile} --seed {seed}"
        if profile in config.PROFILES
        else f"plantsim generate --telemetry-points {telemetry_points} --seed {seed}",
        "reference_date": config.REFERENCE_DATE,
        "provenance": {
            "source": "fully synthetic, produced by this generator",
            "external_data": "none",
            "personal_data": "none",
            "reference_knowledge": [
                "Darcy-Weisbach pipe friction (textbook fluid mechanics)",
                "Centrifugal pump affinity laws (textbook)",
                "Valve flow coefficient Kv definition (IEC 60534 concept, no text reproduced)",
            ],
            "license": "MIT (same as the code)",
        },
        "files": files,
    }
    io.dump_json(out / "manifest.json", manifest)
    return manifest


def load_ground_truth(data_dir: Path) -> dict:
    return json.loads((data_dir / "ground_truth" / "ground_truth.json").read_text("utf-8"))
