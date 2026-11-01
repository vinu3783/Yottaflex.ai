# Requirements trace

How each item in the brief is met, and where to check it.

| Brief item | Where it is met | Evidence |
|---|---|---|
| 1. Master data first: Facility, Pump, Tank, Valve, Pipe | `masterdata.build_master` builds facility -> node -> tank/pump/valve -> pipe -> sensor -> modes | `csv/facility.csv` ... |
| 2. Dependent records use valid parent IDs | runs, telemetry and events are created from the master tables; FK check | validation `foreign_keys` (only the labelled PIPE-108) |
| 3. Topology graph, pump curves, tank capacity, valve states, flow/pressure/level readings, mass balance, fault scenarios, assumptions and outcomes | `topology.py`, `physics.py`, `simulator.py`, `scenarios.py`; `simulation_assumption`; `simulation_run.output_summary_json`; true outcomes in ground truth | [SIMULATION_MODEL.md](SIMULATION_MODEL.md) |
| 4. State changes as events, no overwriting | `pump_state_event`, `valve_state_event`, `component_status_event`, `alarm_event`; what-if runs get new IDs | validation `lifecycle_transitions` |
| 5. Labels from documented rules | `generate.py` derives every label from the injected fault and a stated rule (`rule_basis` column) | `ground_truth/ground_truth.csv` |
| Entities, PK/FK, cardinality, optional, effective dates, lifecycle, bridge tables | `schema.py`; bridge tables `operating_mode_valve_setting`, `operating_mode_pump_setting`, `simulation_run_assumption` | [DATA_DICTIONARY.md](DATA_DICTIONARY.md) |
| ID conventions | `ids.py` | validation `id_and_format` |
| Step 1 data dictionary (type, required, allowed values, units, uniqueness, rule) | generated from the schema | [DATA_DICTIONARY.md](DATA_DICTIONARY.md) |
| Step 4 volumes, fixed seed, parameterised scale | 3 / 20 / 30 / 60, 100,000 telemetry, seed 20261022, `--profile starter|full`, `--telemetry-points` | `manifest.json`, `tests/test_dataset.py` |
| Step 5 labelled edge cases (5) | `generate.py` section "labelled edge cases" | README table; `evaluation.md` "Required edge cases" |
| Step 6 derived values from sources | capacity, resistance, curve k, motor kW, valve state, quality flag, status, run summaries, config hash | validation `arithmetic_reconciliation` |
| Step 7 separate ground truth, no leakage | `ground_truth/`; detector reads CSV inputs only | validation `ground_truth_separation`; `test_detector_does_not_read_ground_truth` |
| Step 8 validation (PK, FK, enums, dates, arithmetic, lifecycle, duplicates) | `validation.py`, 18 checks | `reports/validation_summary.md` |
| Step 9 exports + README, dictionary, command, seed, manifest, validation summary | `csv/`, `json/`, `sql/`, `manifest.json`, `reports/` | `data/full/` |
| Step 10 regenerate twice, compare counts/checksums | `plantsim repro-check` | `reports/reproducibility.json` |
| Topology connected; units explicit | validation `topology_connected`, `units_explicit` | validation summary |
| Scenarios detectable | detector + evaluation: 11/11, boundary case silent, 0 false alarms; 220/220 over 20 seeds | `reports/evaluation.md`, `reports/robustness_sweep.json` |
| Simulation inputs/outputs reproducible | config hash + output hash per run; re-simulation 15/15 | `reports/reproducibility.json` |
| Simulate operating changes | operating modes and schedules in every run; `plantsim whatif` | [examples/](examples/) |
| Lawful provenance | fully synthetic; no external data | `manifest.json` `provenance` |
