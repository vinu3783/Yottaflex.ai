"""What-if simulation of an operating change against a baseline run.

Both the baseline and the change are re-simulated without faults (faults are ground truth
and are not part of an operating plan), with the same seed, so differences come only from
the change. Results go to <data>/whatif/<new SIM id>/; nothing existing is overwritten.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from plantsim import MODEL_VERSION, io, topology
from plantsim.detector import detect
from plantsim.scenarios import canonical_json, config_hash
from plantsim.simulator import mode_settings, simulate, step_ts
from plantsim.summary import run_summary


class WhatIfError(ValueError):
    pass


def _next_run_id(data_dir: Path, tables) -> str:
    used = {int(r["simulation_run_id"][-6:]) for r in tables["simulation_run"]}
    wf = data_dir / "whatif"
    if wf.exists():
        used |= {int(p.name[-6:]) for p in wf.iterdir() if p.name.startswith("SIM-")}
    return f"SIM-2026-{max(used) + 1:06d}"


def _rows(plant, run_id, cfg, res):
    start, dt = cfg["start_ts"], cfg["timestep_s"]
    tel = []
    for n, (step, sid, val) in enumerate(res.telemetry, start=1):
        s = plant.sensors[sid]
        tel.append(
            {
                "telemetry_id": f"TEL-W{n:06d}",
                "simulation_run_id": run_id,
                "sensor_id": sid,
                "ts_utc": step_ts(start, step, dt),
                "step": step,
                "value": val,
                "unit": s["unit"],
                "quality_flag": "GOOD" if s["range_min"] <= val <= s["range_max"] else "BAD_RANGE",
            }
        )
    pev = [
        {
            "event_id": f"PSE-W{n:05d}",
            "simulation_run_id": run_id,
            "pump_id": p,
            "ts_utc": step_ts(start, step - 1, dt),
            "from_state": frm,
            "to_state": to,
            "reason": why,
        }
        for n, (step, p, frm, to, why) in enumerate(res.pump_events, start=1)
    ]
    return tel, pev


def run_whatif(
    data_dir: Path,
    base_run: str,
    mode_code: str | None = None,
    pump_speed: dict[str, float] | None = None,
    valve_opening: dict[str, float] | None = None,
    disabled: list[str] | None = None,
    demand_multiplier: float = 1.0,
) -> dict:
    t = io.load_dataset(data_dir)
    base = next((r for r in t["simulation_run"] if r["simulation_run_id"] == base_run), None)
    if base is None:
        raise WhatIfError(f"unknown run {base_run}")
    if not base["input_config_json"]:
        raise WhatIfError(f"{base_run} has no input configuration, so it cannot be a baseline")
    fac = base["facility_id"]
    plant = topology.build(t, fac)
    modes = mode_settings(t)
    pump_speed, valve_opening, disabled = pump_speed or {}, valve_opening or {}, disabled or []
    for p, v in pump_speed.items():
        if p not in plant.pumps or not 50 <= v <= 100:
            raise WhatIfError(f"pump speed {p}={v}: pump must be in {fac} and speed 50-100 %")
    for vid, v in valve_opening.items():
        if vid not in plant.valves or not 0 <= v <= 100:
            raise WhatIfError(f"valve opening {vid}={v}: valve must be in {fac} and opening 0-100 %")
    for p in disabled:
        if p not in plant.pumps:
            raise WhatIfError(f"{p} is not a pump of {fac}")
    if not 0.1 <= demand_multiplier <= 2.0:
        raise WhatIfError("demand multiplier must be 0.1-2.0")

    base_cfg = json.loads(base["input_config_json"])
    cfg = copy.deepcopy(base_cfg)
    if mode_code:
        mid = next(
            (m.mode_id for m in modes.values() if m.facility_id == fac and m.mode_code == mode_code),
            None,
        )
        if mid is None:
            raise WhatIfError(f"{fac} has no mode {mode_code}")
        cfg["mode_template"] = f"WHATIF_{mode_code}"
        cfg["mode_schedule"] = [{"mode_id": mid, "start_step": 1, "end_step": cfg["steps"]}]
    cfg["overrides"] = {
        "pump_speed_pct": dict(sorted(pump_speed.items())),
        "valve_opening_pct": dict(sorted(valve_opening.items())),
        "pump_disabled": sorted(disabled),
        "demand_multiplier": demand_multiplier,
    }
    cfg["based_on"] = base_run

    seed = base["seed"]
    base_res = simulate(plant, modes, base_cfg, [], seed)
    new_res = simulate(plant, modes, cfg, [], seed)

    run_id = _next_run_id(data_dir, t)
    folder = data_dir / "whatif" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    cfg_json = canonical_json(cfg)
    tel, pev = _rows(plant, run_id, cfg, new_res)
    summary = run_summary(plant, cfg["steps"], cfg["timestep_s"], tel, pev)
    base_tel, base_pev = _rows(plant, base_run, base_cfg, base_res)
    base_summary = run_summary(plant, cfg["steps"], cfg["timestep_s"], base_tel, base_pev)
    run = {
        "simulation_run_id": run_id,
        "facility_id": fac,
        "run_type": "WHATIF",
        "start_ts": cfg["start_ts"],
        "end_ts": base["end_ts"],
        "timestep_s": cfg["timestep_s"],
        "steps": cfg["steps"],
        "seed": seed,
        "model_version": MODEL_VERSION,
        "input_config_json": cfg_json,
        "config_hash": config_hash(cfg_json),
        "output_hash": new_res.output_hash,
        "output_summary_json": canonical_json(summary),
        "status": "COMPLETED",
    }
    segs = [
        {
            "simulation_run_id": run_id,
            "segment_seq": k,
            "mode_id": s["mode_id"],
            "start_step": s["start_step"],
            "end_step": s["end_step"],
            "start_ts": step_ts(cfg["start_ts"], s["start_step"] - 1, cfg["timestep_s"]),
            "end_ts": step_ts(cfg["start_ts"], s["end_step"], cfg["timestep_s"]),
        }
        for k, s in enumerate(cfg["mode_schedule"], start=1)
    ]
    # Detector on the what-if run only (same rules as the dataset)
    scoped = dict(t)
    scoped.update(
        simulation_run=[run],
        telemetry=tel,
        pump_state_event=pev,
        simulation_run_mode_segment=segs,
        alarm=[],
    )
    alarms, _, _ = detect(scoped)

    for name, rows in (
        ("simulation_run", [run]),
        ("simulation_run_mode_segment", segs),
        ("telemetry", tel),
        ("pump_state_event", pev),
    ):
        io.write_csv(folder / f"{name}.csv", io.table(name), rows)
    io.write_csv(folder / "alarm.csv", io.table("alarm"), alarms)

    keys = (
        "unmet_demand_m3",
        "delivered_m3",
        "energy_kwh",
        "min_tank_fill_frac",
        "spill_m3",
        "pump_starts",
    )
    comparison = {
        "baseline_run": base_run,
        "whatif_run": run_id,
        "change": cfg["overrides"] | ({"mode": mode_code} if mode_code else {}),
        "note": "both re-simulated fault-free with the same seed",
        "true_outcomes": {
            k: {
                "baseline": base_res.truth[k],
                "whatif": new_res.truth[k],
                "delta": round(new_res.truth[k] - base_res.truth[k], 4),
            }
            for k in keys
        },
        "measured_summary": {"baseline": base_summary, "whatif": summary},
        "alarms_in_whatif": [{k: a[k] for k in ("alarm_type", "rule_id", "message")} for a in alarms],
    }
    io.dump_json(folder / "comparison.json", comparison)
    return {
        "folder": str(folder),
        "comparison": {k: comparison[k] for k in ("baseline_run", "whatif_run", "change", "true_outcomes")}
        | {"alarms": len(alarms)},
    }
