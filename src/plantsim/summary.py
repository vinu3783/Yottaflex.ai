"""Measured run outcomes, derived only from published telemetry and pump events.

The same function fills simulation_run.output_summary_json at generation time and is
re-run by the validator on the exported CSVs (arithmetic reconciliation).
"""

from __future__ import annotations

from collections import defaultdict

from plantsim import physics
from plantsim.topology import PlantModel


def run_summary(plant: PlantModel, steps: int, timestep_s: int, telemetry, pump_events) -> dict:
    dt_h = timestep_s / 3600.0
    v: dict[str, dict[int, float]] = defaultdict(dict)
    for r in telemetry:
        v[r["sensor_id"]][r["step"]] = r["value"]

    def total(sensor_ids):
        return sum(v[s].get(i, 0.0) for s in sensor_ids for i in range(2, steps + 1)) * dt_h

    inflow = total([b.flow_sensor for b in plant.inlets])
    outflow = total([b.flow_sensor for b in plant.outlets])
    storage = 0.0
    min_frac = 1.0
    low_steps = 0
    for tank_id, sensors in sorted(plant.level_sensors.items()):
        tk = plant.tanks[tank_id]
        lv = v[sensors[0]]
        storage += tk.area * (lv.get(steps, 0.0) - lv.get(1, 0.0))
        for i in range(1, steps + 1):
            frac = lv.get(i, 0.0) / tk.height
            min_frac = min(min_frac, frac)
            low_steps += frac < 0.10
    energy = 0.0
    for tr in plant.transfers:
        if tr.kind != "PUMP":
            continue
        pr = plant.pumps[tr.pump_id]
        for i in range(1, steps + 1):
            q = v[tr.flow_sensor].get(i, 0.0)
            dp = v[tr.pressure_sensor].get(i, 0.0)
            if q > 0 and dp > 0:
                eta = physics.pump_efficiency(q, pr["rated_flow_m3h"], pr["bep_efficiency"])
                energy += dp * (q / 3600.0) / eta * dt_h  # kPa x m3/s = kW
    residual = inflow - outflow - storage
    return {
        "measured_inflow_m3": round(inflow, 3),
        "measured_outflow_m3": round(outflow, 3),
        "measured_storage_change_m3": round(storage, 3),
        "facility_balance_residual_m3": round(residual, 3),
        "facility_balance_residual_pct": round(100 * residual / inflow, 3) if inflow else None,
        "estimated_pump_energy_kwh": round(energy, 3),
        "min_tank_fill_frac": round(min_frac, 4),
        "tank_steps_below_low_level": int(low_steps),
        "pump_starts": sum(1 for e in pump_events if e["to_state"] == "ON" and e["reason"] != "INITIAL"),
        "basis": "intervals 2..steps; inlet/outlet meters and primary level sensors",
    }
