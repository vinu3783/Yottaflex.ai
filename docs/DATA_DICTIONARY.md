# Data dictionary

Generated from `src/plantsim/schema.py` by `plantsim docs`; the SQL DDL, the CSV parser and the
validator use the same definitions. All timestamps are UTC ISO 8601 (`YYYY-MM-DDTHH:MM:SSZ`).
Units are part of the column name and listed per field.

## Entities and keys

| Table | Kind | Primary key | Description |
|---|---|---|---|
| [facility](#facility) | MASTER | facility_id | A plant site. Every component belongs to exactly one facility. |
| [node](#node) | MASTER | node_id | Supertype of every graph node, so that pipes and sensors can hold real foreign keys. |
| [tank](#tank) | MASTER | tank_id | Storage tank with vertical walls (volume = area x level). |
| [pump](#pump) | MASTER | pump_id | Centrifugal pump with a quadratic head curve. |
| [valve](#valve) | MASTER | valve_id | Valve. INLET valves take water from outside the plant; OUTLET valves deliver it. |
| [pipe](#pipe) | MASTER | pipe_id | Directed edge between two nodes of the same facility (flow direction). |
| [sensor](#sensor) | MASTER | sensor_id | Instrument. LEVEL on tanks, DIFF_PRESSURE on pumps, FLOW on pipes. |
| [operating_mode](#operating_mode) | MASTER | mode_id | Named operating configuration. Settings live in the two bridge tables. |
| [operating_mode_valve_setting](#operating_mode_valve_setting) | BRIDGE | mode_id, valve_id | N:M between operating modes and valves. |
| [operating_mode_pump_setting](#operating_mode_pump_setting) | BRIDGE | mode_id, pump_id | N:M between operating modes and pumps. |
| [simulation_assumption](#simulation_assumption) | REFERENCE | assumption_id | Modelling assumptions referenced by simulation runs. |
| [simulation_run](#simulation_run) | TRANSACTION | simulation_run_id | One simulation of one facility. Fault injections are NOT stored here (ground truth). |
| [simulation_run_assumption](#simulation_run_assumption) | BRIDGE | simulation_run_id, assumption_id | N:M between runs and the assumptions they relied on. |
| [simulation_run_mode_segment](#simulation_run_mode_segment) | EVENT | simulation_run_id, segment_seq | Operating-mode schedule of a run. Segments are contiguous and cover the run. |
| [telemetry](#telemetry) | TRANSACTION | telemetry_id | Sensor readings. Contains no labels. |
| [pump_state_event](#pump_state_event) | EVENT | event_id | Pump run/stop history (SCADA run status). Append-only. |
| [valve_state_event](#valve_state_event) | EVENT | event_id | Valve setting history. Append-only. |
| [component_status_event](#component_status_event) | EVENT | event_id | Lifecycle history. Allowed: none->INSTALLED->ACTIVE<->MAINTENANCE, ->RETIRED. |
| [alarm](#alarm) | DERIVED | alarm_id | Alarms. DETECTOR alarms are derived from telemetry by documented rules; OPERATOR alarms are manual log entries (inputs). |
| [alarm_event](#alarm_event) | EVENT | event_id | Alarm lifecycle history. Append-only. |
| [alarm_review](#alarm_review) | DERIVED | review_id | Automatic review of operator alarms against detector evidence. |

Kinds: MASTER = reference entities generated first; BRIDGE = many-to-many; TRANSACTION = run
records; EVENT = append-only state history; DERIVED = calculated by documented rules.

## Relationships and cardinality

| Parent | Child | Cardinality | Optional | Implemented by |
|---|---|---|---|---|
| facility | node | 1:N | no | `node.facility_id` |
| node | tank / pump / valve | 1:1 (subtype) | no | `tank.tank_id = node.node_id, ...` |
| facility | pipe | 1:N | no | `pipe.facility_id` |
| node | pipe (upstream end) | 1:N | no | `pipe.from_node_id` |
| node | pipe (downstream end) | 1:N | no | `pipe.to_node_id` |
| facility | sensor | 1:N | no | `sensor.facility_id` |
| tank | sensor (LEVEL) | 1:1..2 | redundant sensor optional | `sensor.attached_node_id` |
| pump | sensor (DIFF_PRESSURE) | 1:1 | no | `sensor.attached_node_id` |
| pipe | sensor (FLOW) | 1:0..1 | yes | `sensor.attached_pipe_id` |
| operating_mode | valve | N:M | no | `operating_mode_valve_setting` |
| operating_mode | pump | N:M | no | `operating_mode_pump_setting` |
| pump | operating_mode (MAINTENANCE) | 1:0..N | yes | `operating_mode.maintenance_pump_id` |
| facility | simulation_run | 1:N | no | `simulation_run.facility_id` |
| simulation_run | simulation_assumption | N:M | no | `simulation_run_assumption` |
| simulation_run | simulation_run_mode_segment | 1:N | no | `segment.simulation_run_id` |
| operating_mode | simulation_run_mode_segment | 1:N | no | `segment.mode_id` |
| simulation_run | telemetry | 1:N | no | `telemetry.simulation_run_id` |
| sensor | telemetry | 1:N | no | `telemetry.sensor_id` |
| simulation_run | pump_state_event / valve_state_event | 1:N | no | `*.simulation_run_id` |
| node / pipe / sensor | component_status_event | 1:N | no | `exactly one of node_id, pipe_id, sensor_id` |
| simulation_run | component_status_event | 1:0..N | yes | `set when the change happened in a run` |
| simulation_run | alarm | 1:N | no | `alarm.simulation_run_id` |
| node / pipe / sensor / telemetry | alarm | 1:0..N | yes | `at most one of node_id, pipe_id, sensor_id` |
| alarm | alarm_event | 1:1..2 | no | `alarm_event.alarm_id` |
| alarm (OPERATOR) | alarm_review | 1:1 | no | `alarm_review.alarm_id` |

## Effective dates and lifecycle

- Components (tank, pump, valve, pipe, sensor) carry `install_date` and a `status` that is
  **derived** from `component_status_event` as of the reference date (2026-10-22). Allowed transitions:
  `(none) -> INSTALLED -> ACTIVE <-> MAINTENANCE`, `ACTIVE|MAINTENANCE -> RETIRED`.
- `operating_mode` is valid from `effective_from` to `effective_to` (open-ended when null).
- A run's mode schedule is `simulation_run_mode_segment` (contiguous, covers every step).
  A MAINTENANCE segment writes ACTIVE -> MAINTENANCE -> ACTIVE events for its pump.
- Pump run/stop, valve settings and alarm states are append-only event tables; nothing is
  updated in place.

## ID conventions

| Entity | Format | Example |
|---|---|---|
| Facility | `FAC-NNN` | FAC-001 |
| Pump / Tank / Valve | `PUMP-NNN` / `TANK-NNN` / `VALVE-NNN` | PUMP-001 |
| Pipe | `PIPE-NNN` | PIPE-001 |
| Sensor | `SNS-NNN` | SNS-001 |
| Operating mode | `MODE-NNN` | MODE-001 |
| Simulation run | `SIM-2026-NNNNNN` | SIM-2026-000001 |
| Telemetry | `TEL-NNNNNNN` | TEL-0000001 |
| Events | `PSE-` / `VSE-` / `CSE-` / `AEV-NNNNNN` | PSE-000001 |
| Alarm / review | `ALM-NNNNNN` / `ARV-NNNN` | ALM-000001 |
| Assumption / label | `ASM-NNN` / `GT-NNNN` | ASM-001 |

Counters are zero-padded, assigned in generation order, never reused.

## facility

A plant site. Every component belongs to exactly one facility.

Primary key: `facility_id`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| facility_id | TEXT | yes |  |  | yes | format FAC | Facility identifier |
| name | TEXT | yes |  |  | yes |  | Facility name |
| facility_type | TEXT | yes |  | WATER_TREATMENT, PROCESS_PLANT, UTILITY |  |  | Kind of site |
| region | TEXT | yes |  |  |  |  | Region label (synthetic) |
| timezone | TEXT | yes |  | UTC |  |  | All timestamps are UTC |
| commissioned_date | DATE | yes |  |  |  |  | Date the site went live |
| status | TEXT | yes |  | ACTIVE, INACTIVE |  |  | Site state |

## node

Supertype of every graph node, so that pipes and sensors can hold real foreign keys.

Primary key: `node_id`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| node_id | TEXT | yes |  |  | yes | format NODE | Pump, tank or valve ID |
| node_type | TEXT | yes |  | PUMP, TANK, VALVE |  |  | Node subtype |
| facility_id | TEXT | yes |  |  |  | FK -> facility.facility_id | Owning facility |

## tank

Storage tank with vertical walls (volume = area x level).

Primary key: `tank_id`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| tank_id | TEXT | yes |  |  | yes | format TANK; FK -> node.node_id | Tank ID |
| facility_id | TEXT | yes |  |  |  | FK -> facility.facility_id | Owning facility |
| tank_role | TEXT | yes |  | INTAKE, INTERMEDIATE, DISTRIBUTION |  | derived: INTAKE = has the inlet valve; DISTRIBUTION = has an outlet valve | Position in the process |
| height_m | REAL | yes | m | 1 .. 30 |  |  | Usable liquid height |
| area_m2 | REAL | yes | m2 | 1 .. |  |  | Cross-section area (vertical walls) |
| capacity_m3 | REAL | yes | m3 |  |  | derived: round(area_m2 * height_m, 2) | Usable volume |
| base_elevation_m | REAL | yes | m |  |  |  | Elevation of the tank floor |
| low_level_m | REAL | yes | m |  |  | derived: round(0.10 * height_m, 3) | Low |
| high_level_m | REAL | yes | m |  |  | derived: round(0.90 * height_m, 3) | High |
| install_date | DATE | yes |  |  |  | facility.commissioned_date <= install_date | Date the component was installed |
| status | TEXT | yes |  | INSTALLED, ACTIVE, MAINTENANCE, RETIRED |  | derived: to_status of the latest component_status_event at REFERENCE_DATE | Current lifecycle state |

## pump

Centrifugal pump with a quadratic head curve.

Primary key: `pump_id`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| pump_id | TEXT | yes |  |  | yes | format PUMP; FK -> node.node_id | Pump ID |
| facility_id | TEXT | yes |  |  |  | FK -> facility.facility_id | Owning facility |
| model_code | TEXT | yes |  |  |  |  | Synthetic model code |
| pump_type | TEXT | yes |  | CENTRIFUGAL |  |  | Pump type |
| rated_flow_m3h | REAL | yes | m3/h | 1 .. |  |  | Flow at the rated point |
| rated_head_m | REAL | yes | m | 1 .. |  |  | Head at the rated point |
| shutoff_head_m | REAL | yes | m |  |  | shutoff_head_m > rated_head_m | Head at zero flow, full speed |
| curve_k_m_per_m3h2 | REAL | yes | m/(m3/h)^2 |  |  | derived: (shutoff_head_m - rated_head_m) / rated_flow_m3h^2 | Curve coefficient: H = shutoff_head * s^2 - k Q^2 |
| bep_efficiency | REAL | yes |  | 0.3 .. 0.95 |  |  | Efficiency at best point |
| motor_rated_kw | REAL | yes | kW |  |  | derived: ceil(1.15 * rho g Q_rated H_rated / bep_efficiency) | Motor rating |
| has_vfd | BOOLEAN | yes |  |  |  |  | Variable-speed drive fitted |
| install_date | DATE | yes |  |  |  | facility.commissioned_date <= install_date | Date the component was installed |
| status | TEXT | yes |  | INSTALLED, ACTIVE, MAINTENANCE, RETIRED |  | derived: to_status of the latest component_status_event at REFERENCE_DATE | Current lifecycle state |

## valve

Valve. INLET valves take water from outside the plant; OUTLET valves deliver it.

Primary key: `valve_id`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| valve_id | TEXT | yes |  |  | yes | format VALVE; FK -> node.node_id | Valve |
| facility_id | TEXT | yes |  |  |  | FK -> facility.facility_id | Owning facility |
| valve_type | TEXT | yes |  | GATE, CONTROL |  |  | Isolation or modulating |
| valve_role | TEXT | yes |  | INLET, OUTLET, SUCTION, DISCHARGE, GRAVITY_CONTROL |  | must agree with the topology (see validation: topology_roles) | Function in the network |
| size_mm | INTEGER | yes | mm | 25 .. 600 |  |  | Nominal size |
| kv_m3h | REAL | yes | m3/h | 1 .. |  |  | Flow at 1 bar drop, fully open |
| normal_state | TEXT | yes |  | OPEN, CLOSED |  |  | Normal position |
| design_flow_m3h | REAL | no | m3/h |  |  | required for INLET (max supply) and OUTLET (base demand) | Boundary flow |
| install_date | DATE | yes |  |  |  | facility.commissioned_date <= install_date | Date the component was installed |
| status | TEXT | yes |  | INSTALLED, ACTIVE, MAINTENANCE, RETIRED |  | derived: to_status of the latest component_status_event at REFERENCE_DATE | Current lifecycle state |

## pipe

Directed edge between two nodes of the same facility (flow direction).

Primary key: `pipe_id`.
Unique: `(from_node_id, to_node_id)`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| pipe_id | TEXT | yes |  |  | yes | format PIPE | Pipe ID |
| facility_id | TEXT | yes |  |  |  | FK -> facility.facility_id | Owning facility |
| from_node_id | TEXT | yes |  |  |  | FK -> node.node_id | Upstream node |
| to_node_id | TEXT | yes |  |  |  | FK -> node.node_id; != from_node_id | Downstream |
| length_m | REAL | yes | m | 0.5 .. |  |  | Length |
| diameter_mm | INTEGER | yes | mm | 25 .. 600 |  |  | Inner diameter |
| darcy_friction_factor | REAL | yes |  | 0.005 .. 0.08 |  |  | Darcy friction f |
| resistance_m_per_m3h2 | REAL | yes | m/(m3/h)^2 |  |  | derived: f (L/D) / 2g / (A * 3600)^2 (Darcy-Weisbach) | Head loss coefficient |
| install_date | DATE | yes |  |  |  | facility.commissioned_date <= install_date | Date the component was installed |
| status | TEXT | yes |  | INSTALLED, ACTIVE, MAINTENANCE, RETIRED |  | derived: to_status of the latest component_status_event at REFERENCE_DATE | Current lifecycle state |

## sensor

Instrument. LEVEL on tanks, DIFF_PRESSURE on pumps, FLOW on pipes.

Primary key: `sensor_id`.
Exactly one of `attached_node_id, attached_pipe_id` is set.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| sensor_id | TEXT | yes |  |  | yes | format SNS | Sensor ID |
| facility_id | TEXT | yes |  |  |  | FK -> facility.facility_id | Owning facility |
| measurement_type | TEXT | yes |  | LEVEL, FLOW, DIFF_PRESSURE |  |  | What is measured |
| unit | TEXT | yes |  | m, m3/h, kPa |  | LEVEL->m, FLOW->m3/h, DIFF_PRESSURE->kPa | Engineering unit |
| attached_node_id | TEXT | no |  |  |  | FK -> node.node_id | Tank or pump the sensor sits on |
| attached_pipe_id | TEXT | no |  |  |  | FK -> pipe.pipe_id | Pipe a flow meter sits on |
| sensor_role | TEXT | yes |  | PRIMARY, REDUNDANT |  |  | Redundancy role |
| range_min | REAL | yes |  |  |  |  | Instrument span low (sensor unit) |
| range_max | REAL | yes |  |  |  | range_max > range_min | Span high (sensor unit) |
| accuracy_pct | REAL | yes | % of reading | 0 .. 5 |  |  | Rated accuracy |
| install_date | DATE | yes |  |  |  | facility.commissioned_date <= install_date | Date the component was installed |
| calibration_due_date | DATE | yes |  |  |  | > install_date | Next calibration |
| status | TEXT | yes |  | INSTALLED, ACTIVE, MAINTENANCE, RETIRED |  | derived: to_status of the latest component_status_event at REFERENCE_DATE | Current lifecycle state |

## operating_mode

Named operating configuration. Settings live in the two bridge tables.

Primary key: `mode_id`.
Unique: `(facility_id, mode_code)`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| mode_id | TEXT | yes |  |  | yes | format MODE | Mode ID |
| facility_id | TEXT | yes |  |  |  | FK -> facility.facility_id | Owning facility |
| mode_code | TEXT | yes |  | NORMAL, HIGH_DEMAND, LOW_DEMAND, MAINTENANCE |  |  | Mode name |
| description | TEXT | yes |  |  |  |  | What the mode does |
| demand_factor | REAL | yes |  | 0.1 .. 2.0 |  |  | Multiplier on base demand |
| maintenance_pump_id | TEXT | no |  |  |  | FK -> pump.pump_id; required when mode_code = MAINTENANCE | Pump isolated in this mode |
| effective_from | DATE | yes |  |  |  |  | First day the mode may be used |
| effective_to | DATE | no |  |  |  | >= effective_from | Last day |

## operating_mode_valve_setting

N:M between operating modes and valves.

Primary key: `mode_id, valve_id`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| mode_id | TEXT | yes |  |  |  | FK -> operating_mode.mode_id | Mode |
| valve_id | TEXT | yes |  |  |  | FK -> valve.valve_id; same facility as mode | Valve |
| opening_pct | REAL | yes | % | 0 .. 100 |  |  | Opening (limit for CONTROL) |
| valve_state | TEXT | yes |  | OPEN, CLOSED, THROTTLED |  | derived: 0 -> CLOSED, 100 -> OPEN, otherwise THROTTLED | Named state |

## operating_mode_pump_setting

N:M between operating modes and pumps.

Primary key: `mode_id, pump_id`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| mode_id | TEXT | yes |  |  |  | FK -> operating_mode.mode_id | Mode |
| pump_id | TEXT | yes |  |  |  | FK -> pump.pump_id; same facility as mode | Pump |
| enabled | BOOLEAN | yes |  |  |  |  | Pump may run in this mode |
| speed_pct | REAL | yes | % | 50 .. 100 |  |  | Drive speed |

## simulation_assumption

Modelling assumptions referenced by simulation runs.

Primary key: `assumption_id`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| assumption_id | TEXT | yes |  |  | yes | format ASM | Assumption ID |
| category | TEXT | yes |  |  |  |  | Topic |
| statement | TEXT | yes |  |  |  |  | The assumption in plain words |
| value | TEXT | no |  |  |  |  | Parameter value, if any |
| unit | TEXT | no |  |  |  |  | Unit of value |

## simulation_run

One simulation of one facility. Fault injections are NOT stored here (ground truth).

Primary key: `simulation_run_id`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| simulation_run_id | TEXT | yes |  |  | yes | format SIM | Run ID |
| facility_id | TEXT | yes |  |  |  | FK -> facility.facility_id | Simulated facility |
| run_type | TEXT | yes |  | BASELINE, WHATIF |  |  | Dataset run or what-if |
| start_ts | TIMESTAMP | yes |  |  |  |  | Start of first interval (UTC) |
| end_ts | TIMESTAMP | yes |  |  |  | derived: start_ts + steps * timestep_s | End (UTC) |
| timestep_s | INTEGER | yes | s | 60 .. |  |  | Telemetry interval |
| steps | INTEGER | yes |  | 1 .. |  |  | Number of intervals |
| seed | INTEGER | yes |  |  |  |  | Seed for this run's noise and demand |
| model_version | TEXT | yes |  |  |  |  | Simulator version |
| input_config_json | JSON | yes |  |  |  | required: a run without its input configuration cannot be reproduced | Operating configuration: mode schedule, initial levels, demand, overrides |
| config_hash | TEXT | yes |  |  |  | derived: sha256 of input_config_json | Fingerprint of the input configuration |
| output_hash | TEXT | no |  |  |  |  | sha256 of the simulator's clean telemetry (before data-quality injections) |
| output_summary_json | JSON | yes |  |  |  | derived: measured totals from this run's telemetry (see summary.py) | Measured outcomes |
| status | TEXT | yes |  | COMPLETED, FAILED |  |  | Run state |

## simulation_run_assumption

N:M between runs and the assumptions they relied on.

Primary key: `simulation_run_id, assumption_id`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| simulation_run_id | TEXT | yes |  |  |  | FK -> simulation_run.simulation_run_id | Run |
| assumption_id | TEXT | yes |  |  |  | FK -> simulation_assumption.assumption_id | Used |

## simulation_run_mode_segment

Operating-mode schedule of a run. Segments are contiguous and cover the run.

Primary key: `simulation_run_id, segment_seq`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| simulation_run_id | TEXT | yes |  |  |  | FK -> simulation_run.simulation_run_id | Run |
| segment_seq | INTEGER | yes |  | 1 .. |  |  | Order within the run |
| mode_id | TEXT | yes |  |  |  | FK -> operating_mode.mode_id; same facility | Mode |
| start_step | INTEGER | yes |  | 1 .. |  |  | First interval in this mode |
| end_step | INTEGER | yes |  |  |  | >= start_step | Last interval in this mode |
| start_ts | TIMESTAMP | yes |  |  |  |  | Segment start |
| end_ts | TIMESTAMP | yes |  |  |  |  | Segment end |

## telemetry

Sensor readings. Contains no labels.

Primary key: `telemetry_id`.
Unique: `(simulation_run_id, sensor_id, ts_utc)`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| telemetry_id | TEXT | yes |  |  | yes | format TEL | Reading ID |
| simulation_run_id | TEXT | yes |  |  |  | FK -> simulation_run.simulation_run_id | Run |
| sensor_id | TEXT | yes |  |  |  | FK -> sensor.sensor_id | Sensor |
| ts_utc | TIMESTAMP | yes |  |  |  | start_ts < ts_utc <= end_ts | Interval end |
| step | INTEGER | yes |  | 1 .. |  |  | Interval number |
| value | REAL | yes |  |  |  | LEVEL readings must be <= tank height (data-quality rule DQ-001) | FLOW/DIFF_PRESSURE: interval mean; LEVEL: value at interval end |
| unit | TEXT | yes |  | m, m3/h, kPa |  | = sensor.unit | Unit |
| quality_flag | TEXT | yes |  | GOOD, BAD_RANGE |  | derived: BAD_RANGE when value is outside the sensor span | Instrument quality |

## pump_state_event

Pump run/stop history (SCADA run status). Append-only.

Primary key: `event_id`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| event_id | TEXT | yes |  |  | yes | format PSE | Event ID |
| simulation_run_id | TEXT | yes |  |  |  | FK -> simulation_run.simulation_run_id | Run |
| pump_id | TEXT | yes |  |  |  | FK -> pump.pump_id | Pump |
| ts_utc | TIMESTAMP | yes |  |  |  |  | When the state took effect |
| from_state | TEXT | no |  | ON, OFF |  |  | Before |
| to_state | TEXT | yes |  | ON, OFF |  | != from_state | After |
| reason | TEXT | yes |  | INITIAL, LEVEL_LOW_START, LEVEL_HIGH_STOP, SUCTION_LOW_STOP, MODE_DISABLED |  |  | Control rule that fired |

## valve_state_event

Valve setting history. Append-only.

Primary key: `event_id`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| event_id | TEXT | yes |  |  | yes | format VSE | Event ID |
| simulation_run_id | TEXT | yes |  |  |  | FK -> simulation_run.simulation_run_id | Run |
| valve_id | TEXT | yes |  |  |  | FK -> valve.valve_id | Valve |
| ts_utc | TIMESTAMP | yes |  |  |  |  | When the setting took effect |
| from_opening_pct | REAL | no | % |  |  |  | Before |
| to_opening_pct | REAL | yes | % | 0 .. 100 |  |  | After |
| from_state | TEXT | no |  | OPEN, CLOSED, THROTTLED |  |  |  |
| to_state | TEXT | yes |  | OPEN, CLOSED, THROTTLED |  |  | After |
| reason | TEXT | yes |  | INITIAL, MODE_CHANGE, OVERRIDE |  |  | Cause |

## component_status_event

Lifecycle history. Allowed: none->INSTALLED->ACTIVE<->MAINTENANCE, ->RETIRED.

Primary key: `event_id`.
Exactly one of `node_id, pipe_id, sensor_id` is set.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| event_id | TEXT | yes |  |  | yes | format CSE | Event ID |
| component_type | TEXT | yes |  | PUMP, TANK, VALVE, PIPE, SENSOR |  |  |  |
| node_id | TEXT | no |  |  |  | FK -> node.node_id | If a node |
| pipe_id | TEXT | no |  |  |  | FK -> pipe.pipe_id | If a pipe |
| sensor_id | TEXT | no |  |  |  | FK -> sensor.sensor_id | If a sensor |
| from_status | TEXT | no |  | INSTALLED, ACTIVE, MAINTENANCE, RETIRED |  |  | Before |
| to_status | TEXT | yes |  | INSTALLED, ACTIVE, MAINTENANCE, RETIRED |  |  | After |
| effective_at | TIMESTAMP | yes |  |  |  |  | When the change took effect |
| reason | TEXT | yes |  | INSTALLATION, COMMISSIONING, PLANNED_MAINTENANCE, RETURN_TO_SERVICE |  |  |  |
| simulation_run_id | TEXT | no |  |  |  | FK -> simulation_run.simulation_run_id | Set when the change happened inside a run |

## alarm

Alarms. DETECTOR alarms are derived from telemetry by documented rules; OPERATOR alarms are manual log entries (inputs).

Primary key: `alarm_id`.
At most one of `node_id, pipe_id, sensor_id` is set.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| alarm_id | TEXT | yes |  |  | yes | format ALM | Alarm ID |
| simulation_run_id | TEXT | yes |  |  |  | FK -> simulation_run.simulation_run_id | Run |
| facility_id | TEXT | yes |  |  |  | FK -> facility.facility_id | Facility |
| alarm_source | TEXT | yes |  | DETECTOR, OPERATOR |  |  | Who raised it |
| alarm_type | TEXT | yes |  | LEAK_SUSPECTED, BLOCKAGE_SUSPECTED, PUMP_DEGRADATION, LEVEL_SENSOR_FAULT, FLOW_SENSOR_FAULT, MASS_BALANCE_UNEXPLAINED, DQ_LEVEL_EXCEEDS_CAPACITY |  |  | Diagnosis |
| severity | TEXT | yes |  | LOW, MEDIUM, HIGH |  |  | Severity |
| rule_id | TEXT | no |  |  |  | required for DETECTOR | Rule |
| node_id | TEXT | no |  |  |  | FK -> node.node_id | Main component |
| pipe_id | TEXT | no |  |  |  | FK -> pipe.pipe_id | Main pipe |
| sensor_id | TEXT | no |  |  |  | FK -> sensor.sensor_id | Main sensor |
| telemetry_id | TEXT | no |  |  |  | FK -> telemetry.telemetry_id | Offending reading (data-quality alarms) |
| suspected_components | TEXT | no |  |  |  |  | Semicolon-separated IDs on the suspected path |
| raised_at | TIMESTAMP | yes |  |  |  |  | When the rule first held |
| cleared_at | TIMESTAMP | no |  |  |  | >= raised_at | When ended |
| state | TEXT | yes |  | ACTIVE, CLEARED |  |  | Current state |
| evidence_value | REAL | no |  |  |  |  | Peak rule statistic |
| threshold_value | REAL | no |  |  |  |  | Rule threshold |
| evidence_unit | TEXT | no |  |  |  |  | Unit of evidence/threshold |
| message | TEXT | yes |  |  |  |  | Human-readable explanation |

## alarm_event

Alarm lifecycle history. Append-only.

Primary key: `event_id`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| event_id | TEXT | yes |  |  | yes | format AEV | Event ID |
| alarm_id | TEXT | yes |  |  |  | FK -> alarm.alarm_id | Alarm |
| ts_utc | TIMESTAMP | yes |  |  |  |  | When |
| event_type | TEXT | yes |  | RAISED, CLEARED |  |  | Transition |
| from_state | TEXT | no |  | ACTIVE |  |  | Before |
| to_state | TEXT | yes |  | ACTIVE, CLEARED |  |  | After |

## alarm_review

Automatic review of operator alarms against detector evidence.

Primary key: `review_id`.

| Field | Type | Required | Unit | Allowed / range | Unique | Validation / derivation | Description |
|---|---|---|---|---|---|---|---|
| review_id | TEXT | yes |  |  | yes | format ARV | Review ID |
| alarm_id | TEXT | yes |  |  |  | FK -> alarm.alarm_id | Operator alarm reviewed |
| decision | TEXT | yes |  | CONFIRMED, RECLASSIFY_AS_SENSOR_FAULT, UNCONFIRMED |  |  | Outcome of comparing with detector evidence |
| supporting_alarm_ids | TEXT | no |  |  |  |  | Detector alarms used |
| explanation | TEXT | yes |  |  |  |  | Why |

## ground_truth (separate folder, never an input)

`ground_truth/ground_truth.csv` has one row per label; `ground_truth.json` adds the fault
injections and true (noise-free) outcomes per run.

| Field | Description |
|---|---|
| gt_id | Label ID (GT-NNNN) |
| category | NONE, PROCESS_FAULT, SENSOR_FAULT, EDGE_CASE, LABEL_CHECK |
| issue_type | e.g. LEAK, BLOCKAGE, PUMP_DEGRADATION, SENSOR_STUCK, PIPE_REFERENCES_MISSING_NODE |
| simulation_run_id | Run the label applies to (empty for master-data cases) |
| facility_id | Facility |
| component_id | Faulty component (pipe, pump, sensor or tank) |
| sensor_id | Sensor involved, if any |
| record_table | Table of the labelled record |
| record_id | Key of the labelled record |
| onset_step | First faulty interval |
| onset_ts | Start of the first faulty interval (UTC) |
| severity | Fault size (leak fraction, flow ratio, final health, bias, drift m/h, level/height) |
| expected_alarm_type | Alarm a correct detector raises |
| expected_decision | ALARM, NO_ALARM, FLAG_DATA_QUALITY, REJECT_RECORD, REJECT_NOT_REPRODUCIBLE, ... |
| rule_basis | The documented rule the label was derived from |

## Detector thresholds

| Parameter | Value |
|---|---|
| MB_WINDOW_STEPS | 8 |
| MB_PERSIST_STEPS | 4 |
| MB_ABS_TOL_M3H | 1.5 |
| MB_REL_TOL | 0.06 |
| LEVEL_DIFF_TOL_M | 0.08 |
| LEVEL_PERSIST_STEPS | 3 |
| FLATLINE_STEPS | 6 |
| FLATLINE_MIN_MOVE_M | 0.05 |
| CURVE_WINDOW_STEPS | 4 |
| CURVE_PERSIST_STEPS | 3 |
| CURVE_REL_TOL | 0.03 |
| CURVE_ABS_TOL_M | 0.5 |
| BLOCK_RATIO | 0.8 |
| BLOCK_PERSIST_STEPS | 3 |
