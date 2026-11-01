# Prompt 1

The brief was pasted into the chat, followed by the candidate's own line "we complted all right"
(asking whether it had already been done; it had not).

````text
Business Context / Problem
Model pumps, tanks, valves, sensors and flows to detect abnormal operating conditions and simulate operating changes.

Objective and Scope
Build a working solution for this end-to-end workflow: Model pumps, tanks, valves, sensors and flows to detect abnormal operating conditions and simulate operating changes. The solution should expose key decisions and exceptions, preserve traceability, and produce measurable outputs. Medium complexity means multiple related entities, conditional rules, at least one exception path and meaningful evaluation metrics.

Primary Actors / Systems
Plant operator, maintenance/quality engineer, production planner and OT/IT administrator

Business Workflow and Rules
1. Generate/reference master data first: Facility, Pump, Tank, Valve, Pipe.
2. Generate dependent transaction/event records using valid parent IDs.
3. Apply business logic: Create a topology graph with valid connections; define nominal pump curves, tank capacity and valve states; generate sensor readings for flow, pressure and tank level; derive approximate mass balance; inject leak, blockage, pump degradation and sensor-fault scenarios; record simulation assumptions and outcomes.
4. Persist state changes as explicit records/events where relevant; do not overwrite history.
5. Generate outcomes and ground-truth labels from documented rules, not independent random assignment.

Entities and Relationships
Core entities: Facility, Pump, Tank, Valve, Pipe, Sensor, Telemetry, OperatingMode, Alarm, SimulationRun. Define a primary key for every entity and foreign keys for every relationship. Document cardinality (1:1, 1:N, N:M), optional relationships, effective dates and lifecycle states. Use bridge tables for many-to-many relationships; retain event history for state changes; validate all joins. For deliberate anomaly cases, keep an issue_type/ground_truth label rather than silently corrupting unrelated rows.

ID / Nomenclature Conventions
FAC-001; PUMP-001; TANK-001; VALVE-001; SNS-001; SIM-2026-000001.

Synthetic Data Generation Instructions (Granular)
Step 1 — Write a data dictionary for: Facility, Pump, Tank, Valve, Pipe, Sensor, Telemetry, OperatingMode, Alarm, SimulationRun. For each field specify type, required/optional, allowed values, units, uniqueness and validation rule.
Step 2 — Create parent/master tables first, then child and event tables. Apply this domain-specific generation logic: Create a topology graph with valid connections; define nominal pump curves, tank capacity and valve states; generate sensor readings for flow, pressure and tank level; derive approximate mass balance; inject leak, blockage, pump degradation and sensor-fault scenarios; record simulation assumptions and outcomes.
Step 3 — Apply deterministic IDs and naming: FAC-001; PUMP-001; TANK-001; VALVE-001; SNS-001; SIM-2026-000001. Keep counters zero-padded, IDs immutable, and referenced IDs exactly consistent across files.
Step 4 — Generate the baseline volume: Generate 3 facilities, 20 pumps, 30 tanks, 60 valves and 100,000 telemetry points. Ensure every component belongs to a facility and every pipe connects two valid nodes. Use a fixed seed (e.g. 20261022) and parameterize scale so starter and full datasets use the same generator.
Step 5 — Generate valid baseline records first, then inject labelled edge cases: Pipe references missing node; tank level exceeds capacity; flow conservation violated beyond tolerance; simulation output lacks input configuration; sensor fault labelled as process fault.
Step 6 — Calculate derived values (totals, balances, scores, statuses and dates) from source records rather than generating them independently.
Step 7 — Produce a separate ground_truth.csv/json for known anomalies, expected decisions or labels; never leak it into input features.
Step 8 — Run validation: primary-key uniqueness, foreign-key integrity, allowed enums, date ordering, arithmetic reconciliation, lifecycle transitions and duplicate checks.
Step 9 — Export CSV/JSON/SQL seed data plus README, schema/data dictionary, generator command, fixed seed, source manifest and validation summary.
Step 10 — Regenerate twice with the same seed and compare row counts/checksums to demonstrate reproducibility.

Suggested Volume and Coverage
Generate 3 facilities, 20 pumps, 30 tanks, 60 valves and 100,000 telemetry points. Ensure every component belongs to a facility and every pipe connects two valid nodes.

Required Edge Cases / Negative Scenarios
Pipe references missing node; tank level exceeds capacity; flow conservation violated beyond tolerance; simulation output lacks input configuration; sensor fault labelled as process fault.

Validation Rules / Acceptance Criteria
Topology is connected where required; units are explicit; generated scenarios are detectable; simulation inputs and outputs are reproducible.

Evaluation Focus for Generated Data
Review: (a) realistic distributions and meaningful variation; (b) consistent IDs and valid joins; (c) dependency/lifecycle consistency across entities; (d) correct derived values and reconciliation; (e) normal, boundary, failure and exception coverage; (f) separate ground truth; (g) fixed-seed reproducibility, documentation and lawful provenance.

Public Data Guidance / Lawful Use
External data is optional. If used, record source URL, dataset/version, retrieval date, license/terms, attribution, transformations and fields discarded. Use only data you are legally permitted to access and redistribute; do not bypass access controls or collect confidential/personal information. If licensing or suitability is unclear, use synthetic data and cite public reference data only for schema inspiration.

we complted all right
````
