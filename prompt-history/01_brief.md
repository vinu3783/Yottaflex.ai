# 1. Define the project

> **Refined prompt — editorial reconstruction, not a verbatim transcript.**
> This version expresses the implemented project scope clearly for reviewers.

```text
Design an end-to-end, reproducible synthetic plant simulation for pumps, tanks, valves,
pipes and sensors. The system should help plant operators and maintenance engineers
understand abnormal conditions, inspect alarms and evaluate operating changes.

Build a working Python project that:

1. Models facilities, equipment, topology, operating modes, sensor telemetry, simulation
   runs, alarms and state-change events. Use stable, zero-padded IDs, real primary and
   foreign keys, explicit units, documented relationships and lifecycle history.
2. Generates physically plausible plant telemetry from documented hydraulic rules.
   Derive labels and outcomes from injected scenarios and measured signals; do not assign
   labels independently at random.
3. Includes a full profile with 3 facilities, 20 pumps, 30 tanks, 60 valves, 100 sensors
   and exactly 100,000 telemetry points, plus a smaller starter profile using the same
   generator. Use a fixed default seed and allow reproducible runs with alternate seeds.
4. Injects and records meaningful process and sensor scenarios, including leaks,
   blockages, pump degradation and sensor faults. Keep ground truth separate from detector
   inputs. Include labelled negative cases for a dangling pipe, an over-capacity level,
   mass-balance violations and a boundary case within tolerance, a run missing its input
   configuration, and a sensor fault initially reported as a process fault.
5. Provides rule-based detection, validation and evaluation. Report detection outcomes,
   precision/recall, false alarms, detection delay, data-quality results and handling of
   each required edge case.
6. Exports documented CSV, JSON and SQL data. Include a manifest with seed, provenance,
   row counts and checksums; document the schema, simulation assumptions, design
   decisions, validation, limitations and operating instructions.
7. Supports a what-if simulation that compares an operating change against a baseline
   without overwriting prior run history.

Acceptance criteria:
- IDs and relationships are consistent; topology is connected where required.
- Units, assumptions, derivations and expected exceptions are documented.
- Validation distinguishes expected labelled exceptions from unlabelled defects.
- Repeating generation with the same seed produces byte-identical outputs.
- Tests cover core physics, detection, validation failures, reproducibility and what-if
  behavior.
- Runtime code uses only the Python standard library; use pytest and Ruff for development.

Use synthetic data unless external data is genuinely necessary. Do not use personal,
confidential or unlicensed data. State the limitations of evaluating a detector against
data produced by a simulator with shared physics.
```
