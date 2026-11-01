# plantsim: pumps, tanks, valves, sensors and flows

A synthetic plant model that generates a reproducible dataset of three facilities (20 pumps,
30 tanks, 60 valves, 108 pipes, 100 sensors) and **100,000 telemetry points**. It injects
leaks, blockages, pump degradation and sensor faults, detects them with documented rules,
and scores the detector against a separate ground truth. It can also simulate operating
changes ("what if we run high demand with PUMP-002 out of service?").

Everything is derived from physics and documented rules, not random labels. The
generator, simulator, detector, validator and evaluator are plain Python
(standard library only).

## Results at a glance (seed 20261022, full profile)

| What | Result | Evidence |
|---|---|---|
| Validation | **PASS**: 13 checks clean, 5 find exactly the labelled edge cases and nothing else | [validation_summary.md](data/full/reports/validation_summary.md) |
| Faults detected | **11 / 11**, all correctly localised; the below-tolerance leak correctly raises no alarm | [evaluation.md](data/full/reports/evaluation.md) |
| False alarms | **0** (0 in the three fault-free runs) | same |
| Required edge cases | **5 / 5** detected and handled | same |
| Mean detection delay | 194 min (pump degradation is a slow ramp; leaks 90-105 min) | same |
| Robustness, 20 other seeds | 220 / 220 faults detected, 0 false alarms, 20 / 20 validation PASS | [robustness_sweep.json](data/full/reports/robustness_sweep.json) |
| Reproducibility | Two generations with the same seed are byte-identical (51 files); every run re-simulates to its stored output hash (15 / 15) | [reproducibility.json](data/full/reports/reproducibility.json) |
| Facility mass balance, normal runs | measured residual 0.04 %, -0.01 %, -0.01 % of inflow | evaluation.md |

**Read this before trusting the numbers:** the detector is evaluated on data from a
simulator whose physics it shares. That makes the scores an upper bound. Real plants
add model error, unmodelled losses and messier sensors. See [Limitations](#limitations).

## Quick start

Requires Python 3.11+. There are no runtime dependencies.

```bash
# with uv (recommended)
uv sync
uv run plantsim generate --profile full        # ~5 s: data/full/
uv run plantsim generate --profile starter     # ~1 s: data/starter/ (10,000 telemetry points)
uv run pytest                                  # 45 tests, ~13 s

# or plain Python, no install
PYTHONPATH=src python -m plantsim generate --profile full
```

On Windows PowerShell: `$env:PYTHONPATH="src"; python -m plantsim generate --profile full`.

`generate` recreates its output folder from scratch. Run `plantsim repro-check --profile full`
and `plantsim sweep --seeds 1-20` afterwards to rebuild `reproducibility.json` and
`robustness_sweep.json` (about 15 s and 2 min). Regenerating with the default seed reproduces
the committed `data/` files byte for byte, so `git status` shows no changes.

| Command | What it does |
|---|---|
| `plantsim generate --profile full\|starter [--seed N] [--telemetry-points N]` | master data, simulation, edge cases, detection, CSV/JSON/SQL export, validation, evaluation, manifest |
| `plantsim validate --data data/full` | re-run all validation checks on the exported files |
| `plantsim evaluate --data data/full` | re-score detector alarms against ground truth |
| `plantsim repro-check --profile full` | generate twice and compare checksums; re-simulate every run from its stored config |
| `plantsim sweep --seeds 1-20` | robustness: regenerate with other seeds and report detection metrics |
| `plantsim whatif --base-run SIM-2026-000001 --mode HIGH_DEMAND --disable-pump PUMP-002` | simulate an operating change against a baseline run |
| `plantsim docs` | regenerate [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md) from the schema |

The same generator produces both profiles; only `--telemetry-points` changes. The total must
be a multiple of 500 (5 runs per facility x 100 sensors). Full = 200 steps per run
(50 h at 15 min), starter = 20 steps.

## What is generated

```
data/full/
  csv/              21 tables (inputs + derived alarms), one file per entity
  json/             the same tables as JSON arrays
  sql/schema.sql    DDL with PK, FK, CHECK and UNIQUE constraints (PostgreSQL / SQLite)
  sql/seed.sql      INSERTs; rows breaking a declared constraint go to rejected_record
  ground_truth/     ground_truth.csv/.json: labels, fault injections, true outcomes (kept apart)
  reports/          validation_summary, evaluation, reproducibility, robustness_sweep, sql_load_report
  manifest.json     seed, command, provenance, row counts and SHA-256 of every file
```

| Table | Rows | | Table | Rows |
|---|---:|---|---|---:|
| facility | 3 | | operating_mode | 12 |
| pump / tank / valve | 20 / 30 / 60 | | operating_mode_valve_setting (N:M) | 240 |
| node (supertype) | 110 | | operating_mode_pump_setting (N:M) | 80 |
| pipe | 108 (incl. 1 labelled bad) | | simulation_run | 16 (incl. 1 labelled bad) |
| sensor | 100 | | simulation_run_assumption (N:M) | 210 |
| telemetry | **100,000** | | simulation_run_mode_segment | 32 |
| pump_state_event | 1,174 | | component_status_event | 654 |
| valve_state_event | 316 | | alarm / alarm_event / alarm_review | 19 / 27 / 2 |

Primary keys, foreign keys, cardinality, units, allowed values and the derivation rule of
every field are in the [data dictionary](docs/DATA_DICTIONARY.md). ID formats:
`FAC-001`, `PUMP-001`, `TANK-001`, `VALVE-001`, `PIPE-001`, `SNS-001`, `SIM-2026-000001`,
`TEL-0000001`, zero-padded, assigned once and never reused.

## How it works

```
master data -> topology graph -> scenario plan -> simulation -> labelled edge cases
  -> derived values -> export CSV -> detector (reads the CSVs only) -> alarms
  -> JSON / SQL -> validation -> evaluation -> manifest
```

1. **Master data first.** Facilities, then a tree of tanks per facility. Each tree edge is a
   *transfer*: either `tank -> suction valve -> pump -> discharge valve -> tank` or
   `tank -> gravity control valve -> lower tank`. An inlet valve feeds the intake tank
   and outlet valves deliver from the leaf tanks. Pumps, pipes, valves and tanks are
   **sized from downstream demand**: pump curves cover the worst static head,
   pipe resistance follows Darcy-Weisbach, and valves are sized by Kv.
2. **Simulation.** 15-minute telemetry intervals are integrated in 60 s substeps. Each
   pump runs where its curve meets the system curve, gravity flow follows the head
   difference, and tanks integrate volume. Level control and a diurnal demand profile
   apply on top, and each run follows a schedule of operating modes. See
   [docs/SIMULATION_MODEL.md](docs/SIMULATION_MODEL.md).
3. **Faults** come from a separate list stored only in `ground_truth/`, so the published
   run configuration never reveals them. Severities follow documented rules: a leak
   below the 6 % mass-balance tolerance is labelled `NO_ALARM`, not `ALARM`.
4. **Detector** ([src/plantsim/detector.py](src/plantsim/detector.py)) uses the
   physics, not the labels:

   | Rule | Signal | Diagnosis |
   |---|---|---|
   | MB-001 | tank volume balance (meters vs level) does not close | `LEAK_SUSPECTED`, located between the meter and the tank |
   | PMP-001 | pump runs below its own curve | `PUMP_DEGRADATION` |
   | SNS-003 | pump appears to run above its curve (impossible) | `FLOW_SENSOR_FAULT` |
   | BLK-001 | pump on its curve but flow well below the system model | `BLOCKAGE_SUSPECTED` |
   | SNS-001 / SNS-002 | redundant level sensors disagree, or a level is frozen while the meters say it should move | `LEVEL_SENSOR_FAULT` |
   | DQ-001 | level reading above tank height | `DQ_LEVEL_EXCEEDS_CAPACITY` |

   An imbalance is **not** called a leak if one of that tank's balance sensors is faulty.
   This is how the operator's mislabelled "leak" gets reclassified as a meter fault.

## Required edge cases

| Edge case (brief) | Injected as | How it is caught |
|---|---|---|
| Pipe references missing node | `PIPE-108` -> `TANK-031` (does not exist) | `foreign_keys` and `topology_dangling_pipes` flag it; SQL load moves it (and its 2 history rows) to `rejected_record`; the topology builder ignores it |
| Tank level exceeds capacity | 6 readings above tank height | DQ-001 raises 6/6; `level_within_capacity` lists exactly these |
| Flow conservation violated beyond tolerance | 20 % and 12 % leaks on discharge pipes; 3 % leak as the within-tolerance boundary case | MB-001 alarms on both violations and correctly stays silent on the 3 % case |
| Simulation output lacks input configuration | `SIM-2026-000016` has outputs but no config, hash or seed | `required_fields` and `run_reproducibility_inputs` flag it; the detector skips it; SQL rejects it |
| Sensor fault labelled as process fault | operator alarm `ALM-000002` says LEAK; the cause is a +25 % flow-meter bias | detector finds the meter fault (SNS-003); `alarm_review` decides `RECLASSIFY_AS_SENSOR_FAULT` |

Validation reports a check as `EXPECTED_EXCEPTIONS` only when its violations are **exactly**
the labelled records. An unlabelled problem, or a labelled one that is no longer found,
makes it `FAIL` (see [tests/test_validation_negative.py](tests/test_validation_negative.py)).

## Simulating operating changes

```bash
uv run plantsim whatif --data data/full --base-run SIM-2026-000001 --mode HIGH_DEMAND --disable-pump PUMP-002
```

Both the baseline and the change are re-simulated fault-free with the same seed, so the
difference comes only from the change. Result for this command
([docs/examples](docs/examples/whatif_high_demand_without_pump002.json)): unmet demand rises from
303 m3 to 1,123 m3, pump energy falls from 580 to 426 kWh, and pump starts drop from 36 to
20. The run is written to `data/full/whatif/SIM-2026-000017/` with its own config hash.
Existing runs are never overwritten. Other options: `--pump-speed PUMP-001=85`,
`--valve-opening VALVE-005=50`, `--demand-multiplier 1.2`.

## Repository map

| Path | Purpose |
|---|---|
| [src/plantsim/schema.py](src/plantsim/schema.py) | every table, field, unit, rule (DDL, parser, validator and docs are generated from it) |
| [src/plantsim/masterdata.py](src/plantsim/masterdata.py), [topology.py](src/plantsim/topology.py), [physics.py](src/plantsim/physics.py) | components, graph, hydraulics |
| [src/plantsim/scenarios.py](src/plantsim/scenarios.py), [simulator.py](src/plantsim/simulator.py) | run plans, fault injection, time-stepped simulation |
| [src/plantsim/generate.py](src/plantsim/generate.py) | the pipeline, edge cases, ground truth, exports |
| [src/plantsim/detector.py](src/plantsim/detector.py) | detection rules |
| [src/plantsim/validation.py](src/plantsim/validation.py), [evaluation.py](src/plantsim/evaluation.py) | checks and metrics |
| [src/plantsim/repro.py](src/plantsim/repro.py), [sweep.py](src/plantsim/sweep.py), [whatif.py](src/plantsim/whatif.py) | reproducibility, robustness, operating changes |
| [docs/](docs/) | data dictionary, simulation model, design decisions, validation and evaluation, requirements trace, AI usage |
| [prompt-history/](prompt-history/) | every prompt given to the AI assistant for this project |

## Limitations

- **Model-matched evaluation.** The detector's expected-flow and pump-curve checks use
  the same equations as the simulator. Perfect scores here do not predict field
  performance. A fair next step is to evaluate with deliberately mismatched detector
  parameters (wrong friction factors, pump curves that have aged).
- **Quasi-steady hydraulics.** There are no transients, no water hammer and no pressure
  network solver. Each transfer is solved independently, which fits the tree topology
  but would not fit looped networks.
- **One fault per run.** Simultaneous faults, and a leak on a tank with a faulty sensor,
  are not tested. The batch diagnosis also uses all of a run's evidence, so
  classification is not causal in real time; the `raised_at` time is still the first
  time each rule held.
- **Control uses true levels**, so a faulty level sensor does not mislead the
  controllers.
- Leaks are placed downstream of the transfer flow meter. A leak upstream of the meter,
  or a leak from a tank wall, would show up as a source-tank imbalance, which the
  current rules report as `MASS_BALANCE_UNEXPLAINED` rather than localising it.
- The facility count, component counts and fault mix are fixed in `config.py`. Only the
  seed and telemetry volume are command-line parameters.

## Provenance and licence

All data is synthetic, produced by this generator. No external datasets, personal data
or proprietary sources were used. The physics comes from textbook relations
(Darcy-Weisbach, pump affinity laws, the Kv valve coefficient). The details are in
`manifest.json`. Code and data are released under the MIT licence.

AI assistance is disclosed in [docs/AI_USAGE.md](docs/AI_USAGE.md), and the prompts are in
[prompt-history/](prompt-history/).
