# Validation summary

**Overall: PASS** - EXPECTED_EXCEPTIONS: 5, PASS: 13

EXPECTED_EXCEPTIONS means the check found exactly the labelled edge cases and nothing else.

| Check | Category | Status | Violations | Labelled | Description |
|---|---|---|---:|---:|---|
| primary_key_unique | INTEGRITY | PASS | 0 | 0 | Primary keys are unique in every table |
| id_and_format | INTEGRITY | PASS | 0 | 0 | IDs follow the naming convention; dates/JSON parse |
| required_fields | INTEGRITY | EXPECTED_EXCEPTIONS | 1 | 1 | Required fields are present |
| allowed_values | INTEGRITY | PASS | 0 | 0 | Enumerations and numeric ranges |
| exclusive_references | INTEGRITY | PASS | 0 | 0 | Polymorphic references use exactly one FK |
| foreign_keys | INTEGRITY | EXPECTED_EXCEPTIONS | 1 | 1 | Every foreign key resolves to an existing parent |
| duplicates | INTEGRITY | PASS | 0 | 0 | No duplicate natural keys (e.g. one reading per sensor per time) |
| facility_consistency | INTEGRITY | PASS | 0 | 0 | Related records belong to the same facility |
| topology_dangling_pipes | TOPOLOGY | EXPECTED_EXCEPTIONS | 1 | 1 | Every pipe connects two existing nodes |
| topology_connected | TOPOLOGY | PASS | 0 | 0 | Each facility is one connected graph, tanks reachable from inlet |
| topology_roles | TOPOLOGY | PASS | 0 | 0 | Pumps/valves have the pipe counts their role requires |
| units_explicit | UNITS | PASS | 0 | 0 | Every reading carries its sensor's engineering unit |
| date_ordering | DATES | PASS | 0 | 0 | Dates are ordered and every timestamp falls inside its run |
| arithmetic_reconciliation | DERIVED | PASS | 0 | 0 | Derived values recomputed from their sources |
| run_reproducibility_inputs | REPRODUCIBILITY | EXPECTED_EXCEPTIONS | 1 | 1 | Every run with outputs can be re-run |
| lifecycle_transitions | LIFECYCLE | PASS | 0 | 0 | Allowed state transitions; status matches history |
| level_within_capacity | PLAUSIBILITY | EXPECTED_EXCEPTIONS | 6 | 6 | Level readings never exceed tank height |
| ground_truth_separation | LEAKAGE | PASS | 0 | 0 | Labels only in ground_truth/, and every label points at a real record |

## Exceptions found

- **required_fields**: simulation_run:SIM-2026-000016: seed is null; simulation_run:SIM-2026-000016: input_config_json is null; simulation_run:SIM-2026-000016: config_hash is null
- **foreign_keys**: pipe:PIPE-108: to_node_id=TANK-031 not found in node
- **topology_dangling_pipes**: pipe:PIPE-108: to_node_id not an existing node
- **run_reproducibility_inputs**: simulation_run:SIM-2026-000016: has outputs but no input_config_json, config_hash, seed
- **level_within_capacity**: telemetry:TEL-0001670: level 5.974 m above tank height; telemetry:TEL-0008417: level 11.647 m above tank height; telemetry:TEL-0008490: level 6.453 m above tank height; telemetry:TEL-0008825: level 9.037 m above tank height; telemetry:TEL-0008983: level 5.941 m above tank height

## Row counts

| Table | Rows |
|---|---:|
| facility | 3 |
| node | 110 |
| tank | 30 |
| pump | 20 |
| valve | 60 |
| pipe | 108 |
| sensor | 100 |
| operating_mode | 12 |
| operating_mode_valve_setting | 240 |
| operating_mode_pump_setting | 80 |
| simulation_assumption | 14 |
| simulation_run | 16 |
| simulation_run_assumption | 210 |
| simulation_run_mode_segment | 32 |
| telemetry | 10,000 |
| pump_state_event | 198 |
| valve_state_event | 316 |
| component_status_event | 654 |
| alarm | 19 |
| alarm_event | 26 |
| alarm_review | 2 |
