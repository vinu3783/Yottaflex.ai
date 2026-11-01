# Simulation model

Units: flow Q in m3/h, head H in metres of water, pressure in kPa, level in m, volume in m3.
Water at 1000 kg/m3 and g = 9.80665 m/s2, so 1 m of head = 9.80665 kPa.

## 1. Topology

Each facility is a **tree of 10 tanks**, rooted at an intake tank. The tree is a random
recursive tree drawn from the seed, retried until it has exactly as many leaves as there
are outlet valves. Each of the 9 tree edges is one *transfer*:

| Transfer | Path | Pipes | Valves | Sensors |
|---|---|---:|---:|---|
| Pumped | tank -> SUCTION valve -> pump -> DISCHARGE valve -> tank | 4 | 2 | flow meter on pump->discharge pipe, differential pressure on pump |
| Gravity | tank -> GRAVITY_CONTROL valve -> lower tank | 2 | 1 | flow meter on valve->tank pipe |

Plus one INLET valve (boundary supply into the intake tank) and an OUTLET valve on every
leaf tank (boundary demand), each with a flow meter. Every tank has a primary level
sensor, and 10 tanks have a redundant one.

| Facility | Pumps | Gravity transfers | Valves | Outlets | Sensors |
|---|---:|---:|---:|---:|---:|
| FAC-001 | 7 | 2 | 20 | 3 | 34 |
| FAC-002 | 7 | 2 | 20 | 3 | 33 |
| FAC-003 | 6 | 3 | 20 | 4 | 33 |

`node` is the supertype of pump, tank and valve, so `pipe.from_node_id`/`to_node_id`
and `sensor.attached_node_id` are real foreign keys. Validation confirms that each
facility is one connected graph and that every tank is reachable from the inlet in the
flow direction.

## 2. Sizing (derived, not random)

- Base demand at each outlet: 12-40 m3/h. Downstream demand is summed up the tree.
- Design flow into a tank: 1.5 x downstream demand for pumped transfers (pumps cycle on/off),
  1.3 x for gravity, and 1.6 x total demand for the inlet.
- Tank capacity: 3.2-5 hours of design inflow; height 5-10 m; area = capacity / height.
  `capacity_m3 = area_m2 x height_m` is checked by validation.
- Pumped transfers lift water 4-12 m and gravity transfers drop it 8-14 m.
- Pipe diameter: the smallest standard DN that keeps velocity at or below 1.8 m/s.
  Resistance comes from Darcy-Weisbach:
  `h = f (L/D) v^2 / 2g`, written as `h = r Q^2`.
- Valve: `Kv` = 5-7 x design flow. Head loss = `10.197 (Q / (Kv * opening))^2` m (linear trim).
- Pump curve: `H(Q) = H0 * s^2 - k Q^2`, with speed ratio s (affinity laws). H0 is chosen
  so that 70 % of the rated flow is still delivered at the worst static head (destination
  90 % full, source 10 % full). The rated head is 0.8 H0 and
  `k = (H0 - H_rated) / Q_rated^2`.

## 3. Time stepping

Telemetry interval is 900 s, integrated in 15 substeps of 60 s. In every substep:

| Element | Flow |
|---|---|
| Pump ON | operating point `Q = sqrt((H0*health*s^2 - Hs) / (k + R))`, with `Hs` = (destination water elevation) - (source water elevation) and `R` = pipes + valves; 0 if the pump cannot beat the static head |
| Gravity | `Q = sqrt(dh / R)`, with dh = head difference; check valve prevents reverse flow; the control valve closes as the destination fills (fully closed at 85 %) |
| Inlet | design flow x mode opening x level control (fully closed at 75 % intake fill) |
| Outlet | base demand x diurnal profile x mode demand factor x opening |

If a tank cannot supply its outflows, they are scaled down to what it holds. Volume
above capacity is lost as spill (recorded in the true outcomes). Volume is conserved
exactly: the true balance error `inflow - delivered - leak - spill - storage change`
is 0.000 m3 in every run.

**Control** acts at each telemetry boundary using true levels. A pump starts when its
destination tank is below 45 % full and its source is above 20 %. It stops above 85 %, or
when the source falls below 10 % (dry-run protection), or when its mode disables it.

**Demand profile**: `1 + 0.25 sin(2 pi (h - 14 + 6)/24) + a sin(4 pi (h + phase)/24)`. The
daily peak is at 14:00, and each outlet has its own harmonic (a = 0.03-0.08).

## 4. Operating modes (simulated operating changes)

| Mode | Demand factor | Settings |
|---|---:|---|
| NORMAL | 1.0 | all valves open, pumps 100 % |
| HIGH_DEMAND | 1.3 | as normal |
| LOW_DEMAND | 0.7 | pumps 92 % speed, inlet throttled to 80 % |
| MAINTENANCE | 0.9 | one pump disabled, its two valves closed |

Settings live in two N:M bridge tables. Each run follows a schedule template. The
fault-free run goes NORMAL -> HIGH_DEMAND -> MAINTENANCE -> NORMAL. A MAINTENANCE segment
writes `ACTIVE -> MAINTENANCE -> ACTIVE` lifecycle events for its pump, and the validator
checks that no pump starts while it is not ACTIVE.

## 5. Telemetry

| Measurement | Value | Noise (1 sigma) | Notes |
|---|---|---|---|
| FLOW (m3/h) | interval mean | 0.5 % + 0.2 | reads 0 below 0.5 m3/h (low-flow cut-off) |
| DIFF_PRESSURE (kPa) | interval mean of pump head x 9.80665 | 0.4 % + 0.3 | 0 when the pump is off |
| LEVEL (m) | value at interval end | 4 mm | 1 mm resolution |

`quality_flag` = BAD_RANGE when a value is outside the sensor span (derived).

## 6. Faults (ground truth only)

Each facility has 5 runs: NORMAL, LEAK, BLOCKAGE, PUMP_DEGRADATION and SENSOR_FAULT.
Onset falls at 35-50 % of the run, and the fault lasts to the end.

| Fault | Injection | Severity rule |
|---|---|---|
| LEAK | a fraction of the transfer flow is lost on the discharge pipe, downstream of the meter | 20 % / 12 % / 3 % by facility; expected ALARM iff fraction >= 6 % (the MB tolerance) |
| BLOCKAGE | discharge-pipe resistance multiplied so the design flow drops to 50-65 % | multiplier derived from `dR = (k + R)(1/ratio^2 - 1)` |
| PUMP_DEGRADATION | H0 x health, with health falling linearly to 0.72-0.80; efficiency x sqrt(health) | |
| SENSOR STUCK | level reading frozen at its last value (FAC-001) | process healthy |
| SENSOR BIAS | flow meter reads +25 % (FAC-002) | process healthy |
| SENSOR DRIFT | level reading drifts +0.06 m/h (FAC-003) | process healthy |

Faults are never on the pump that the MAINTENANCE mode isolates. The four fault targets
in a facility are four different pumped transfers.

## 7. Assumptions table

The 14 statements above that matter most are stored as `simulation_assumption` rows
(`ASM-001`..`ASM-014`). Every run links to the assumptions it used through
`simulation_run_assumption`.
