"""Rule-based anomaly detector.

Inputs: only the published tables (master data, run configs, mode schedule, pump run
status, telemetry, operator alarms). It never reads ground_truth/.

Rules (thresholds below are the documented values):
  DQ-001  level reading above tank height                  -> DQ_LEVEL_EXCEEDS_CAPACITY
  SNS-001 primary and redundant level disagree             -> LEVEL_SENSOR_FAULT
  SNS-002 level frozen while the meters say it should move -> LEVEL_SENSOR_FAULT
  SNS-003 pump operating point above its curve             -> FLOW_SENSOR_FAULT (meter over-reads)
  PMP-001 pump operating point below its curve             -> PUMP_DEGRADATION
  BLK-001 pump on its curve but flow well below the model  -> BLOCKAGE_SUSPECTED
  MB-001  tank volume balance does not close               -> LEAK_SUSPECTED (water missing)
                                                              or MASS_BALANCE_UNEXPLAINED
          ...unless a sensor fault on one of the tank's balance sensors explains it.
Diagnosis is done per run (batch): an imbalance is attributed using all sensor evidence of
that run. raised_at is still the first time the rule held.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field

from plantsim import ids, physics, topology
from plantsim.simulator import mode_settings, parse_ts, step_ts

# Thresholds
MB_WINDOW = 8  # steps (2 h)
MB_PERSIST = 4
MB_ABS_TOL = 1.5  # m3/h
MB_REL_TOL = 0.06  # of measured inflow
LEVEL_DIFF_TOL = 0.08  # m
LEVEL_PERSIST = 3
FLATLINE_STEPS = 6
FLATLINE_MIN_MOVE = 0.05  # m the level should have moved according to the meters
CURVE_WINDOW = 4  # pump-on steps
CURVE_PERSIST = 3
CURVE_REL_TOL = 0.03  # of H0 s^2
CURVE_ABS_TOL = 0.5  # m
BLOCK_RATIO = 0.80
BLOCK_PERSIST = 3

THRESHOLDS = {
    "MB_WINDOW_STEPS": MB_WINDOW,
    "MB_PERSIST_STEPS": MB_PERSIST,
    "MB_ABS_TOL_M3H": MB_ABS_TOL,
    "MB_REL_TOL": MB_REL_TOL,
    "LEVEL_DIFF_TOL_M": LEVEL_DIFF_TOL,
    "LEVEL_PERSIST_STEPS": LEVEL_PERSIST,
    "FLATLINE_STEPS": FLATLINE_STEPS,
    "FLATLINE_MIN_MOVE_M": FLATLINE_MIN_MOVE,
    "CURVE_WINDOW_STEPS": CURVE_WINDOW,
    "CURVE_PERSIST_STEPS": CURVE_PERSIST,
    "CURVE_REL_TOL": CURVE_REL_TOL,
    "CURVE_ABS_TOL_M": CURVE_ABS_TOL,
    "BLOCK_RATIO": BLOCK_RATIO,
    "BLOCK_PERSIST_STEPS": BLOCK_PERSIST,
}


@dataclass
class Finding:
    alarm_type: str
    rule_id: str
    run_id: str
    steps: list[int]  # steps where the condition held after persistence was reached
    node_id: str | None = None
    pipe_id: str | None = None
    sensor_id: str | None = None
    telemetry_id: str | None = None
    suspected: list[str] = field(default_factory=list)
    evidence: float = 0.0
    threshold: float = 0.0
    unit: str = ""
    message: str = ""
    sign: int = 0


def episodes(flags: list[bool], persist: int) -> list[int]:
    """Steps at which a condition has held for `persist` consecutive samples."""
    out, run = [], 0
    for k, f in enumerate(flags):
        run = run + 1 if f else 0
        if run >= persist:
            out.append(k)
    return out


class RunData:
    def __init__(self, run: dict, plant, telemetry: list[dict], pump_events, segments, modes):
        self.run = run
        self.plant = plant
        self.n = run["steps"]
        self.dt_h = run["timestep_s"] / 3600.0
        self.cfg = json.loads(run["input_config_json"])
        self.values: dict[str, list[float | None]] = defaultdict(lambda: [None] * (self.n + 1))
        self.tel_id: dict[tuple[str, int], str] = {}
        for r in telemetry:
            if r["quality_flag"] == "GOOD":
                self.values[r["sensor_id"]][r["step"]] = r["value"]
            self.tel_id[(r["sensor_id"], r["step"])] = r["telemetry_id"]
        # Pump run status per step (an event at ts applies from that interval on)
        start = parse_ts(run["start_ts"])
        self.pump_on: dict[str, list[bool]] = {}
        by_pump = defaultdict(list)
        for e in pump_events:
            k = int((parse_ts(e["ts_utc"]) - start).total_seconds() // run["timestep_s"]) + 1
            by_pump[e["pump_id"]].append((k, e["to_state"]))
        for p in plant.pumps:
            state, series = False, [False] * (self.n + 1)
            evs = sorted(by_pump.get(p, []))
            j = 0
            for i in range(1, self.n + 1):
                while j < len(evs) and evs[j][0] <= i:
                    state = evs[j][1] == "ON"
                    j += 1
                series[i] = state
            self.pump_on[p] = series
        self.mode = [None] * (self.n + 1)
        for s in segments:
            for i in range(s["start_step"], s["end_step"] + 1):
                self.mode[i] = modes[s["mode_id"]]
        ov = self.cfg.get("overrides", {})
        self.speed_ovr = ov.get("pump_speed_pct", {})
        self.open_ovr = ov.get("valve_opening_pct", {})

    def speed(self, pump_id: str, i: int) -> float:
        return self.speed_ovr.get(pump_id, self.mode[i].pump_speed[pump_id]) / 100.0

    def opening(self, valve_id: str, i: int) -> float:
        return self.open_ovr.get(valve_id, self.mode[i].valve_opening[valve_id]) / 100.0

    def ts(self, step: int) -> str:
        return step_ts(self.run["start_ts"], step, self.run["timestep_s"])


def _fill_gaps(series: list[float | None]) -> list[float | None]:
    """Linear interpolation over isolated missing readings (index 0 is unused)."""
    out = list(series)
    n = len(out) - 1
    for i in range(1, n + 1):
        if out[i] is None:
            prev = next((out[j] for j in range(i - 1, 0, -1) if out[j] is not None), None)
            nxt = next((series[j] for j in range(i + 1, n + 1) if series[j] is not None), None)
            if prev is not None and nxt is not None:
                out[i] = (prev + nxt) / 2
            else:
                out[i] = prev if prev is not None else nxt
    return out


def detect_run(rd: RunData) -> list[Finding]:
    plant, n, run_id = rd.plant, rd.n, rd.run["simulation_run_id"]
    findings: list[Finding] = []

    # DQ-001: level above tank height. Flag, then interpolate for the other rules.
    for tank_id, sensors in plant.level_sensors.items():
        height = plant.tanks[tank_id].height
        for sid in sensors:
            series = rd.values[sid]
            for i in range(1, n + 1):
                v = series[i]
                if v is not None and v > height:
                    findings.append(
                        Finding(
                            "DQ_LEVEL_EXCEEDS_CAPACITY",
                            "DQ-001",
                            run_id,
                            [i],
                            sensor_id=sid,
                            telemetry_id=rd.tel_id[(sid, i)],
                            evidence=v,
                            threshold=height,
                            unit="m",
                            message=f"{sid} reads {v} m, above the {height} m height of {tank_id}",
                        )
                    )
                    series[i] = None
            rd.values[sid] = _fill_gaps(series)

    sensor_faults: dict[str, Finding] = {}

    def add_sensor_fault(f: Finding):
        prev = sensor_faults.get(f.sensor_id)
        if prev is None or f.steps[0] < prev.steps[0]:
            if prev is not None:
                f.steps = sorted(set(f.steps) | set(prev.steps))
            sensor_faults[f.sensor_id] = f
        else:
            prev.steps = sorted(set(prev.steps) | set(f.steps))

    def balance_meters(tank_id: str) -> tuple[list[str], list[str]]:
        ins, outs = [], []
        tr_in = plant.transfer_into(tank_id)
        if tr_in:
            ins.append(tr_in.flow_sensor)
        ins += [b.flow_sensor for b in plant.inlets if b.tank_id == tank_id]
        outs += [tr.flow_sensor for tr in plant.transfers if tr.src == tank_id]
        outs += [b.flow_sensor for b in plant.outlets if b.tank_id == tank_id]
        return ins, outs

    # Mass balance residual series for a tank using a given level sensor
    def mb_series(tank_id: str, level_sid: str):
        tk = plant.tanks[tank_id]
        ins, outs = balance_meters(tank_id)
        lv = rd.values[level_sid]
        rate = [None] * (n + 1)
        inflow = [None] * (n + 1)
        for i in range(MB_WINDOW + 1, n + 1):
            win = range(i - MB_WINDOW + 1, i + 1)
            try:
                vin = sum(rd.values[s][j] for s in ins for j in win) * rd.dt_h
                vout = sum(rd.values[s][j] for s in outs for j in win) * rd.dt_h
                dv = tk.area * (lv[i] - lv[i - MB_WINDOW])
            except TypeError:
                continue
            hours = MB_WINDOW * rd.dt_h
            rate[i] = (vin - vout - dv) / hours
            inflow[i] = vin / hours
        return rate, inflow, ins + outs

    def mb_flags(rate, inflow):
        flags, tol = [False] * (n + 1), [0.0] * (n + 1)
        for i in range(1, n + 1):
            if rate[i] is not None:
                tol[i] = max(MB_ABS_TOL, MB_REL_TOL * inflow[i])
                flags[i] = abs(rate[i]) > tol[i]
        return flags, tol

    # SNS-001 redundant disagreement, SNS-002 flatline
    for tank_id, sensors in plant.level_sensors.items():
        if len(sensors) > 1:
            a, b = rd.values[sensors[0]], rd.values[sensors[1]]
            diff = [abs(a[i] - b[i]) if a[i] is not None and b[i] is not None else 0.0 for i in range(n + 1)]
            hits = episodes([d > LEVEL_DIFF_TOL for d in diff], LEVEL_PERSIST)
            if hits:
                # Blame the sensor whose mass balance is worse after the disagreement starts
                scores = {}
                for sid in sensors[:2]:
                    rate, _, _ = mb_series(tank_id, sid)
                    vals = [abs(rate[i]) for i in range(hits[0], n + 1) if rate[i] is not None]
                    scores[sid] = sum(vals) / len(vals) if vals else 0.0
                bad = max(scores, key=lambda s: (scores[s], s))
                add_sensor_fault(
                    Finding(
                        "LEVEL_SENSOR_FAULT",
                        "SNS-001",
                        run_id,
                        hits,
                        sensor_id=bad,
                        node_id=None,
                        suspected=[bad, tank_id],
                        evidence=round(max(diff), 3),
                        threshold=LEVEL_DIFF_TOL,
                        unit="m",
                        message=(
                            f"Level sensors on {tank_id} disagree by up to {max(diff):.3f} m; "
                            f"{bad} has the larger mass-balance error"
                        ),
                    )
                )
        ins, outs = balance_meters(tank_id)
        area = plant.tanks[tank_id].area
        for sid in sensors:
            v = rd.values[sid]
            same = [False] * (n + 1)
            for i in range(2, n + 1):
                same[i] = v[i] is not None and v[i] == v[i - 1] and v[i] > 0.05
            # A frozen value only counts when the meters say the level should have moved;
            # a steady tank can repeat a reading by chance (1 mm resolution, 4 mm noise).
            hits = []
            for i in episodes(same, FLATLINE_STEPS - 1):
                win = range(i - FLATLINE_STEPS + 2, i + 1)
                net = sum((rd.values[s][j] or 0.0) for s in ins for j in win) - sum(
                    (rd.values[s][j] or 0.0) for s in outs for j in win
                )
                if abs(net * rd.dt_h / area) > FLATLINE_MIN_MOVE:
                    hits.append(i)
            if hits:
                add_sensor_fault(
                    Finding(
                        "LEVEL_SENSOR_FAULT",
                        "SNS-002",
                        run_id,
                        hits,
                        sensor_id=sid,
                        suspected=[sid, tank_id],
                        evidence=float(len(hits) + FLATLINE_STEPS - 1),
                        threshold=float(FLATLINE_STEPS),
                        unit="steps",
                        message=f"{sid} repeats the same value for {len(hits) + FLATLINE_STEPS - 1} steps",
                    )
                )

    def healthy_level(tank_id: str) -> str:
        """Primary level sensor, or the redundant one if the primary is known to be faulty."""
        sensors = plant.level_sensors[tank_id]
        for sid in sensors:
            if sid not in sensor_faults:
                return sid
        return sensors[0]

    # Pump rules: curve residual (SNS-003 / PMP-001) and blockage (BLK-001)
    for tr in plant.transfers:
        if tr.kind != "PUMP":
            continue
        pr = plant.pumps[tr.pump_id]
        h0, k = pr["shutoff_head_m"], pr["curve_k_m_per_m3h2"]
        q_series, dp_series = rd.values[tr.flow_sensor], rd.values[tr.pressure_sensor]
        src, dst = plant.tanks[tr.src], plant.tanks[tr.dst]
        lvl_s = rd.values[healthy_level(tr.src)]
        lvl_d = rd.values[healthy_level(tr.dst)]
        on_steps, resid, tol, ratio = [], [], [], []
        for i in range(2, n + 1):
            if not rd.pump_on[tr.pump_id][i]:
                continue
            q, dp = q_series[i], dp_series[i]
            if q is None or dp is None:
                continue
            s = rd.speed(tr.pump_id, i)
            h_meas = dp / physics.KPA_PER_M
            resid.append(h_meas - (h0 * s * s - k * q * q))
            tol.append(max(CURVE_ABS_TOL, CURVE_REL_TOL * h0 * s * s))
            # Expected flow from the nominal system curve at the measured levels
            ls = (lvl_s[i] + lvl_s[i - 1]) / 2
            ld = (lvl_d[i] + lvl_d[i - 1]) / 2
            r = plant.pipe_resistance(tr.pipes) + sum(
                physics.valve_resistance(plant.valves[v]["kv_m3h"], rd.opening(v, i)) for v in tr.valves
            )
            q_exp = physics.pump_flow(h0 * s * s, k, (dst.z + ld) - (src.z + ls), r)
            ratio.append(q / q_exp if q_exp > 1.0 else 1.0)
            on_steps.append(i)
        mean_res = []
        for j in range(len(on_steps)):
            w = resid[max(0, j - CURVE_WINDOW + 1) : j + 1]
            mean_res.append(sum(w) / len(w) if len(w) >= CURVE_WINDOW - 1 else 0.0)
        for sign, atype, rule in (
            (1, "FLOW_SENSOR_FAULT", "SNS-003"),
            (-1, "PUMP_DEGRADATION", "PMP-001"),
        ):
            flags = [sign * m > t for m, t in zip(mean_res, tol, strict=False)]
            hits = [on_steps[j] for j in episodes(flags, CURVE_PERSIST)]
            if not hits:
                continue
            peak = max((sign * m for m in mean_res), default=0.0)
            f = Finding(
                atype,
                rule,
                run_id,
                hits,
                evidence=round(peak, 3),
                threshold=round(max(tol), 3),
                unit="m",
                sign=sign,
            )
            if atype == "FLOW_SENSOR_FAULT":
                f.sensor_id = tr.flow_sensor
                f.suspected = [tr.flow_sensor, tr.pump_id]
                f.message = (
                    f"{tr.pump_id} appears to run {peak:.2f} m above its curve: "
                    f"flow meter {tr.flow_sensor} over-reads"
                )
                add_sensor_fault(f)
            else:
                f.node_id = tr.pump_id
                f.suspected = [tr.pump_id]
                f.message = f"{tr.pump_id} delivers up to {peak:.2f} m less head than its curve"
                findings.append(f)
        block_flags = [
            r < BLOCK_RATIO and abs(m) <= 0.5 * t for r, m, t in zip(ratio, mean_res, tol, strict=False)
        ]
        hits = [on_steps[j] for j in episodes(block_flags, BLOCK_PERSIST)]
        if hits:
            worst = min(ratio[j] for j in range(len(ratio)) if block_flags[j])
            findings.append(
                Finding(
                    "BLOCKAGE_SUSPECTED",
                    "BLK-001",
                    run_id,
                    hits,
                    node_id=tr.pump_id,
                    suspected=[tr.pump_id] + tr.valves + tr.pipes,
                    evidence=round(worst, 3),
                    threshold=BLOCK_RATIO,
                    unit="ratio",
                    message=(
                        f"{tr.pump_id} is on its curve but delivers {worst:.0%} of the expected "
                        f"flow: restriction on the {tr.src} -> {tr.dst} path"
                    ),
                )
            )

    # MB-001 per tank, with sensor-fault explanation
    for tank_id in sorted(plant.tanks):
        level_sid = healthy_level(tank_id)
        rate, inflow, balance_sensors = mb_series(tank_id, level_sid)
        flags, tol = mb_flags(rate, inflow)
        hits = episodes(flags, MB_PERSIST)
        if not hits:
            continue
        explained = [s for s in balance_sensors + [level_sid] if s in sensor_faults]
        if explained:
            for s in explained:
                sensor_faults[s].message += f"; explains the imbalance at {tank_id}"
            continue
        peak_i = max(hits, key=lambda i: abs(rate[i]))
        sign = 1 if rate[peak_i] > 0 else -1
        tr_in = plant.transfer_into(tank_id)
        if sign > 0:
            pipe = tr_in.pipes[-1] if tr_in else next(b.pipe_id for b in plant.inlets if b.tank_id == tank_id)
            suspected = (tr_in.downstream_of_sensor if tr_in else [pipe]) + [tank_id]
            findings.append(
                Finding(
                    "LEAK_SUSPECTED",
                    "MB-001",
                    run_id,
                    hits,
                    pipe_id=pipe,
                    suspected=suspected,
                    evidence=round(rate[peak_i], 3),
                    threshold=round(tol[peak_i], 3),
                    unit="m3/h",
                    sign=1,
                    message=(
                        f"{rate[peak_i]:.2f} m3/h measured into {tank_id} is not stored: "
                        f"loss between the meter and the tank"
                    ),
                )
            )
        else:
            findings.append(
                Finding(
                    "MASS_BALANCE_UNEXPLAINED",
                    "MB-001",
                    run_id,
                    hits,
                    node_id=tank_id,
                    suspected=[tank_id],
                    evidence=round(rate[peak_i], 3),
                    threshold=round(tol[peak_i], 3),
                    unit="m3/h",
                    sign=-1,
                    message=f"{tank_id} gains {-rate[peak_i]:.2f} m3/h more than measured inflow",
                )
            )
    findings.extend(sensor_faults.values())
    return findings


SEVERITY = {
    "LEAK_SUSPECTED": "HIGH",
    "BLOCKAGE_SUSPECTED": "HIGH",
    "PUMP_DEGRADATION": "MEDIUM",
    "LEVEL_SENSOR_FAULT": "MEDIUM",
    "FLOW_SENSOR_FAULT": "MEDIUM",
    "MASS_BALANCE_UNEXPLAINED": "MEDIUM",
    "DQ_LEVEL_EXCEEDS_CAPACITY": "LOW",
}


def detect(tables: dict[str, list[dict]], first_alarm_no: int = 0):
    """Run every rule on every reproducible run. Returns (alarm, alarm_event, alarm_review)."""
    modes = mode_settings(tables)
    tel_by_run = defaultdict(list)
    for r in tables["telemetry"]:
        tel_by_run[r["simulation_run_id"]].append(r)
    pev_by_run = defaultdict(list)
    for e in tables["pump_state_event"]:
        pev_by_run[e["simulation_run_id"]].append(e)
    seg_by_run = defaultdict(list)
    for s in tables["simulation_run_mode_segment"]:
        seg_by_run[s["simulation_run_id"]].append(s)
    plants = {}
    operator = [a for a in tables.get("alarm", []) if a["alarm_source"] == "OPERATOR"]
    alarm_no = max([first_alarm_no] + [int(a["alarm_id"][4:]) for a in operator])
    alarms, events, reviews = [], [], []
    findings_by_run: dict[str, list[tuple[dict, Finding]]] = defaultdict(list)

    for run in sorted(tables["simulation_run"], key=lambda r: r["simulation_run_id"]):
        rid = run["simulation_run_id"]
        if not run.get("input_config_json") or not tel_by_run.get(rid):
            continue  # not reproducible / no data: reported by validation instead
        fac = run["facility_id"]
        if fac not in plants:
            plants[fac] = topology.build(tables, fac)
        rd = RunData(run, plants[fac], tel_by_run[rid], pev_by_run[rid], seg_by_run[rid], modes)
        found = detect_run(rd)
        found.sort(key=lambda f: (f.steps[0], f.alarm_type, f.sensor_id or f.node_id or f.pipe_id or ""))
        for f in found:
            alarm_no += 1
            aid = ids.make("ALM", alarm_no)
            raised = rd.ts(f.steps[0])
            last = f.steps[-1]
            cleared = rd.ts(last + 1) if last < rd.n else None
            if f.alarm_type == "DQ_LEVEL_EXCEEDS_CAPACITY":
                cleared = rd.ts(f.steps[0])
            row = {
                "alarm_id": aid,
                "simulation_run_id": rid,
                "facility_id": fac,
                "alarm_source": "DETECTOR",
                "alarm_type": f.alarm_type,
                "severity": SEVERITY[f.alarm_type],
                "rule_id": f.rule_id,
                "node_id": None if f.sensor_id or f.pipe_id else f.node_id,
                "pipe_id": None if f.sensor_id else f.pipe_id,
                "sensor_id": f.sensor_id,
                "telemetry_id": f.telemetry_id,
                "suspected_components": ";".join(f.suspected) or None,
                "raised_at": raised,
                "cleared_at": cleared,
                "state": "CLEARED" if cleared else "ACTIVE",
                "evidence_value": f.evidence,
                "threshold_value": f.threshold,
                "evidence_unit": f.unit,
                "message": f.message,
            }
            alarms.append(row)
            findings_by_run[rid].append((row, f))

    ev_no = 0
    for a in sorted(operator + alarms, key=lambda r: r["alarm_id"]):
        ev_no += 1
        events.append(_aev(ev_no, a["alarm_id"], a["raised_at"], "RAISED", None, "ACTIVE"))
        if a["cleared_at"]:
            ev_no += 1
            events.append(_aev(ev_no, a["alarm_id"], a["cleared_at"], "CLEARED", "ACTIVE", "CLEARED"))

    for n_rev, op in enumerate(sorted(operator, key=lambda r: r["alarm_id"]), start=1):
        reviews.append(_review(n_rev, op, findings_by_run.get(op["simulation_run_id"], []), plants))
    return alarms, events, reviews


def _aev(n, alarm_id, ts, etype, frm, to):
    return {
        "event_id": ids.make("AEV", n),
        "alarm_id": alarm_id,
        "ts_utc": ts,
        "event_type": etype,
        "from_state": frm,
        "to_state": to,
    }


def _review(n: int, op: dict, found: list[tuple[dict, Finding]], plants) -> dict:
    tank = op["node_id"]
    plant = plants.get(op["facility_id"])
    balance = set()
    if plant and tank in plant.tanks:
        tr_in = plant.transfer_into(tank)
        if tr_in:
            balance.add(tr_in.flow_sensor)
        balance |= {tr.flow_sensor for tr in plant.transfers if tr.src == tank}
        balance |= {b.flow_sensor for b in plant.inlets + plant.outlets if b.tank_id == tank}
        balance |= set(plant.level_sensors.get(tank, []))
    same = [row for row, f in found if row["alarm_type"] == op["alarm_type"] and tank in f.suspected]
    sensor = [
        row
        for row, f in found
        if row["alarm_type"] in ("LEVEL_SENSOR_FAULT", "FLOW_SENSOR_FAULT") and row["sensor_id"] in balance
    ]
    if same:
        decision = "CONFIRMED"
        support = same
        why = f"Detector raised {op['alarm_type']} on the same location."
    elif sensor and op["alarm_type"] in ("LEAK_SUSPECTED", "MASS_BALANCE_UNEXPLAINED"):
        decision = "RECLASSIFY_AS_SENSOR_FAULT"
        support = sensor
        why = (
            f"The imbalance at {tank} is explained by a fault on "
            f"{', '.join(r['sensor_id'] for r in sensor)}; no process leak is needed to explain it."
        )
    else:
        decision, support, why = "UNCONFIRMED", [], "No detector evidence for this alarm."
    return {
        "review_id": ids.make("ARV", n),
        "alarm_id": op["alarm_id"],
        "decision": decision,
        "supporting_alarm_ids": ";".join(r["alarm_id"] for r in support) or None,
        "explanation": why,
    }
