"""Quasi-steady hydraulic simulation of one facility.

Each telemetry interval (15 min) is integrated in substeps (60 s). In every substep:
  * pumped transfers run at the intersection of pump curve and system curve,
  * gravity transfers flow under the tank-to-tank head difference,
  * inlet/outlet control valves follow level control and the demand profile,
  * tank volumes are integrated (conservation of volume, water is incompressible).
Faults (leak, blockage, pump degradation, sensor faults) come from a separate fault list
so they can be kept out of the published input tables.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from plantsim import config, physics
from plantsim.topology import PlantModel

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, TS_FMT).replace(tzinfo=UTC)


def fmt_ts(d: datetime) -> str:
    return d.astimezone(UTC).strftime(TS_FMT)


def step_ts(start: str, step: int, timestep_s: int) -> str:
    return fmt_ts(parse_ts(start) + timedelta(seconds=step * timestep_s))


@dataclass
class ModeSettings:
    mode_id: str
    facility_id: str
    mode_code: str
    demand_factor: float
    maintenance_pump_id: str | None
    valve_opening: dict[str, float]  # percent
    pump_enabled: dict[str, bool]
    pump_speed: dict[str, float]  # percent


def mode_settings(tables: dict[str, list[dict]]) -> dict[str, ModeSettings]:
    out = {}
    for m in tables["operating_mode"]:
        out[m["mode_id"]] = ModeSettings(
            m["mode_id"],
            m["facility_id"],
            m["mode_code"],
            m["demand_factor"],
            m["maintenance_pump_id"],
            {},
            {},
            {},
        )
    for s in tables["operating_mode_valve_setting"]:
        out[s["mode_id"]].valve_opening[s["valve_id"]] = s["opening_pct"]
    for s in tables["operating_mode_pump_setting"]:
        out[s["mode_id"]].pump_enabled[s["pump_id"]] = s["enabled"]
        out[s["mode_id"]].pump_speed[s["pump_id"]] = s["speed_pct"]
    return out


def mode_per_step(schedule: list[dict], steps: int) -> list[str]:
    """Index 1..steps -> mode_id (index 0 unused)."""
    out = [""] * (steps + 1)
    for seg in schedule:
        for i in range(seg["start_step"], seg["end_step"] + 1):
            out[i] = seg["mode_id"]
    if any(not m for m in out[1:]):
        raise ValueError("mode schedule does not cover every step")
    return out


def diurnal(clock_h: float, amplitude: float, peak_h: float, harm_amp: float, phase_h: float):
    base = 1.0 + amplitude * math.sin(2 * math.pi * (clock_h - peak_h + 6.0) / 24.0)
    return base + harm_amp * math.sin(4 * math.pi * (clock_h + phase_h) / 24.0)


@dataclass
class SimResult:
    telemetry: list[tuple[int, str, float]] = field(default_factory=list)  # (step, sensor, value)
    pump_events: list[tuple[int, str, str | None, str, str]] = field(default_factory=list)
    valve_events: list[tuple[int, str, float | None, float, str]] = field(default_factory=list)
    truth: dict = field(default_factory=dict)
    output_hash: str = ""


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def simulate(
    plant: PlantModel,
    modes: dict[str, ModeSettings],
    cfg: dict,
    faults: list[dict] | None,
    seed: int,
) -> SimResult:
    rng = random.Random(seed)
    faults = faults or []
    n = cfg["steps"]
    dt_h = cfg["timestep_s"] / 3600.0
    sub = cfg["substeps"]
    h = dt_h / sub
    start = parse_ts(cfg["start_ts"])
    clock0 = start.hour + start.minute / 60.0
    ov = cfg.get("overrides", {})
    speed_ovr = ov.get("pump_speed_pct", {})
    open_ovr = ov.get("valve_opening_pct", {})
    disabled = set(ov.get("pump_disabled", []))
    demand_mult = ov.get("demand_multiplier", 1.0)
    dem = cfg["demand"]

    tanks = plant.tanks
    vol = {t: tanks[t].area * cfg["initial_levels_m"][t] for t in sorted(tanks)}
    vol0 = sum(vol.values())
    step_mode = mode_per_step(cfg["mode_schedule"], n)

    leak = {f["pipe_id"]: f for f in faults if f["type"] == "LEAK"}
    block = {f["pipe_id"]: f for f in faults if f["type"] == "BLOCKAGE"}
    degrade = {f["pump_id"]: f for f in faults if f["type"] == "PUMP_DEGRADATION"}
    sfault = {f["sensor_id"]: f for f in faults if f["type"] == "SENSOR_FAULT"}

    res = SimResult()
    pump_state: dict[str, str] = {}
    prev_open: dict[str, float] = {}
    last_reading: dict[str, float] = {}
    tot = defaultdict(float)
    min_frac = 1.0
    sensors = sorted(plant.sensors)
    clean_lines = []

    for i in range(1, n + 1):
        mode = modes[step_mode[i]]
        opening = {}
        for vid in plant.valves:
            pct = open_ovr.get(vid, mode.valve_opening[vid])
            opening[vid] = pct / 100.0
            if i == 1 or pct != prev_open[vid]:
                reason = "MODE_CHANGE"
                if i == 1:
                    reason = "OVERRIDE" if vid in open_ovr else "INITIAL"
                res.valve_events.append((i, vid, None if i == 1 else prev_open[vid], pct, reason))
            prev_open[vid] = pct

        # Pump control at the interval boundary (true levels, simple hysteresis)
        frac = {t: vol[t] / tanks[t].capacity for t in tanks}
        for tr in plant.transfers:
            if tr.kind != "PUMP":
                continue
            p = tr.pump_id
            enabled = mode.pump_enabled[p] and p not in disabled
            fd, fs = frac[tr.dst], frac[tr.src]
            if i == 1:
                on = enabled and fd < 0.6 and fs > config.SUCTION_RESTART_FRAC
                new, reason = ("ON" if on else "OFF"), "INITIAL"
            else:
                new, reason = pump_state[p], None
                if pump_state[p] == "ON":
                    if not enabled:
                        new, reason = "OFF", "MODE_DISABLED"
                    elif fd >= config.PUMP_STOP_FRAC:
                        new, reason = "OFF", "LEVEL_HIGH_STOP"
                    elif fs <= config.SUCTION_STOP_FRAC:
                        new, reason = "OFF", "SUCTION_LOW_STOP"
                elif enabled and fd <= config.PUMP_START_FRAC and fs >= config.SUCTION_RESTART_FRAC:
                    new, reason = "ON", "LEVEL_LOW_START"
            if reason:
                res.pump_events.append((i, p, None if i == 1 else pump_state[p], new, reason))
            pump_state[p] = new

        health = {}
        for p, f in degrade.items():
            if i >= f["onset_step"]:
                span = n - f["onset_step"] + 1
                health[p] = 1.0 - (1.0 - f["final_health"]) * (i - f["onset_step"] + 1) / span
        mult = {pid: f["resistance_multiplier"] for pid, f in block.items() if i >= f["onset_step"]}
        leak_now = {pid: f["leak_fraction"] for pid, f in leak.items() if i >= f["onset_step"]}

        acc = defaultdict(float)
        for s in range(sub):
            clock = (clock0 + (i - 1) * dt_h + (s + 0.5) * h) % 24.0
            level = {t: vol[t] / tanks[t].area for t in tanks}
            fr = {t: vol[t] / tanks[t].capacity for t in tanks}
            q_tr, head_tr = [], []
            for tr in plant.transfers:
                src, dst = tanks[tr.src], tanks[tr.dst]
                r_pipe = plant.pipe_resistance(tr.pipes, mult)
                if tr.kind == "PUMP":
                    q = hd = 0.0
                    if pump_state[tr.pump_id] == "ON":
                        pr = plant.pumps[tr.pump_id]
                        spd = speed_ovr.get(tr.pump_id, mode.pump_speed[tr.pump_id]) / 100.0
                        r = r_pipe + sum(
                            physics.valve_resistance(plant.valves[v]["kv_m3h"], opening[v]) for v in tr.valves
                        )
                        h0_eff = pr["shutoff_head_m"] * health.get(tr.pump_id, 1.0) * spd * spd
                        hs = (dst.z + level[tr.dst]) - (src.z + level[tr.src])
                        q = physics.pump_flow(h0_eff, pr["curve_k_m_per_m3h2"], hs, r)
                        hd = h0_eff - pr["curve_k_m_per_m3h2"] * q * q
                else:
                    v = tr.valves[0]
                    ctrl = _clamp((config.GRAVITY_CLOSE_FRAC - fr[tr.dst]) / config.GRAVITY_BAND_FRAC)
                    o = opening[v] * ctrl
                    r = r_pipe + physics.valve_resistance(plant.valves[v]["kv_m3h"], o)
                    dh = (src.z + level[tr.src]) - (dst.z + level[tr.dst])
                    q, hd = physics.gravity_flow(dh, r), 0.0
                q_tr.append(q)
                head_tr.append(hd)
            q_in = []
            for b in plant.inlets:
                ctrl = _clamp((config.INLET_CLOSE_FRAC - fr[b.tank_id]) / config.INLET_BAND_FRAC)
                q_in.append(b.design_flow * opening[b.valve_id] * ctrl)
            q_out, q_req = [], []
            for b in plant.outlets:
                o = dem["outlets"][b.valve_id]
                f = diurnal(
                    clock,
                    dem["diurnal_amplitude"],
                    dem["peak_hour"],
                    o["harmonic_amp"],
                    o["phase_h"],
                )
                req = b.design_flow * f * mode.demand_factor * demand_mult * opening[b.valve_id]
                q_req.append(req)
                q_out.append(req)

            # A tank cannot deliver more than it holds: scale its outflows if needed.
            out_total = defaultdict(float)
            for tr, q in zip(plant.transfers, q_tr, strict=False):
                out_total[tr.src] += q
            for b, q in zip(plant.outlets, q_out, strict=False):
                out_total[b.tank_id] += q
            scale = {t: (min(1.0, vol[t] / (out_total[t] * h)) if out_total[t] > 0 else 1.0) for t in tanks}
            for k, tr in enumerate(plant.transfers):
                if scale[tr.src] < 1.0:
                    q_tr[k] *= scale[tr.src]
                    if tr.kind == "PUMP" and q_tr[k] > 0:
                        pr = plant.pumps[tr.pump_id]
                        spd = speed_ovr.get(tr.pump_id, mode.pump_speed[tr.pump_id]) / 100.0
                        h0_eff = pr["shutoff_head_m"] * health.get(tr.pump_id, 1.0) * spd * spd
                        head_tr[k] = h0_eff - pr["curve_k_m_per_m3h2"] * q_tr[k] ** 2
            for k, b in enumerate(plant.outlets):
                q_out[k] *= scale[b.tank_id]

            for tr, q, hd in zip(plant.transfers, q_tr, head_tr, strict=False):
                lost = q * sum(leak_now.get(p, 0.0) for p in tr.pipes)
                vol[tr.src] -= q * h
                vol[tr.dst] += (q - lost) * h
                tot["leak_loss_m3"] += lost * h
                acc[tr.flow_sensor] += q / sub
                if tr.kind == "PUMP":
                    acc[tr.pressure_sensor] += (hd * physics.KPA_PER_M if q > 0 or hd > 0 else 0.0) / sub
                    if q > 0:
                        pr = plant.pumps[tr.pump_id]
                        eta = physics.pump_efficiency(q, pr["rated_flow_m3h"], pr["bep_efficiency"])
                        eta *= math.sqrt(health.get(tr.pump_id, 1.0))
                        tot["energy_kwh"] += physics.hydraulic_power_kw(q, hd) / eta * h
            for b, q in zip(plant.inlets, q_in, strict=False):
                vol[b.tank_id] += q * h
                tot["inflow_m3"] += q * h
                acc[b.flow_sensor] += q / sub
            for b, q, req in zip(plant.outlets, q_out, q_req, strict=False):
                vol[b.tank_id] -= q * h
                tot["delivered_m3"] += q * h
                tot["requested_m3"] += req * h
                acc[b.flow_sensor] += q / sub
            for t in tanks:
                if vol[t] > tanks[t].capacity:
                    tot["spill_m3"] += vol[t] - tanks[t].capacity
                    vol[t] = tanks[t].capacity
                if vol[t] < 0:
                    vol[t] = 0.0
                min_frac = min(min_frac, vol[t] / tanks[t].capacity)

        # Readings at the end of the interval
        for sid in sensors:
            s = plant.sensors[sid]
            kind = s["measurement_type"]
            if kind == "LEVEL":
                true = vol[s["attached_node_id"]] / tanks[s["attached_node_id"]].area
                val = max(0.0, true + rng.gauss(0.0, config.LEVEL_NOISE_ABS))
            elif kind == "FLOW":
                true = acc[sid]
                noise = rng.gauss(0.0, config.FLOW_NOISE_REL * true + config.FLOW_NOISE_ABS)
                val = 0.0 if true < config.FLOW_CUTOFF else max(0.0, true + noise)
            else:
                true = acc[sid]
                noise = rng.gauss(0.0, config.DP_NOISE_REL * true + config.DP_NOISE_ABS)
                val = 0.0 if true < config.DP_CUTOFF else max(0.0, true + noise)
            f = sfault.get(sid)
            if f and i >= f["onset_step"]:
                if f["fault_type"] == "STUCK":
                    val = last_reading[sid]
                elif f["fault_type"] == "DRIFT":
                    val = val + f["drift_m_per_h"] * (i - f["onset_step"] + 1) * dt_h
                elif f["fault_type"] == "BIAS":
                    val = val * (1.0 + f["bias_fraction"])
            val = round(val, 3)
            last_reading[sid] = val
            res.telemetry.append((i, sid, val))
            clean_lines.append(f"{i},{sid},{val!r}")

    vol_end = sum(vol.values())
    tot["storage_change_m3"] = vol_end - vol0
    tot["balance_error_m3"] = (
        tot["inflow_m3"]
        - tot["delivered_m3"]
        - tot["leak_loss_m3"]
        - tot["spill_m3"]
        - tot["storage_change_m3"]
    )
    tot["unmet_demand_m3"] = tot["requested_m3"] - tot["delivered_m3"]
    res.truth = {k: round(v, 3) for k, v in sorted(tot.items())}
    res.truth["min_tank_fill_frac"] = round(min_frac, 4)
    res.truth["pump_starts"] = sum(1 for e in res.pump_events if e[3] == "ON" and e[4] != "INITIAL")
    res.output_hash = hashlib.sha256("\n".join(clean_lines).encode()).hexdigest()
    return res
