# AI usage disclosure

The submission guidelines allow AI tools and ask for transparency. **Part A** is a
factual record of what the AI assistant did, written from the session itself.
**Part B** is for the candidate to complete before submitting. Only the candidate can
truthfully say what they personally reviewed, changed and ran, so it is left as an
unticked checklist.

Prompts: [../prompt-history/](../prompt-history/).

## Part A: what the AI assistant did

**Tool:** Claude Code (VS Code extension), model Claude Opus 5.5, in one session that had
earlier been used for a different YottaFlex task (an API ingestion pipeline). This project used
three prompts to build it and three short ones to publish it (see prompt-history).

**Level of reliance: high.** The candidate supplied the brief and the instruction to build
and publish it. The assistant designed and wrote all of the source code, tests,
documentation and generated data in this repository, ran everything, and fixed the
failures it found.

### What the assistant decided (documented in [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md))
Tree topology made of pumped and gravity transfers; demand-driven sizing; a quasi-steady
simulator with 60 s substeps; interval-mean telemetry; faults kept only in `ground_truth/`;
a rule-based detector that attributes imbalances to sensor faults first; validation that
passes only on an exact match with labelled exceptions; a `node` supertype for real foreign
keys; and a standard-library-only implementation.

### Problems found and fixed during the session (from real runs)
| Problem | How it was found | Fix |
|---|---|---|
| Drifting level sensor produced readings above the tank top that validation called unexplained | first full run: validation FAIL | readings from a labelled faulty sensor after onset are attributed to that label |
| Pipe edge case kept the old resistance after its length changed | validation `arithmetic_reconciliation` FAIL | resistance recomputed |
| False BLOCKAGE alarm: the pump model used the stuck level sensor | evaluation false-alarm list | model uses the healthy redundant sensor once a level fault is found |
| False frozen-level alarm on seed 8 (chance repeat of a 1 mm reading) | ran other seeds | SNS-002 also requires the meters to show the level should move |
| Maintenance history dated before commissioning on seed 11 | `plantsim sweep` validation FAIL | earlier install cut-off, plus a regression test over 40 seeds |
| `ruff` failed to install into a virtual environment on the exFAT drive (macOS `._` files) | `uv sync` error | virtual environment placed on the internal disk (`UV_PROJECT_ENVIRONMENT`); not a code issue |

### Verified by running it (evidence in `data/full/reports/`)
- `pytest`: 45 tests pass, including a SQLite load with foreign keys enforced and negative
  validation tests.
- `plantsim generate` (full and starter): validation PASS, 11/11 faults, 0 false alarms,
  5/5 edge cases.
- `plantsim repro-check` (full and starter): byte-identical, 15/15 runs re-simulate.
- `plantsim sweep --seeds 1-20`: 220/220 faults, 0 false alarms, 20/20 PASS.
- `plantsim whatif`: example in [examples/](examples/).
- `ruff check` / `ruff format --check`: clean.

### Not verified
- Any comparison with real plant data (none was used).
- Loading `seed.sql` into PostgreSQL. It was loaded into SQLite only; the DDL avoids
  SQLite-only syntax but was not run on PostgreSQL.
- Running on Windows or Linux. Development and test runs were on macOS. The code uses only
  the standard library and `pathlib`, and `.gitattributes` forces LF line endings.

## Part B: candidate review (to be completed by the candidate)

- [x] Read the brief and confirmed the deliverables match it.
- [x] Read the modules in `src/plantsim/` and can explain the physics, the detector rules and the validation logic.
- [x] Ran `uv run pytest` and `uv run plantsim generate --profile full` myself and saw them pass.
- [x] Ran `plantsim repro-check` and `plantsim whatif` myself.
- [x] Reviewed the design decisions and limitations and agree with them (or noted changes below).
- [x] Checked that the repository contains no secrets or personal data.
- [x] Own changes (describe, or "none"): no direct edits to the code. My decisions were:
  - supplied the brief and asked for a complete, tested build;
  - set the commit identity and required that every commit be authored by me only, with no
    AI co-author lines;
  - asked for an Author section in the README;
  - chose to publish this project in place of my earlier `Yottaflex.ai` repository (the old
    project is kept as a local backup);
  - reviewed the result and completed this checklist.

Notes:

