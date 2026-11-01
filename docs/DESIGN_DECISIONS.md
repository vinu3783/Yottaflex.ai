# Design decisions

Each entry gives the decision, the reason and the trade-off.

**1. Physics first, labels second.** Telemetry comes from a hydraulic model (pump curves,
Darcy-Weisbach, volume balance), and labels come from the injected faults. A label is never
assigned at random. *Why:* the brief asks for outcomes derived from documented rules, and a
detector can only be evaluated meaningfully if the anomalies leave a physical signature.
*Trade-off:* the detector shares the model's physics, so the scores are optimistic (see the
README limitations).

**2. Tree topology made of transfers.** *Why:* every pipe connects two real nodes, every tank
is reachable from the inlet, and each pumped transfer can be solved on its own. That keeps
the hydraulics exact and explainable without a network solver. *Trade-off:* no loops or
parallel pumps.

**3. One schema module drives everything.** DDL, the CSV parser, the validator and the data
dictionary are all generated from `schema.py`. *Why:* the documentation cannot drift from
the code.

**4. A `node` supertype table.** *Why:* pipes and sensors attach to pumps, tanks or valves,
and a supertype gives them real foreign keys instead of "polymorphic" text columns. Event
tables use exactly one of `node_id`/`pipe_id`/`sensor_id`, enforced by a CHECK.

**5. Ground truth is physically separate.** Fault injections live only in `ground_truth/`;
`simulation_run.input_config_json` holds the operating plan (modes, initial levels, demand,
overrides). *Why:* the detector must not be able to read labels. A test asserts the detector
source never opens files, and validation checks that no input column or config contains
label words. *Trade-off:* re-simulating a faulty run needs the ground-truth file too. The
reproducibility check uses both.

**6. Detector input = exported CSVs.** During generation the detector runs on the files just
written, not on in-memory objects. *Why:* it proves the published files are enough on their
own, and a test re-runs the detector from the CSVs and gets identical alarms.

**7. Flow and pressure are interval means; level is an instantaneous value.** *Why:* with
snapshots, pump on/off switching inside an interval would break the mass balance. Historian
averages are realistic, and they make the balance close to within noise (0.04 % on normal
runs).

**8. Sensor faults explain imbalances before process faults do.** MB-001 finds that water is
missing, and the attribution comes next: if one of the tank's balance sensors has a fault
(redundant disagreement, frozen value, operating point above the pump curve), the imbalance
is assigned to that sensor. *Why:* this is exactly the "sensor fault labelled as process
fault" case. *Trade-off:* diagnosis is batch, using the whole run.

**9. Tolerance-aware labels.** The 3 % leak is labelled `NO_ALARM` because it is below the 6 %
mass-balance tolerance. *Why:* boundary coverage. A detector that alarms on it is
over-sensitive and is scored as a false alarm.

**10. "Expected exceptions" means exact match.** A validation check passes with exceptions
only if the records it flags are exactly the labelled ones. *Why:* otherwise injected edge
cases could hide real generator bugs. This worked: the 20-seed sweep caught a lifecycle bug
on seed 11 (maintenance dated before commissioning). It was fixed, and a regression test
was added.

**11. SQL load quarantines instead of failing.** `seed.sql` loads only rows that satisfy the
declared constraints. The rest, with their dependent rows, go to `rejected_record` with
a reason. *Why:* the bad pipe and the config-less run must stay visible in the CSV/JSON
inputs, while the database stays referentially intact (`PRAGMA foreign_key_check` is empty).

**12. Append-only history.** Pump run/stop, valve settings, component lifecycle and alarm
states are event tables. Master `status` is derived from the latest lifecycle event and
reconciled by validation. What-if runs get new SIM IDs and their own folder, and nothing
is overwritten.

**13. Deterministic everything.** Named `random.Random` streams per purpose (master, plan,
edge cases, one per run seeded from `sha256(seed:run_id)`), sorted iteration, `repr` floats,
and no wall-clock timestamps in outputs. *Why:* byte-identical regeneration, which the
`repro-check` command verifies.

**14. Standard library only.** *Why:* the FAQ asks to avoid heavy dependencies, and anyone
can run it with plain Python. pytest and ruff are development tools only.

**15. Telemetry volume exactly 100,000.** 15 runs x 100 sensors x 200 steps. Ten redundant
level sensors bring the sensor count to 100, which also gives the detector a way to tell
level-sensor faults from process faults.
