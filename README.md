# Orders API Ingestion Pipeline

A configurable, restartable and observable pipeline that pulls paginated
**customer orders** and **order events** from a REST API into PostgreSQL, and
keeps working through rate limits, authentication problems, transient
failures, schema changes, duplicate records and crashes.

It ships with a **local mock source API** (FastAPI) and a **deterministic data
generator**, so everything, including failure scenarios, can be evaluated
offline with no external credentials.

> YottaFlex AI candidate project. AI-assistance disclosure and full prompt
> history: [`prompt-history/`](prompt-history/) and [`docs/AI_USAGE.md`](docs/AI_USAGE.md).

---

## Contents

1. [Business problem](#1-business-problem)
2. [What it does](#2-what-it-does)
3. [Architecture](#3-architecture)
4. [Technology choices](#4-technology-choices)
5. [Quick start (local Python)](#5-quick-start-local-python)
6. [Quick start (Docker Compose)](#6-quick-start-docker-compose)
7. [Command reference](#7-command-reference)
8. [Testing](#8-testing)
9. [Verifying the results](#9-verifying-the-results)
10. [Performance benchmark](#10-performance-benchmark)
11. [Troubleshooting](#11-troubleshooting)
12. [Security notes](#12-security-notes)
13. [Limitations and future work](#13-limitations-and-future-work)
14. [Repository layout and documentation map](#14-repository-layout-and-documentation-map)

---

## 1. Business problem

An analytics team needs an accurate, queryable copy of the orders and order
events that a source system exposes only through a paginated HTTP API. The
API is not friendly: it rate-limits, fails intermittently, needs a token,
sometimes returns broken or unexpected data, re-delivers the same records, and
publishes some changes late. The copy must be:

* **complete**: nothing silently lost, even across crashes and late data;
* **correct**: no duplicates, no older data overwriting newer data;
* **explainable**: every number can be reconciled and every rejected record
  traced back to the exact HTTP response it came from.

## 2. What it does

| Requirement | How it is met | Where |
|---|---|---|
| Paginated ingestion | Keyset cursor `(source_updated_at, source_record_id)`, safe with timestamp ties | `pipeline.py`, `mock_api/app.py` |
| Authentication | Bearer token from env (`SecretStr`), never logged or stored | `config.py`, `api_client.py` |
| Rate limits | Client-side throttle + HTTP 429 `Retry-After` honoured (capped) | `rate_limiter.py`, `retry_policy.py` |
| Transient failures | Bounded retries, exponential backoff with full jitter, timeouts | `api_client.py`, `retry_policy.py` |
| Restartability | Checkpoint committed **in the same transaction** as each page's data | `pipeline.py`, `checkpoint.py` |
| Late-arriving data | Configurable lookback window + explicit `--since` backfill | `checkpoint.py`, `cli.py` |
| Raw retention | Every HTTP attempt (incl. 429/5xx/timeouts) stored in `raw_responses` (JSONB) | `raw_archive.py` |
| Validation + transformation | Pydantic models with stable error codes; UTC/Decimal/enum normalisation | `schemas.py`, `transformations.py`, `validation.py` |
| Quarantine | Original payload + error codes + messages + link to raw response | `quarantine.py` |
| Idempotency | Business-key upserts, "newer version wins" rule enforced in Python **and** SQL | `repository.py` |
| Schema drift | Additive fields accepted and counted; required field vanishing = run fails | `validation.py` |
| Observability | Run table, per-entity counters, JSON logs with `run_id`, per-record ledger | `metrics.py`, `logging_config.py` |
| Reconciliation | 14 automated checks incl. comparison with the generator's expected results | `reporting.py`, `sql/` |
| Mock API | Pagination, auth, rate limits, 429/5xx/timeouts/malformed/schema faults, late data | `mock_api/` |
| Tests | 130 tests (unit + integration on real PostgreSQL), 90% coverage | `tests/` |

**Delivery guarantee (stated precisely):** extraction is *at-least-once* (a
page may be fetched again after a failure or by a lookback), and publishing is
*idempotent*. The combination gives *effectively-once* results in the target
tables. It is not exactly-once delivery across systems.

## 3. Architecture

```mermaid
flowchart LR
    subgraph Source
      API[Mock Orders API<br/>FastAPI]
    end
    subgraph Pipeline["Pipeline process (Python)"]
      CLI[cli.py] --> RUN[pipeline.py<br/>orchestrator]
      RUN --> CLIENT[api_client.py<br/>auth, throttle, retries]
      CLIENT -->|every attempt| ARCH[raw_archive.py]
      RUN --> VAL[validation.py<br/>+ transformations]
      VAL -->|valid| REPO[repository.py<br/>idempotent upsert]
      VAL -->|invalid| QUAR[quarantine.py]
      REPO -->|conflict / unknown FK| QUAR
      RUN --> CKPT[checkpoint.py]
      RUN --> MET[metrics.py<br/>run tracking]
    end
    subgraph PostgreSQL
      RAW[(raw_responses)]
      TGT[(orders<br/>order_events)]
      Q[(quarantine)]
      LED[(record_outcomes)]
      CP[(checkpoints)]
      RUNS[(pipeline_runs<br/>run_entity_stats)]
    end
    API <-->|HTTP + Bearer token| CLIENT
    ARCH --> RAW
    REPO --> TGT
    QUAR --> Q
    REPO --> LED
    CKPT --> CP
    MET --> RUNS
    REP[reporting.py<br/>reconcile] --> PostgreSQL
```

One page is the unit of progress. For each page:

```
fetch (retries) -> archive raw (own txn) -> schema-break check ->
BEGIN: validate -> upsert -> quarantine -> ledger -> checkpoint -> counters :COMMIT
```

Details, sequence diagram and failure propagation: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## 4. Technology choices

| Tool | Why |
|---|---|
| **Python 3.12** | Required stack; mature data/HTTP ecosystem; type hints throughout |
| **FastAPI** (mock API) | Small, declarative, auto OpenAPI docs at `/docs`; testable in-process |
| **httpx** | Explicit timeouts, pluggable transports (`MockTransport` and FastAPI `TestClient` in tests) |
| **Pydantic v2 / pydantic-settings** | Typed config with secret masking; record validation with custom error codes |
| **PostgreSQL 16** | Transactions, `ON CONFLICT` upserts, JSONB raw archive, constraints, advisory locks |
| **SQLAlchemy 2 (Core)** | Explicit SQL-shaped code, parameter binding, connection pooling; no ORM magic |
| **Alembic** | Versioned, reviewable schema migrations |
| **pytest + pytest-cov** | Unit + integration tests against a real database, coverage report |
| **Docker Compose** | One-command reproducible stack (Postgres + mock API + pipeline) |
| **Ruff** | Lint + format in one fast tool |
| **uv** | Fast installs and a reproducible lock file (`uv.lock`) |

Deliberately **not** used: Kafka, Spark, Airflow, Redis, Kubernetes or cloud
services. The data volume and the requirements don't need them, and each would
add moving parts a reviewer has to run. See ADR-17 in
[docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md).

## 5. Quick start (local Python)

### Prerequisites

* Python **3.12** (3.13 also works) and [uv](https://docs.astral.sh/uv/) (`pip install uv` or `brew install uv`)
* PostgreSQL **14+** running locally (16 used in development)
* `psql` is optional (only for the SQL sample files)

### 1. Install

```bash
git clone https://github.com/vinu3783/Yottaflex.ai.git
cd Yottaflex.ai
uv sync                      # creates .venv and installs exact versions from uv.lock
source .venv/bin/activate    # makes the `ingest` command available
```

### 2. Configure

```bash
cp .env.example .env
# edit .env: set PIPELINE_DATABASE_URL, and the same token for
# PIPELINE_API_TOKEN and MOCK_API_TOKEN (any string for the local mock)
```

### 3. Create the databases and apply migrations

```bash
createuser pipeline --pwprompt          # or use an existing role
createdb -O pipeline pipeline
createdb -O pipeline pipeline_test      # used by the integration tests
ingest db-upgrade                       # Alembic migrations -> head
```

### 4. Generate data and start the mock API

```bash
ingest generate-data --out data/generated/review --orders 200 --seed 42
ingest mock-api --dataset-dir data/generated/review --port 8000      # leave running
```

Interactive API docs: <http://127.0.0.1:8000/docs>. Health: `GET /health`.

### 5. Run the pipeline (second terminal)

```bash
ingest run                       # incremental run (first run = full load)
ingest runs                      # list runs
ingest show-run latest           # counters, HTTP attempts, quarantine by code
ingest reconcile --dataset data/generated/review
```

### 6. Full demonstration in one command

```bash
# WARNING: wipes the database in PIPELINE_DATABASE_URL
export PIPELINE_DATABASE_URL=postgresql+psycopg://pipeline:<password>@localhost:5432/pipeline
scripts/demo.sh
```

It runs an initial load, releases late data, injects 503/429 faults, shows the
gap the lookback cannot cover, backfills it, reruns to prove idempotency and
writes all evidence to `reports/demo/`. The committed copy of that evidence is
described in [reports/README.md](reports/README.md).

## 6. Quick start (Docker Compose)

> Not executed on the development machine (Docker was not available there):
> the files are written to the same commands that were verified locally.

```bash
cp .env.example .env                          # optional; defaults work
docker compose up -d db mock-api              # Postgres + mock API (200 orders, seed 42)
docker compose run --rm pipeline ingest db-upgrade
docker compose run --rm pipeline ingest run
docker compose run --rm pipeline ingest show-run latest
docker compose run --rm pipeline pytest       # full test suite inside the container
docker compose run --rm pipeline ingest mock-admin release-phase 2
docker compose run --rm pipeline ingest run   # incremental run picks up phase 2
docker compose down -v                        # remove containers and the volume
```

## 7. Command reference

| Command | Purpose |
|---|---|
| `ingest db-upgrade` | Apply migrations |
| `ingest db-reset --yes` | **Dev only**: drop and re-create the schema |
| `ingest generate-data --out DIR --orders N --seed S [--no-edge-cases] [--no-phases]` | Deterministic dataset + oracle |
| `ingest mock-api [--dataset-dir DIR] [--phase N] [--rate-limit RPS] [--port P]` | Start the mock source API |
| `ingest mock-admin release-phase 2` | Publish late / phase-2 rows on the running mock |
| `ingest mock-admin set-faults '<json>'` / `clear-faults` / `stats` | Inject failures, inspect requests |
| `ingest run [--entity orders] [--since TS] [--until TS] [--max-pages N] [--write-mode batch\|row]` | Run the pipeline |
| `ingest runs` / `ingest show-run [RUN_ID\|latest]` | Inspect runs |
| `ingest checkpoints show` / `ingest checkpoints reset [--entity E] --yes` | Inspect / reset checkpoints |
| `ingest reconcile [--dataset DIR] [--out reports/x]` | Reconciliation checks; exit code 1 on failure |
| `ingest benchmark --orders N --database-url URL [--modes batch row]` | Throughput benchmark (wipes its DB) |

Fault rule example (fields: `entity`, `page`, `kind`, `status_code`, `retry_after`, `delay_seconds`, `field`, `times`):

```bash
ingest mock-admin set-faults '[{"entity":"orders","page":3,"kind":"status","status_code":503,"times":2},
                               {"entity":"order_events","kind":"status","status_code":429,"retry_after":2}]'
```

Kinds: `status`, `malformed`, `invalid_envelope`, `delay` (timeouts), `drop_field` (breaking schema change), `stuck_cursor`.

All configuration is in [.env.example](.env.example) (`PIPELINE_*` for the
pipeline, `MOCK_*` for the mock API).

## 8. Testing

```bash
uv run pytest                     # 130 tests: unit + integration (needs PostgreSQL)
uv run pytest tests/unit          # pure logic only, no database needed (97 tests)
uv run pytest --cov               # with coverage report
uv run ruff check . && uv run ruff format --check .
```

* Integration tests use `TEST_DATABASE_URL`
  (default `postgresql+psycopg://pipeline:pipeline@localhost:5432/pipeline_test`).
  The schema is rebuilt from the migration at session start and every table is
  truncated before each test, so the suite is repeatable. If the database is
  unreachable, integration tests are **skipped** with a clear message.
* No test touches the internet or real credentials: the mock API runs
  in-process (or on a random local port for the real-timeout test).
* Latest run (fresh clone, `uv sync --frozen`): **130 passed, 90% line+branch coverage** ([reports/test_results.txt](reports/test_results.txt)).
  The percentage shows which lines ran; it does **not** prove they are
  correct. Correctness comes from the assertions (e.g. comparing the target
  tables with the generator's oracle after a crash-and-restart).

## 9. Verifying the results

`ingest reconcile --dataset data/generated/review` runs these checks (all PASS in
[reports/demo/06_reconciliation.md](reports/demo/06_reconciliation.md)):

1. Per run and entity: `fetched = inserted + updated + duplicate + stale + quarantined = ledger rows`.
2. Target rows = number of `inserted` outcomes (each business key inserted once).
3. Raw `ok` records = ledger rows + records archived but never processed.
4. No order event without its order; no negative amount / `updated_at < created_at` in the target.
5. *(with `--dataset`)* Target keys and versions equal the generator's expected final state.
6. *(with `--dataset`)* Quarantine contains exactly the labelled invalid rows, with the expected codes.
7. *(with `--dataset`)* The first-delivery outcome of every labelled row matches its scenario.

Expected numbers for the default review dataset (seed 42, 200 orders, both phases + backfill):
`orders` **214** rows, `order_events` **587** rows, **34** distinct quarantined
order rows and **8** quarantined event rows. Why raw counts differ from target
counts (1,315 order deliveries vs 214 rows) is explained in
[docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md#count-semantics).

Sample queries: [sql/sample_queries.sql](sql/sample_queries.sql) ·
reconciliation as SQL: [sql/reconciliation.sql](sql/reconciliation.sql).

## 10. Performance benchmark

```bash
createdb -O pipeline pipeline_bench
ingest benchmark --orders 20000 --database-url postgresql+psycopg://pipeline:<pw>@localhost:5432/pipeline_bench
```

Measured on an Apple Silicon laptop (8 cores, PostgreSQL 16, everything local),
116,218 source records:

| Write mode | Duration | Throughput | Peak RSS | Reconciliation |
|---|---|---|---|---|
| batch (one upsert per page) | 25.2 s | **4,608.7 records/s** | 112 MB | PASS |
| row (one statement per record) | 79.1 s | 1,468.5 records/s | 70 MB | PASS |

Method, environment and limits: [docs/PERFORMANCE_REPORT.md](docs/PERFORMANCE_REPORT.md).

## 11. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `PIPELINE_API_TOKEN is not set` | Copy `.env.example` to `.env` and set the token |
| Run fails with `authentication` | Token differs between `PIPELINE_API_TOKEN` and `MOCK_API_TOKEN` |
| Integration tests all *skipped* | PostgreSQL not reachable at `TEST_DATABASE_URL`; create `pipeline_test` |
| `another run ... holds the pipeline lock` | A run is in progress (one run at a time by design) |
| Run `failed` with `retries_exhausted` | Source still failing after `PIPELINE_MAX_ATTEMPTS`; rerun later, it resumes from the checkpoint |
| Run `failed` with `breaking_schema_change` | A required field disappeared from the API; see [docs/FAILURE_RECOVERY.md](docs/FAILURE_RECOVERY.md) |
| `address already in use` on 8000 | Another mock running; use `--port` and `PIPELINE_API_BASE_URL` |
| Alembic `source code string cannot contain null bytes` | macOS `._*` files on an exFAT/FAT drive; `ingest db-upgrade` removes them automatically, or run `dot_clean .` |
| PostgreSQL crashes with *No space left on device* | Large benchmarks need ~1-2 GB free for data + WAL; use a smaller `--orders` |

More: [docs/OPERATIONS_RUNBOOK.md](docs/OPERATIONS_RUNBOOK.md).

## 12. Security notes

* Credentials only come from environment variables / an untracked `.env`. `.env.example` has placeholders.
* The token is a `SecretStr` (masked in reprs). The `Authorization` header is
  never logged and never written to `raw_responses` (tests assert this).
* Log messages pass through a redaction filter (tokens, `password=`, DB URL passwords).
* Stored endpoints are sanitised (user-info and query strings removed from the base URL).
* All SQL uses bound parameters; table names in report queries are internal constants.
* The mock's `/_admin/*` endpoints are for local testing only (`MOCK_ENABLE_ADMIN=false` disables them).
* Synthetic data only. No personal data is generated or committed.

## 13. Limitations and future work

* **Single runner, sequential pages.** A keyset cursor is inherently sequential; parallelism would come from partitioning (e.g. per entity or time range).
* **Raw archive in PostgreSQL.** Fine at this scale; for large volumes move bodies to object storage (S3/GCS, Parquet/gzip JSON) and keep only metadata + URI in Postgres.
* **Orders keep latest state only.** Version history is reconstructable from `raw_responses` and the ledger but not modelled as an SCD-2 table.
* **Lookback is time-based.** Rows arriving later than the window need an operator backfill (`--since`). The reconciliation report detects the gap only when an oracle or source count is available.
* **No retention policy job** for `raw_responses` / `record_outcomes` yet.
* **Docker files not executed** on the development machine (Docker unavailable); verified commands are the local ones.
* **100k-order benchmark not measured** (the development machine's system disk was nearly full); the measured workload is 116,218 records.

Full list and next steps: [docs/ACCEPTANCE_REPORT.md](docs/ACCEPTANCE_REPORT.md#remaining-limitations-and-next-steps).

## 14. Repository layout and documentation map

```
src/ingestion_pipeline/   application code (one module per responsibility)
  mock_api/               FastAPI mock source + fault rules
migrations/               Alembic migration(s)
tests/unit, tests/integration
scripts/demo.sh           end-to-end demonstration
sql/                      sample + reconciliation queries
reports/                  evidence from real executions
docs/                     design and interview documentation
prompt-history/           every prompt given to the AI assistant
```

| Document | For |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, data/control flow, transactions, failure propagation |
| [DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md) | Every table and column, grain, keys, count semantics |
| [DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md) | 17 ADRs with trade-offs and interview answers |
| [FAILURE_RECOVERY.md](docs/FAILURE_RECOVERY.md) | What happens in each failure, automatic vs manual recovery |
| [OPERATIONS_RUNBOOK.md](docs/OPERATIONS_RUNBOOK.md) | Exact commands and SQL for operators |
| [PERFORMANCE_REPORT.md](docs/PERFORMANCE_REPORT.md) | Benchmark method and measured results |
| [INTERVIEW_PREPARATION.md](docs/INTERVIEW_PREPARATION.md) | Concept handbook tied to this code |
| [TECHNICAL_WALKTHROUGH.md](docs/TECHNICAL_WALKTHROUGH.md) | 10-15 minute presentation script |
| [QUESTIONS_AND_ANSWERS.md](docs/QUESTIONS_AND_ANSWERS.md) | 60 project-specific interview questions |
| [GLOSSARY.md](docs/GLOSSARY.md) | Plain-English definitions |
| [ACCEPTANCE_REPORT.md](docs/ACCEPTANCE_REPORT.md) | Requirement-by-requirement PASS/FAIL/NOT VERIFIED |
| [AI_USAGE.md](docs/AI_USAGE.md) | How AI was used and what was verified |
