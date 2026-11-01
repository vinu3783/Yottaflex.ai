# Detection evaluation

| Metric | Value |
|---|---|
| fault recall | 1.0 |
| alarm precision | 1.0 |
| faults detected | 11/11 |
| false alarms | 0 |
| false alarms in normal runs | 0 |
| mean detection delay min | 193.6 |
| edge cases handled | 5/5 |
| data quality recall | 1.0 |
| data quality precision | 1.0 |
| dq alarms from labelled sensor faults | 0 |

## Injected faults

| Run | Fault | Component | Expected | Outcome | Delay (min) | Alarm types raised in run |
|---|---|---|---|---|---:|---|
| SIM-2026-000002 | LEAK | PIPE-017 | LEAK_SUSPECTED | TRUE_POSITIVE | 90 | LEAK_SUSPECTED |
| SIM-2026-000003 | BLOCKAGE | PIPE-029 | BLOCKAGE_SUSPECTED | TRUE_POSITIVE | 45 | BLOCKAGE_SUSPECTED |
| SIM-2026-000004 | PUMP_DEGRADATION | PUMP-001 | PUMP_DEGRADATION | TRUE_POSITIVE | 315 | PUMP_DEGRADATION |
| SIM-2026-000005 | SENSOR_STUCK | SNS-002 | LEVEL_SENSOR_FAULT | TRUE_POSITIVE | 75 | LEVEL_SENSOR_FAULT |
| SIM-2026-000007 | LEAK | PIPE-069 | LEAK_SUSPECTED | TRUE_POSITIVE | 105 | LEAK_SUSPECTED |
| SIM-2026-000008 | BLOCKAGE | PIPE-047 | BLOCKAGE_SUSPECTED | TRUE_POSITIVE | 60 | BLOCKAGE_SUSPECTED |
| SIM-2026-000009 | PUMP_DEGRADATION | PUMP-012 | PUMP_DEGRADATION | TRUE_POSITIVE | 255 | PUMP_DEGRADATION |
| SIM-2026-000010 | SENSOR_BIAS | SNS-054 | FLOW_SENSOR_FAULT | TRUE_POSITIVE | 315 | FLOW_SENSOR_FAULT |
| SIM-2026-000012 | LEAK | PIPE-095 | no alarm | TRUE_NEGATIVE |  | - |
| SIM-2026-000013 | BLOCKAGE | PIPE-077 | BLOCKAGE_SUSPECTED | TRUE_POSITIVE | 315 | BLOCKAGE_SUSPECTED |
| SIM-2026-000014 | PUMP_DEGRADATION | PUMP-016 | PUMP_DEGRADATION | TRUE_POSITIVE | 435 | PUMP_DEGRADATION |
| SIM-2026-000015 | SENSOR_DRIFT | SNS-076 | LEVEL_SENSOR_FAULT | TRUE_POSITIVE | 120 | LEVEL_SENSOR_FAULT |

## Per alarm type

| Type | Faults | Detected | Alarms | Correct | Recall | Precision |
|---|---:|---:|---:|---:|---:|---:|
| BLOCKAGE_SUSPECTED | 3 | 3 | 3 | 3 | 1.0 | 1.0 |
| FLOW_SENSOR_FAULT | 1 | 1 | 1 | 1 | 1.0 | 1.0 |
| LEAK_SUSPECTED | 2 | 2 | 2 | 2 | 1.0 | 1.0 |
| LEVEL_SENSOR_FAULT | 2 | 2 | 2 | 2 | 1.0 | 1.0 |
| PUMP_DEGRADATION | 3 | 3 | 3 | 3 | 1.0 | 1.0 |

## Required edge cases

| Edge case | Handled | How |
|---|---|---|
| Pipe references missing node | yes | foreign_keys + topology_dangling_pipes flag it; SQL load moves it to rejected_record; topology builder ignores it |
| Tank level exceeds capacity | yes | DQ-001 raised 6/6 alarms; validation level_within_capacity lists exactly these readings |
| Flow conservation violated beyond tolerance | yes | MB-001 detected 2/2 violations above tolerance and stayed silent on 1/1 within-tolerance case(s) |
| Simulation output lacks input configuration | yes | required_fields + run_reproducibility_inputs flag it; detector skips it; SQL rejects it |
| Sensor fault labelled as process fault | yes | alarm_review reclassifies the operator LEAK alarm because the detector attributes the imbalance to a flow-meter fault (SNS-003) |

## False alarms

None.

## Facility mass balance on normal runs (measured)

| Run | Residual % of inflow | True balance error m3 |
|---|---:|---:|
| SIM-2026-000001 | 0.044 | 0.0 |
| SIM-2026-000006 | -0.009 | 0.0 |
| SIM-2026-000011 | -0.009 | 0.0 |
