"""Scenario plan: one run per (facility, scenario) with its operating config and fault list.

The operating config is published with the run (simulation_run.input_config_json).
The fault list is ground truth and is written only to ground_truth/.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import timedelta

from plantsim import config, ids, physics
from plantsim.simulator import ModeSettings, fmt_ts, parse_ts
from plantsim.topology import PlantModel

# Mode schedule templates as fractions of the run: (mode_code, start_fraction)
TEMPLATES = {
    "NORMAL_DAY": (("NORMAL", 0.0), ("HIGH_DEMAND", 0.30), ("MAINTENANCE", 0.55), ("NORMAL", 0.80)),
    "STEADY": (("NORMAL", 0.0),),
    "PEAK_LATE": (("NORMAL", 0.0), ("HIGH_DEMAND", 0.60)),
    "NIGHT_START": (("LOW_DEMAND", 0.0), ("NORMAL", 0.40)),
}


@dataclass
class RunPlan:
    run_id: str
    facility_id: str
    scenario: str
    seed: int
    config: dict
    faults: list[dict]


def run_seed(seed: int, run_id: str) -> int:
    return int(hashlib.sha256(f"{seed}:{run_id}".encode()).hexdigest()[:12], 16)


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def config_hash(cfg_json: str) -> str:
    return hashlib.sha256(cfg_json.encode()).hexdigest()


def schedule_from_template(name: str, steps: int, modes_by_code: dict[str, str]) -> list[dict]:
    parts = TEMPLATES[name]
    starts = [max(1, round(frac * steps) + 1) for _, frac in parts]
    out = []
    for k, (code, _) in enumerate(parts):
        end = starts[k + 1] - 1 if k + 1 < len(parts) else steps
        if end >= starts[k]:
            out.append({"mode_id": modes_by_code[code], "start_step": starts[k], "end_step": end})
    return out


def build_plan(
    seed: int,
    steps: int,
    plants: dict[str, PlantModel],
    modes: dict[str, ModeSettings],
) -> list[RunPlan]:
    rng = random.Random(f"plan-{seed}")
    plans = []
    run_no = 0
    start0 = parse_ts(config.RUN_START_UTC)
    for f_index, fac_id in enumerate(sorted(plants)):
        plant = plants[fac_id]
        by_code = {m.mode_code: m.mode_id for m in modes.values() if m.facility_id == fac_id}
        maint_pump = modes[by_code["MAINTENANCE"]].maintenance_pump_id
        pumped = [tr for tr in plant.transfers if tr.kind == "PUMP" and tr.pump_id != maint_pump]
        fault_targets = rng.sample(pumped, 4)  # leak, blockage, degradation, flow-bias
        for scenario in config.SCENARIOS:
            run_no += 1
            run_id = ids.make("SIM", run_no)
            start = start0 + timedelta(hours=config.RUN_SPACING_HOURS * (run_no - 1))
            template = (
                "NORMAL_DAY" if scenario == "NORMAL" else rng.choice(("STEADY", "PEAK_LATE", "NIGHT_START"))
            )
            cfg = {
                "facility_id": fac_id,
                "start_ts": fmt_ts(start),
                "steps": steps,
                "timestep_s": config.STEP_SECONDS,
                "substeps": config.SUBSTEPS,
                "mode_template": template,
                "mode_schedule": schedule_from_template(template, steps, by_code),
                "initial_levels_m": {
                    t: round(plant.tanks[t].height * rng.uniform(0.45, 0.70), 3) for t in sorted(plant.tanks)
                },
                "demand": {
                    "diurnal_amplitude": config.DIURNAL_AMPLITUDE,
                    "peak_hour": config.DIURNAL_PEAK_HOUR,
                    "outlets": {
                        b.valve_id: {
                            "phase_h": round(rng.uniform(0, 24), 2),
                            "harmonic_amp": round(rng.uniform(0.03, 0.08), 3),
                        }
                        for b in plant.outlets
                    },
                },
                "control": {
                    "pump_start_frac": config.PUMP_START_FRAC,
                    "pump_stop_frac": config.PUMP_STOP_FRAC,
                    "suction_stop_frac": config.SUCTION_STOP_FRAC,
                    "suction_restart_frac": config.SUCTION_RESTART_FRAC,
                    "gravity_close_frac": config.GRAVITY_CLOSE_FRAC,
                    "inlet_close_frac": config.INLET_CLOSE_FRAC,
                },
                "overrides": {},
            }
            onset = max(2, round(steps * rng.uniform(0.35, 0.50)))
            faults = _faults(scenario, f_index, plant, fault_targets, onset, rng)
            plans.append(RunPlan(run_id, fac_id, scenario, run_seed(seed, run_id), cfg, faults))
    return plans


def _faults(scenario, f_index, plant: PlantModel, targets, onset, rng) -> list[dict]:
    leak_tr, block_tr, degr_tr, bias_tr = targets
    if scenario == "NORMAL":
        return []
    if scenario == "LEAK":
        return [
            {
                "type": "LEAK",
                "pipe_id": leak_tr.pipes[-1],  # discharge pipe, downstream of the flow meter
                "pump_id": leak_tr.pump_id,
                "tank_id": leak_tr.dst,
                "leak_fraction": config.LEAK_FRACTIONS[f_index],
                "onset_step": onset,
            }
        ]
    if scenario == "BLOCKAGE":
        pipe = block_tr.pipes[-1]
        target_ratio = round(rng.uniform(*config.BLOCKAGE_FLOW_RATIO), 3)
        # Extra resistance that cuts flow to target_ratio at the design point:
        # Q ~ 1/sqrt(k + R)  =>  dR = (k + R) (1/ratio^2 - 1)
        pr = plant.pumps[block_tr.pump_id]
        r = plant.pipe_resistance(block_tr.pipes) + sum(
            physics.valve_resistance(plant.valves[v]["kv_m3h"], 1.0) for v in block_tr.valves
        )
        d_r = (pr["curve_k_m_per_m3h2"] + r) * (1 / target_ratio**2 - 1)
        r_pipe = plant.pipes[pipe]["resistance_m_per_m3h2"]
        return [
            {
                "type": "BLOCKAGE",
                "pipe_id": pipe,
                "pump_id": block_tr.pump_id,
                "target_flow_ratio": target_ratio,
                "resistance_multiplier": round(1 + d_r / r_pipe, 3),
                "onset_step": onset,
            }
        ]
    if scenario == "PUMP_DEGRADATION":
        return [
            {
                "type": "PUMP_DEGRADATION",
                "pump_id": degr_tr.pump_id,
                "final_health": round(rng.uniform(*config.DEGRADATION_FINAL_HEALTH), 3),
                "onset_step": onset,
            }
        ]
    # SENSOR_FAULT
    kind = config.SENSOR_FAULT_TYPES[f_index]
    if kind == "BIAS":
        return [
            {
                "type": "SENSOR_FAULT",
                "fault_type": "BIAS",
                "sensor_id": bias_tr.flow_sensor,
                "pump_id": bias_tr.pump_id,
                "tank_id": bias_tr.dst,
                "bias_fraction": config.FLOW_BIAS_FRACTION,
                "onset_step": onset,
            }
        ]
    redundant = sorted(t for t, s in plant.level_sensors.items() if len(s) > 1)
    tank = rng.choice(redundant)
    f = {
        "type": "SENSOR_FAULT",
        "fault_type": kind,
        "sensor_id": plant.level_sensors[tank][0],
        "tank_id": tank,
        "onset_step": onset,
    }
    if kind == "DRIFT":
        f["drift_m_per_h"] = config.LEVEL_DRIFT_M_PER_H
    return [f]
