# Validation and evaluation

All numbers below come from files in `data/full/reports/`, produced by
`plantsim generate --profile full`, `plantsim repro-check --profile full` and
`plantsim sweep --seeds 1-20`.

## Validation (18 checks on the exported CSVs)

| Check | Category | Status | What it proves |
|---|---|---|---|
| primary_key_unique | integrity | PASS | no duplicate keys in 21 tables |
| id_and_format | integrity | PASS | IDs match FAC-001 / SIM-2026-000001 / ... ; dates and JSON parse |
| required_fields | integrity | EXPECTED (1) | only SIM-2026-000016 lacks config/hash/seed |
| allowed_values | integrity | PASS | enums and numeric ranges |
| exclusive_references | integrity | PASS | exactly one FK in polymorphic references |
| foreign_keys | integrity | EXPECTED (1) | only PIPE-108 -> TANK-031 is dangling |
| duplicates | integrity | PASS | one reading per sensor per timestamp; no duplicate pipes |
| facility_consistency | integrity | PASS | related records belong to the same facility |
| topology_dangling_pipes | topology | EXPECTED (1) | PIPE-108 only |
| topology_connected | topology | PASS | one connected graph per facility; every tank reachable from the inlet |
| topology_roles | topology | PASS | pumps have 1 in / 1 out; valve pipe counts match role |
| units_explicit | units | PASS | each reading's unit = its sensor's unit = its measurement type's unit |
| date_ordering | dates | PASS | install after commissioning; sensors after components; readings inside runs; contiguous mode segments |
| arithmetic_reconciliation | derived | PASS | capacity, pipe resistance, pump curve, motor rating, valve state, quality flag, config hash, telemetry count and run summaries all recomputed from source |
| run_reproducibility_inputs | reproducibility | EXPECTED (1) | SIM-2026-000016 only |
| lifecycle_transitions | lifecycle | PASS | allowed transitions; status = history; no pump starts while in maintenance |
| level_within_capacity | plausibility | EXPECTED (6) | exactly the 6 labelled readings |
| ground_truth_separation | leakage | PASS | no label columns in inputs; no fault terms in run configs; every label points at a real record |

*EXPECTED* means the violations are exactly the labelled records. With other seeds the
level check can also list readings from the drifting level sensor once its value rises above
the tank top. Those readings are explained by the labelled sensor fault, and the validator
attributes them to it.

Negative tests ([tests/test_validation_negative.py](../tests/test_validation_negative.py))
corrupt a copy of the data and require FAIL: a duplicated reading, a wrong unit, a tampered
capacity, an unlabelled dangling pipe, a labelled case removed, and a broken lifecycle.

The SQL seed is loaded into SQLite with `PRAGMA foreign_keys = ON` in the tests:
`foreign_key_check` returns nothing, all 100,000 readings load and join to sensor and
facility, and 4 rows are quarantined (PIPE-108 with its 2 history rows, and
SIM-2026-000016).

## Detection (official seed)

| Run | Fault | Component | Outcome | Delay |
|---|---|---|---|---:|
| SIM-2026-000002 | leak 20 % | PIPE-017 | detected, localised | 90 min |
| SIM-2026-000003 | blockage | PIPE-029 | detected, localised | 45 min |
| SIM-2026-000004 | pump degradation | PUMP-001 | detected | 315 min |
| SIM-2026-000005 | level stuck | SNS-002 | detected | 75 min |
| SIM-2026-000007 | leak 12 % | PIPE-069 | detected, localised | 105 min |
| SIM-2026-000008 | blockage | PIPE-047 | detected, localised | 60 min |
| SIM-2026-000009 | pump degradation | PUMP-012 | detected | 255 min |
| SIM-2026-000010 | flow meter +25 % | SNS-054 | detected (and the operator's LEAK alarm reclassified) | 315 min |
| SIM-2026-000012 | leak 3 % (below tolerance) | PIPE-095 | no alarm, as expected | - |
| SIM-2026-000013 | blockage | PIPE-077 | detected, localised | 315 min |
| SIM-2026-000014 | pump degradation | PUMP-016 | detected | 435 min |
| SIM-2026-000015 | level drift | SNS-076 | detected | 120 min |

Metrics: recall 11/11, alarm precision 11/11 (every process/sensor alarm raised was correct), 0 false alarms in the three fault-free runs,
6/6 data-quality readings flagged with no extras, and 2/2 operator alarm reviews correct.

Delays are long for pump degradation because health falls linearly, so the curve residual
crosses its 3 % tolerance only part-way through the ramp. Detection requires 3 consecutive
pump-on intervals, and a pump that cycles off delays it further.

## Robustness (20 other seeds)

`plantsim sweep --seeds 1-20` regenerates the whole dataset with each seed: new topology,
sizes, targets, onsets and noise. Result: **220/220 faults detected, 0 false alarms, 20/20
validation PASS, 5/5 edge cases on every seed**, with a mean delay of about 180 min.

Testing other seeds found two problems, both now fixed:
1. **Seed 8** (found during development, before the first commit): a *frozen-level* false
   alarm. A steady tank repeated the same 1 mm-resolution reading 6 times by chance. With
   4 mm noise this happens roughly once per full dataset. Rule SNS-002 now also requires the
   meters to say the level should have moved by at least 5 cm.
2. **Seed 11** (found by `plantsim sweep`, fixed in commit "Fix maintenance history dated
   before commissioning"): validation FAIL. Maintenance history was dated before a pump's
   commissioning. Install dates now end early enough, and a regression test checks 40 seeds.

## Reproducibility

`plantsim repro-check --profile full` generates the dataset twice into separate folders
and compares the manifests. All 51 files have identical row counts and SHA-256 checksums.
It then re-simulates each of the 15 runs from its stored `input_config_json` and seed,
plus its ground-truth fault list: 15/15 reproduce the stored `output_hash`. A different
seed changes the telemetry checksum. The tests repeat these checks on the starter profile.
