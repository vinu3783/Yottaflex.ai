# AI Usage Disclosure

The submission guidelines allow AI tools and ask for transparency about how
they were used. This document has two parts:

* **Part A** is a factual record of what the AI assistant did. It was written
  during the session, from the session itself.
* **Part B** is for the candidate to complete before submitting: what *you*
  personally reviewed, changed and validated. Only you can state this
  truthfully, so it is left as a checklist and is not pre-filled.

All prompts are in [../prompt-history/](../prompt-history/).

---

## Part A: What the AI assistant did

**Tool:** Claude Code (VS Code extension), model Claude Opus 5.5, one session, two prompts.

**Level of reliance: heavy.** After the specification prompt, the assistant
designed, implemented, tested and documented the project largely
autonomously: it wrote the source code, tests, SQL, scripts, Docker files and
all documentation, ran everything locally, and made the git commits under the
candidate's name as instructed.

### Where AI accelerated the work
* Scaffolding: project layout, `pyproject.toml`, Alembic setup, CLI.
* The data generator's edge-case matrix and the oracle files.
* Writing the test suite (130 tests) and fault-injection scenarios.
* Drafting all documentation, diagrams and interview material.

### Decisions the assistant made (and documented as ADRs)
Keyset cursor on `(source_updated_at, source_record_id)`; checkpoint in the
same transaction as each page; lookback window + `--since` backfill;
"strictly newer version wins" with conflicts quarantined; raw responses as
JSONB; per-record outcome ledger; synchronous single-runner design with an
advisory lock. See [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md).

### Problems found and fixed during the session (from real executions)
| Problem | How it was found | Fix |
|---|---|---|
| Upsert `rowcount` returned `-1` with SQLAlchemy insertmanyvalues + psycopg 3 | integration test `test_sql_guard_blocks_older_version_even_without_python_check` failed | count via `RETURNING` (commit `Fix rows_written counting …`) |
| `page_size=5000` would exceed PostgreSQL's 65,535 bind-parameter limit | noticed while writing ADR-16 | cap at 4,000 + new test |
| `SimulatedCrash` was caught by the pipeline's error handling, unlike a real crash | design review before running tests | made it a `BaseException` |
| Alembic tried to import macOS `._*.py` files (exFAT drive) | migration failed with "null bytes" | `run_migrations` removes stray AppleDouble files |
| PostgreSQL timestamps read back in local time (IST) | smoke-test output | sessions forced to UTC |
| 100k-order benchmark filled the system disk; PostgreSQL stopped | PostgreSQL log: *No space left on device* | freed space, capped WAL on the local dev cluster, reported the run as NOT MEASURED |

### What was verified by actually running it (evidence in `reports/`)
* Full test suite (unit + integration on PostgreSQL 16), twice in a row, plus coverage.
* `scripts/demo.sh` end to end over real HTTP, with injected faults, backfill and reconciliation.
* Benchmark on 116,218 records in two write modes.
* `ruff check` / `ruff format --check`.

### What was **not** verified
* Docker / Docker Compose (Docker not installed on the development machine).
* The 100,000-order benchmark (disk space).
* Behaviour against a real third-party API (only the mock).

---

## Part B: Candidate review (to be completed by you before submission)

Tick only what you actually did, and add notes in your own words. Interviewers
may ask about any of these.

- [ ] Read the two PDFs and confirmed the deliverables match the brief.
- [ ] Read every module in `src/ingestion_pipeline/` and can explain it.
- [ ] Ran `uv sync`, `ingest db-upgrade`, `uv run pytest` myself and saw them pass.
- [ ] Ran `scripts/demo.sh` myself and inspected `reports/demo/`.
- [ ] Reviewed the design decisions and agree with them (or changed: ________).
- [ ] Checked the repository contains no secrets or personal data.
- [ ] Ran the Docker Compose path (if Docker available): result ________.
- [ ] Made my own changes to: ________.
- [ ] Practised the walkthrough in [TECHNICAL_WALKTHROUGH.md](TECHNICAL_WALKTHROUGH.md).

Notes:

> _(write here)_
