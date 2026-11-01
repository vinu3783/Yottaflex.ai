"""Dataset validation on the exported CSV files.

Every check lists the records that break it. A check passes when it finds nothing; it is
EXPECTED_EXCEPTIONS when the records it finds are exactly the deliberately injected,
labelled edge cases for that check; anything else (an unlabelled problem, or a labelled
case that is not found) is FAIL.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict, deque
from datetime import date
from pathlib import Path

from plantsim import config, ids, io, physics, topology
from plantsim.scenarios import config_hash
from plantsim.schema import LABEL_WORDS, TABLE_BY_NAME, TABLES
from plantsim.simulator import parse_ts, step_ts
from plantsim.summary import run_summary

ALLOWED_TRANSITIONS = {
    (None, "INSTALLED"),
    ("INSTALLED", "ACTIVE"),
    ("ACTIVE", "MAINTENANCE"),
    ("MAINTENANCE", "ACTIVE"),
    ("ACTIVE", "RETIRED"),
    ("MAINTENANCE", "RETIRED"),
}

# Which labelled edge cases may legitimately appear in which check
EXPECTED_BY_CHECK = {
    "required_fields": ("SIMULATION_OUTPUT_WITHOUT_INPUT_CONFIG",),
    "foreign_keys": ("PIPE_REFERENCES_MISSING_NODE",),
    "topology_dangling_pipes": ("PIPE_REFERENCES_MISSING_NODE",),
    "level_within_capacity": ("TANK_LEVEL_EXCEEDS_CAPACITY", "SENSOR_FAULT_CONSEQUENCE"),
    "run_reproducibility_inputs": ("SIMULATION_OUTPUT_WITHOUT_INPUT_CONFIG",),
}


def _key(table: str, rec) -> str:
    return f"{table}:{rec}"


def validate(data_dir: Path) -> dict:
    t = io.load_dataset(data_dir)
    gt = json.loads((data_dir / "ground_truth" / "ground_truth.json").read_text("utf-8"))
    labelled = defaultdict(set)
    for g in gt["labels"]:
        if g["record_table"] and g["record_id"]:
            labelled[g["issue_type"]].add(_key(g["record_table"], g["record_id"]))

    checks: list[dict] = []

    def check(name: str, category: str, description: str, violations: list[tuple[str, str]]):
        found = {k for k, _ in violations}
        expected = set()
        for issue in EXPECTED_BY_CHECK.get(name, ()):
            expected |= labelled.get(issue, set())
        if not found and not expected:
            status = "PASS"
        elif found == expected:
            status = "EXPECTED_EXCEPTIONS"
        else:
            status = "FAIL"
        checks.append(
            {
                "check": name,
                "category": category,
                "description": description,
                "status": status,
                "violations": len(found),
                "expected_labelled": len(expected),
                "unexpected": sorted(found - expected)[:20],
                "labelled_not_found": sorted(expected - found)[:20],
                "examples": [f"{k}: {why}" for k, why in violations[:5]],
            }
        )

    pk = {}
    # ---------------- keys, formats, required, enums
    v_pk, v_fmt, v_req, v_enum, v_range, v_onehot = [], [], [], [], [], []
    for tb in TABLES:
        rows = t[tb.name]
        seen = Counter(tuple(r.get(k) for k in tb.pk) for r in rows)
        for key, n in seen.items():
            if n > 1:
                v_pk.append((_key(tb.name, "|".join(map(str, key))), f"{n} rows share the key"))
        pk[tb.name] = {k[0] if len(k) == 1 else k for k in seen}
        for r in rows:
            rid = "|".join(str(r.get(k)) for k in tb.pk)
            for c in tb.cols:
                v = r.get(c.name)
                if v is None:
                    if c.required:
                        v_req.append((_key(tb.name, rid), f"{c.name} is null"))
                    continue
                if c.pattern and not ids.matches(c.pattern, v):
                    v_fmt.append((_key(tb.name, rid), f"{c.name}={v!r} does not match {c.pattern}"))
                if c.allowed and v not in c.allowed:
                    v_enum.append((_key(tb.name, rid), f"{c.name}={v!r} not allowed"))
                if c.min is not None and v < c.min or c.max is not None and v > c.max:
                    v_range.append((_key(tb.name, rid), f"{c.name}={v} outside [{c.min}, {c.max}]"))
                if c.type in ("DATE", "TIMESTAMP"):
                    try:
                        date.fromisoformat(v) if c.type == "DATE" else parse_ts(v)
                    except ValueError:
                        v_fmt.append((_key(tb.name, rid), f"{c.name}={v!r} is not ISO 8601"))
                if c.type == "JSON":
                    try:
                        json.loads(v)
                    except ValueError:
                        v_fmt.append((_key(tb.name, rid), f"{c.name} is not valid JSON"))
            for group in tb.exactly_one_of:
                if sum(r.get(g) is not None for g in group) != 1:
                    v_onehot.append((_key(tb.name, rid), f"exactly one of {group} required"))
            for group in tb.at_most_one_of:
                if sum(r.get(g) is not None for g in group) > 1:
                    v_onehot.append((_key(tb.name, rid), f"at most one of {group} allowed"))
    check("primary_key_unique", "INTEGRITY", "Primary keys are unique in every table", v_pk)
    check("id_and_format", "INTEGRITY", "IDs follow the naming convention; dates/JSON parse", v_fmt)
    check("required_fields", "INTEGRITY", "Required fields are present", v_req)
    check("allowed_values", "INTEGRITY", "Enumerations and numeric ranges", v_enum + v_range)
    check("exclusive_references", "INTEGRITY", "Polymorphic references use exactly one FK", v_onehot)

    # ---------------- foreign keys
    v_fk = []
    for tb in TABLES:
        for c in tb.cols:
            if not c.fk:
                continue
            ref_t, _ = c.fk.split(".")
            for r in t[tb.name]:
                v = r.get(c.name)
                if v is not None and v not in pk[ref_t]:
                    rid = "|".join(str(r.get(k)) for k in tb.pk)
                    v_fk.append((_key(tb.name, rid), f"{c.name}={v} not found in {ref_t}"))
    check("foreign_keys", "INTEGRITY", "Every foreign key resolves to an existing parent", v_fk)

    # ---------------- duplicates / natural keys
    v_dup = []
    for tb in TABLES:
        for group in tb.unique_together:
            cnt = Counter(tuple(r.get(g) for g in group) for r in t[tb.name])
            for key, n in cnt.items():
                if n > 1:
                    v_dup.append((_key(tb.name, "|".join(map(str, key))), f"{group} repeated {n}x"))
        for c in tb.cols:
            if c.unique and (c.name,) != tb.pk:
                cnt = Counter(r.get(c.name) for r in t[tb.name])
                v_dup += [(_key(tb.name, k), f"{c.name} repeated") for k, n in cnt.items() if n > 1]
    check(
        "duplicates",
        "INTEGRITY",
        "No duplicate natural keys (e.g. one reading per sensor per time)",
        v_dup,
    )

    # ---------------- facility consistency across relationships
    node_fac = {n["node_id"]: n["facility_id"] for n in t["node"]}
    pipe_fac = {p["pipe_id"]: p["facility_id"] for p in t["pipe"]}
    mode_fac = {m["mode_id"]: m["facility_id"] for m in t["operating_mode"]}
    run_fac = {r["simulation_run_id"]: r["facility_id"] for r in t["simulation_run"]}
    sensor_fac = {s["sensor_id"]: s["facility_id"] for s in t["sensor"]}
    v_fac = []
    for sub in ("tank", "pump", "valve"):
        for r in t[sub]:
            rid = r[f"{sub}_id"]
            if node_fac.get(rid) not in (None, r["facility_id"]):
                v_fac.append((_key(sub, rid), "facility differs from node"))
    for p in t["pipe"]:
        for end in ("from_node_id", "to_node_id"):
            if p[end] in node_fac and node_fac[p[end]] != p["facility_id"]:
                v_fac.append((_key("pipe", p["pipe_id"]), f"{end} is in another facility"))
    for s in t["sensor"]:
        ref = node_fac.get(s["attached_node_id"]) or pipe_fac.get(s["attached_pipe_id"])
        if ref and ref != s["facility_id"]:
            v_fac.append((_key("sensor", s["sensor_id"]), "attached to another facility"))
    for tbl, col in (
        ("operating_mode_valve_setting", "valve_id"),
        ("operating_mode_pump_setting", "pump_id"),
    ):
        for r in t[tbl]:
            if node_fac.get(r[col]) != mode_fac.get(r["mode_id"]):
                v_fac.append((_key(tbl, f"{r['mode_id']}|{r[col]}"), "mode and component facilities differ"))
    for s in t["simulation_run_mode_segment"]:
        if mode_fac.get(s["mode_id"]) != run_fac.get(s["simulation_run_id"]):
            v_fac.append(
                (
                    _key("simulation_run_mode_segment", s["simulation_run_id"]),
                    "mode of another facility",
                )
            )
    for r in t["telemetry"]:
        if sensor_fac.get(r["sensor_id"]) != run_fac.get(r["simulation_run_id"]):
            v_fac.append((_key("telemetry", r["telemetry_id"]), "sensor not in the run's facility"))
    check("facility_consistency", "INTEGRITY", "Related records belong to the same facility", v_fac)

    # ---------------- topology
    v_dangle, v_topo, v_roles = [], [], []
    for p in t["pipe"]:
        missing = [e for e in ("from_node_id", "to_node_id") if p[e] not in node_fac]
        if missing:
            v_dangle.append((_key("pipe", p["pipe_id"]), f"{', '.join(missing)} not an existing node"))
        if p["from_node_id"] == p["to_node_id"]:
            v_topo.append((_key("pipe", p["pipe_id"]), "self loop"))
    for f in t["facility"]:
        fid = f["facility_id"]
        nodes = {n for n, fac in node_fac.items() if fac == fid}
        valid = [
            p
            for p in t["pipe"]
            if p["facility_id"] == fid and p["from_node_id"] in nodes and p["to_node_id"] in nodes
        ]
        und = defaultdict(set)
        out = defaultdict(set)
        indeg, outdeg = Counter(), Counter()
        for p in valid:
            und[p["from_node_id"]].add(p["to_node_id"])
            und[p["to_node_id"]].add(p["from_node_id"])
            out[p["from_node_id"]].add(p["to_node_id"])
            outdeg[p["from_node_id"]] += 1
            indeg[p["to_node_id"]] += 1
        if nodes:
            start = sorted(nodes)[0]
            seen, q = {start}, deque([start])
            while q:
                for nb in und[q.popleft()]:
                    if nb not in seen:
                        seen.add(nb)
                        q.append(nb)
            for n in sorted(nodes - seen):
                v_topo.append((_key("node", n), f"not connected to the rest of {fid}"))
        inlets = [v for v in t["valve"] if v["facility_id"] == fid and v["valve_role"] == "INLET"]
        reach = set()
        q = deque(v["valve_id"] for v in inlets)
        reach |= set(q)
        while q:
            for nb in out[q.popleft()]:
                if nb not in reach:
                    reach.add(nb)
                    q.append(nb)
        for tk in t["tank"]:
            if tk["facility_id"] == fid and tk["tank_id"] not in reach:
                v_topo.append((_key("tank", tk["tank_id"]), "not reachable from an inlet (flow direction)"))
        for pmp in t["pump"]:
            if pmp["facility_id"] == fid and (indeg[pmp["pump_id"]], outdeg[pmp["pump_id"]]) != (
                1,
                1,
            ):
                v_roles.append((_key("pump", pmp["pump_id"]), "pump must have one inlet and one outlet pipe"))
        for v in t["valve"]:
            if v["facility_id"] != fid:
                continue
            want = {"INLET": (0, 1), "OUTLET": (1, 0)}.get(v["valve_role"], (1, 1))
            if (indeg[v["valve_id"]], outdeg[v["valve_id"]]) != want:
                v_roles.append(
                    (_key("valve", v["valve_id"]), f"{v['valve_role']} valve has wrong pipe count")
                )
        try:
            topology.build(t, fid)
        except topology.TopologyError as e:
            v_roles.append((_key("facility", fid), str(e)))
    check("topology_dangling_pipes", "TOPOLOGY", "Every pipe connects two existing nodes", v_dangle)
    check(
        "topology_connected",
        "TOPOLOGY",
        "Each facility is one connected graph, tanks reachable from inlet",
        v_topo,
    )
    check(
        "topology_roles",
        "TOPOLOGY",
        "Pumps/valves have the pipe counts their role requires",
        v_roles,
    )

    # ---------------- units
    unit_for = {"LEVEL": "m", "FLOW": "m3/h", "DIFF_PRESSURE": "kPa"}
    sensors = {s["sensor_id"]: s for s in t["sensor"]}
    v_unit = [
        (_key("sensor", s["sensor_id"]), "unit does not match measurement type")
        for s in t["sensor"]
        if unit_for.get(s["measurement_type"]) != s["unit"]
    ]
    v_unit += [
        (_key("telemetry", r["telemetry_id"]), "unit differs from sensor unit")
        for r in t["telemetry"]
        if r["sensor_id"] in sensors and r["unit"] != sensors[r["sensor_id"]]["unit"]
    ]
    check("units_explicit", "UNITS", "Every reading carries its sensor's engineering unit", v_unit)

    # ---------------- dates and ordering
    v_date = []
    fac_comm = {f["facility_id"]: f["commissioned_date"] for f in t["facility"]}
    installs = {}
    for tbl, col in (
        ("tank", "tank_id"),
        ("pump", "pump_id"),
        ("valve", "valve_id"),
        ("pipe", "pipe_id"),
    ):
        for r in t[tbl]:
            installs[r[col]] = r["install_date"]
            if r["install_date"] < fac_comm[r["facility_id"]]:
                v_date.append((_key(tbl, r[col]), "installed before the facility was commissioned"))
    for s in t["sensor"]:
        parent = installs.get(s["attached_node_id"] or s["attached_pipe_id"])
        if parent and s["install_date"] < parent:
            v_date.append((_key("sensor", s["sensor_id"]), "installed before its component"))
        if s["calibration_due_date"] <= s["install_date"]:
            v_date.append((_key("sensor", s["sensor_id"]), "calibration due before installation"))
    runs = {r["simulation_run_id"]: r for r in t["simulation_run"]}
    for r in runs.values():
        if step_ts(r["start_ts"], r["steps"], r["timestep_s"]) != r["end_ts"]:
            v_date.append(
                (_key("simulation_run", r["simulation_run_id"]), "end_ts != start + steps*timestep")
            )
    ordered = sorted(runs.values(), key=lambda r: r["start_ts"])
    for a, b in zip(ordered, ordered[1:], strict=False):
        if a["facility_id"] == b["facility_id"] and b["start_ts"] < a["end_ts"]:
            v_date.append((_key("simulation_run", b["simulation_run_id"]), "overlaps the previous run"))
    for r in t["telemetry"]:
        run = runs.get(r["simulation_run_id"])
        if run and (
            not 1 <= r["step"] <= run["steps"]
            or r["ts_utc"] != step_ts(run["start_ts"], r["step"], run["timestep_s"])
        ):
            v_date.append((_key("telemetry", r["telemetry_id"]), "timestamp/step outside its run"))
    segs = defaultdict(list)
    for s in t["simulation_run_mode_segment"]:
        segs[s["simulation_run_id"]].append(s)
    modes = {m["mode_id"]: m for m in t["operating_mode"]}
    for rid, ss in segs.items():
        ss.sort(key=lambda s: s["segment_seq"])
        run = runs[rid]
        expect = 1
        for s in ss:
            if s["start_step"] != expect or s["end_step"] < s["start_step"]:
                v_date.append(
                    (
                        _key("simulation_run_mode_segment", f"{rid}|{s['segment_seq']}"),
                        "gap or overlap",
                    )
                )
            expect = s["end_step"] + 1
            m = modes[s["mode_id"]]
            if m["effective_from"] > run["start_ts"][:10] or (
                m["effective_to"] and m["effective_to"] < run["end_ts"][:10]
            ):
                v_date.append(
                    (
                        _key("simulation_run_mode_segment", f"{rid}|{s['segment_seq']}"),
                        "mode not effective",
                    )
                )
        if expect != run["steps"] + 1:
            v_date.append((_key("simulation_run", rid), "mode segments do not cover the run"))
    for a in t["alarm"]:
        if a["cleared_at"] and a["cleared_at"] < a["raised_at"]:
            v_date.append((_key("alarm", a["alarm_id"]), "cleared before raised"))
        run = runs.get(a["simulation_run_id"])
        if run and not run["start_ts"] <= a["raised_at"] <= run["end_ts"]:
            v_date.append((_key("alarm", a["alarm_id"]), "raised outside its run"))
    for tbl in ("pump_state_event", "valve_state_event"):
        for e in t[tbl]:
            run = runs.get(e["simulation_run_id"])
            if run and not run["start_ts"] <= e["ts_utc"] < run["end_ts"]:
                v_date.append((_key(tbl, e["event_id"]), "event outside its run"))
    check(
        "date_ordering",
        "DATES",
        "Dates are ordered and every timestamp falls inside its run",
        v_date,
    )

    # ---------------- arithmetic reconciliation (derived values recomputed)
    v_ar = []

    def close(a, b, rel=1e-6, abs_=1e-9):
        return a is not None and b is not None and math.isclose(a, b, rel_tol=rel, abs_tol=abs_)

    for tk in t["tank"]:
        if not close(tk["capacity_m3"], round(tk["area_m2"] * tk["height_m"], 2), abs_=0.011):
            v_ar.append((_key("tank", tk["tank_id"]), "capacity != area x height"))
        if not close(tk["low_level_m"], round(0.1 * tk["height_m"], 3)) or not close(
            tk["high_level_m"], round(0.9 * tk["height_m"], 3)
        ):
            v_ar.append((_key("tank", tk["tank_id"]), "low/high level not 10/90 % of height"))
    for p in t["pipe"]:
        r = physics.pipe_resistance(p["length_m"], p["diameter_mm"], p["darcy_friction_factor"])
        if not close(p["resistance_m_per_m3h2"], r, rel=1e-6, abs_=1e-12):
            v_ar.append((_key("pipe", p["pipe_id"]), "resistance does not match Darcy-Weisbach"))
    for p in t["pump"]:
        if not close(
            p["curve_k_m_per_m3h2"],
            physics.curve_k(p["shutoff_head_m"], p["rated_head_m"], p["rated_flow_m3h"]),
            abs_=1e-12,
        ):
            v_ar.append((_key("pump", p["pump_id"]), "curve_k inconsistent with rated point"))
        motor = math.ceil(
            1.15 * physics.hydraulic_power_kw(p["rated_flow_m3h"], p["rated_head_m"]) / p["bep_efficiency"]
        )
        if p["motor_rated_kw"] != motor:
            v_ar.append((_key("pump", p["pump_id"]), "motor rating inconsistent"))
        if p["shutoff_head_m"] <= p["rated_head_m"]:
            v_ar.append((_key("pump", p["pump_id"]), "shut-off head must exceed rated head"))
    from plantsim.masterdata import valve_state

    for s in t["operating_mode_valve_setting"]:
        if s["valve_state"] != valve_state(s["opening_pct"]):
            v_ar.append(
                (
                    _key("operating_mode_valve_setting", f"{s['mode_id']}|{s['valve_id']}"),
                    "state != opening",
                )
            )
    tel_by_run = defaultdict(list)
    for r in t["telemetry"]:
        tel_by_run[r["simulation_run_id"]].append(r)
        s = sensors.get(r["sensor_id"])
        if s:
            want = "GOOD" if s["range_min"] <= r["value"] <= s["range_max"] else "BAD_RANGE"
            if r["quality_flag"] != want:
                v_ar.append((_key("telemetry", r["telemetry_id"]), "quality_flag not derived from span"))
    pev = defaultdict(list)
    for e in t["pump_state_event"]:
        pev[e["simulation_run_id"]].append(e)
    n_sensors = Counter(s["facility_id"] for s in t["sensor"])
    plants = {}
    for rid, run in runs.items():
        if run["input_config_json"]:
            if run["config_hash"] != config_hash(run["input_config_json"]):
                v_ar.append((_key("simulation_run", rid), "config_hash mismatch"))
        rows = tel_by_run.get(rid, [])
        if not rows:
            continue
        if len(rows) != run["steps"] * n_sensors[run["facility_id"]]:
            v_ar.append((_key("simulation_run", rid), "telemetry count != steps x sensors"))
        fac = run["facility_id"]
        plants.setdefault(fac, topology.build(t, fac))
        s = run_summary(plants[fac], run["steps"], run["timestep_s"], rows, pev.get(rid, []))
        if json.loads(run["output_summary_json"]) != s:
            v_ar.append((_key("simulation_run", rid), "output_summary_json differs from telemetry"))
    check("arithmetic_reconciliation", "DERIVED", "Derived values recomputed from their sources", v_ar)

    # ---------------- reproducibility inputs
    v_rep = []
    for rid, run in runs.items():
        missing = [c for c in ("input_config_json", "config_hash", "seed") if run[c] is None]
        if run["output_summary_json"] and missing:
            v_rep.append((_key("simulation_run", rid), f"has outputs but no {', '.join(missing)}"))
        if tel_by_run.get(rid) and not run["output_hash"]:
            v_rep.append((_key("simulation_run", rid), "telemetry without output_hash"))
    check(
        "run_reproducibility_inputs",
        "REPRODUCIBILITY",
        "Every run with outputs can be re-run",
        v_rep,
    )

    # ---------------- lifecycle
    v_life = []
    by_comp = defaultdict(list)
    for e in t["component_status_event"]:
        cid = e["node_id"] or e["pipe_id"] or e["sensor_id"]
        by_comp[cid].append(e)
    ref = config.REFERENCE_DATE + "T23:59:59Z"
    for evs in by_comp.values():
        evs.sort(key=lambda e: (e["effective_at"], e["event_id"]))
        prev = None
        for e in evs:
            if e["from_status"] != prev or (e["from_status"], e["to_status"]) not in ALLOWED_TRANSITIONS:
                v_life.append(
                    (
                        _key("component_status_event", e["event_id"]),
                        f"{e['from_status']}->{e['to_status']} after {prev}",
                    )
                )
            prev = e["to_status"]
    current = {
        cid: [e for e in evs if e["effective_at"] <= ref][-1]["to_status"] for cid, evs in by_comp.items()
    }
    for tbl, col in (
        ("tank", "tank_id"),
        ("pump", "pump_id"),
        ("valve", "valve_id"),
        ("pipe", "pipe_id"),
        ("sensor", "sensor_id"),
    ):
        for r in t[tbl]:
            if r[col] not in current:
                v_life.append((_key(tbl, r[col]), "no lifecycle history"))
            elif current[r[col]] != r["status"]:
                v_life.append((_key(tbl, r[col]), f"status {r['status']} != history {current[r[col]]}"))

    def status_at(cid, ts):
        st = None
        for e in by_comp.get(cid, []):
            if e["effective_at"] <= ts:
                st = e["to_status"]
        return st

    for rid, run in runs.items():
        if not tel_by_run.get(rid):
            continue
        for tbl, col in (
            ("tank", "tank_id"),
            ("valve", "valve_id"),
            ("sensor", "sensor_id"),
            ("pipe", "pipe_id"),
        ):
            for r in t[tbl]:
                if r["facility_id"] == run["facility_id"] and r[col] in by_comp:
                    if status_at(r[col], run["start_ts"]) != "ACTIVE":
                        v_life.append((_key(tbl, r[col]), f"not ACTIVE during {rid}"))
    last_state = {}
    for e in sorted(t["pump_state_event"], key=lambda e: (e["simulation_run_id"], e["pump_id"], e["ts_utc"])):
        k = (e["simulation_run_id"], e["pump_id"])
        if (
            (k not in last_state and (e["reason"] != "INITIAL" or e["from_state"] is not None))
            or (k in last_state and e["from_state"] != last_state[k])
            or e["from_state"] == e["to_state"]
        ):
            v_life.append((_key("pump_state_event", e["event_id"]), "state history broken"))
        last_state[k] = e["to_state"]
        if e["to_state"] == "ON" and status_at(e["pump_id"], e["ts_utc"]) != "ACTIVE":
            v_life.append((_key("pump_state_event", e["event_id"]), "pump started while not ACTIVE"))
    for a in t["alarm"]:
        if a["alarm_source"] == "DETECTOR" and not a["rule_id"]:
            v_life.append((_key("alarm", a["alarm_id"]), "detector alarm without rule"))
        if (a["state"] == "CLEARED") != (a["cleared_at"] is not None):
            v_life.append((_key("alarm", a["alarm_id"]), "state disagrees with cleared_at"))
    check(
        "lifecycle_transitions",
        "LIFECYCLE",
        "Allowed state transitions; status matches history",
        v_life,
    )

    # ---------------- physical plausibility
    tank_h = {tk["tank_id"]: tk["height_m"] for tk in t["tank"]}
    v_lvl = []
    for r in t["telemetry"]:
        s = sensors.get(r["sensor_id"])
        if (
            s
            and s["measurement_type"] == "LEVEL"
            and r["value"] > tank_h.get(s["attached_node_id"], math.inf)
        ):
            v_lvl.append((_key("telemetry", r["telemetry_id"]), f"level {r['value']} m above tank height"))
    # Readings from a sensor with a labelled fault (after onset) are explained by that label.
    faulty = {
        (g["simulation_run_id"], g["sensor_id"]): g["onset_step"]
        for g in gt["labels"]
        if g["category"] == "SENSOR_FAULT"
    }
    tel_step = {r["telemetry_id"]: r for r in t["telemetry"]}
    for k, _ in v_lvl:
        r = tel_step[k.split(":", 1)[1]]
        onset = faulty.get((r["simulation_run_id"], r["sensor_id"]))
        if onset is not None and r["step"] >= onset:
            labelled["SENSOR_FAULT_CONSEQUENCE"].add(k)
    check("level_within_capacity", "PLAUSIBILITY", "Level readings never exceed tank height", v_lvl)

    # ---------------- ground truth kept apart
    v_gt = []
    for tb in TABLES:
        p = data_dir / "csv" / f"{tb.name}.csv"
        header = io.csv_header(p)
        if header != tb.names:
            v_gt.append((_key(tb.name, "header"), "columns differ from the schema"))
        for h in header:
            if any(w in h.lower() for w in LABEL_WORDS):
                v_gt.append((_key(tb.name, h), "label-like column in an input table"))
    for run in runs.values():
        if run["input_config_json"] and re.search(
            r"fault|leak|blockage|degrad", run["input_config_json"], re.I
        ):
            v_gt.append((_key("simulation_run", run["simulation_run_id"]), "fault details in input config"))
    for g in gt["labels"]:
        tb = g["record_table"]
        if tb and g["record_id"] and g["record_id"] not in pk.get(tb, set()):
            v_gt.append((_key("ground_truth", g["gt_id"]), f"{g['record_id']} not found in {tb}"))
    check(
        "ground_truth_separation",
        "LEAKAGE",
        "Labels only in ground_truth/, and every label points at a real record",
        v_gt,
    )

    counts = Counter(c["status"] for c in checks)
    return {
        "overall_status": "FAIL" if counts["FAIL"] else "PASS",
        "summary": dict(sorted(counts.items())),
        "row_counts": {tb.name: len(t[tb.name]) for tb in TABLES},
        "checks": checks,
    }


def to_markdown(v: dict) -> str:
    lines = [
        "# Validation summary",
        "",
        f"**Overall: {v['overall_status']}** - " + ", ".join(f"{k}: {n}" for k, n in v["summary"].items()),
        "",
        "EXPECTED_EXCEPTIONS means the check found exactly the labelled edge cases and nothing else.",
        "",
        "| Check | Category | Status | Violations | Labelled | Description |",
        "|---|---|---|---:|---:|---|",
    ]
    for c in v["checks"]:
        lines.append(
            f"| {c['check']} | {c['category']} | {c['status']} | {c['violations']} | "
            f"{c['expected_labelled']} | {c['description']} |"
        )
    lines += ["", "## Exceptions found", ""]
    for c in v["checks"]:
        if c["examples"]:
            lines.append(f"- **{c['check']}**: " + "; ".join(c["examples"]))
    lines += ["", "## Row counts", "", "| Table | Rows |", "|---|---:|"]
    lines += [f"| {k} | {n:,} |" for k, n in v["row_counts"].items()]
    return "\n".join(lines) + "\n"


__all__ = ["validate", "to_markdown", "TABLE_BY_NAME"]
