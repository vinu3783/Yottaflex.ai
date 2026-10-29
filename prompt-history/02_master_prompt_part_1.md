# Prompt 2 (master prompt, part 1 of 4)

**Tool:** Claude Code (VS Code extension), Claude Opus 5.5, same session as prompt 1.
**Note:** This file preserves a section of the original single master prompt; the prompt is divided at section boundaries.

````text
# MASTER PROMPT: Build a Production-Style API Data Ingestion Pipeline + Complete Interview Preparation Documentation

## 1. Your Role

Act as a Senior Data Engineer, Python Backend Engineer, Software Architect, QA Engineer, and technical interview mentor.

Your task is to build a complete, working, well-tested Data Engineering project that satisfies the assessment specification below. You must also teach me how every important part works so that I can confidently explain the implementation, architecture, trade-offs, debugging decisions, and limitations to an interview panel.

Do not stop after generating a plan. Inspect the workspace, implement the project, run it, execute tests, fix failures, generate real output evidence, and prepare the documentation.

Work incrementally. Verify each milestone before proceeding. Never claim that something works unless you have actually tested it.

## 2. Business Objective

Build a configurable, restartable, observable API data ingestion pipeline that retrieves paginated API data despite rate limits, authentication requirements, transient failures, schema changes, duplicate records, and interrupted execution.

The system must support:

- Paginated API ingestion.
- Configurable authentication without committed credentials.
- Rate-limit handling and retry with exponential backoff and jitter.
- HTTP 429 and transient server-error handling.
- Request timeouts and bounded retries.
- Incremental checkpoints and restart recovery.
- Raw API response retention.
- Schema validation and deterministic transformations.
- Duplicate detection and idempotent target publishing.
- Explicit quarantine of invalid records.
- Run metadata, logs, metrics, and failure reporting.
- A local mock API for deterministic evaluation without external credentials.
- A reproducible starter dataset and a configurable large dataset.
- Automated tests, reconciliation queries, performance measurements, and a public GitHub-ready repository.

The finished project must be runnable locally from a clean environment.

## 3. Technology Stack

Use the following stack unless a documented technical constraint justifies a change.

### Core application

- Python 3.12 or another compatible, explicitly documented Python version.
- FastAPI for the local mock source API.
- httpx for the ingestion HTTP client.
- Pydantic for configuration and data/schema validation.
- PostgreSQL for durable pipeline state and analytical target tables.
- SQLAlchemy 2.x for database access and transaction management.
- Alembic for database schema migrations.
- pytest for automated testing.
- pytest-cov for test coverage reporting.
- Docker and Docker Compose for reproducible local execution.
- Python logging with structured, machine-readable log fields.
- Ruff for linting and formatting.
- Git and GitHub for source control and public submission.

Use SQLAlchemy Core or clearly separated SQL statements where they make data operations easier to understand. Use explicit SQL for important upsert, deduplication, and reconciliation operations when it improves clarity.

Use a small, modular monolith. Do not introduce Kafka, Spark, Kubernetes, Airflow, Redis, or a cloud service unless a genuine requirement emerges. The objective is a correct, maintainable assessment solution, not unnecessary infrastructure.

Pin or constrain dependencies appropriately and generate a reproducible dependency lock file if practical.

## 4. Required Architecture

Implement the following logical flow:

Source API → HTTP client → raw-response archive → record validation → transformation → quarantine or staging → idempotent target publishing → reconciliation and run reporting.

Maintain a separate control path for ingestion state:

Pipeline configuration → run manager → checkpoint store → retry policy → structured logs and metrics.

The mock source API must be independently runnable and configurable. It must simulate:

- Paginated responses.
- Configurable page size and dataset size.
- API authentication.
- Rate limiting and HTTP 429 responses.
- Transient HTTP 500 and 503 errors.
- Request timeouts or simulated interrupted requests where practical.
- Empty pages and empty datasets.
- Duplicate records.
- Malformed payloads.
- Missing fields and unexpected schema fields.
- Late-arriving or out-of-order records.
- Invalid reference keys.
- A deterministic mode for repeatable tests.

Do not make production ingestion correctness depend on undocumented behavior of the mock server.

## 5. Data Model and Business Rules

Design a realistic synthetic dataset for an API that publishes customer orders and order events.

Use two related entities:

### Orders

Suggested fields:

- order_id: stable business identifier.
- customer_id: reference to a customer.
- order_status: enumerated status.
- order_amount: non-negative decimal monetary amount.
- currency: ISO-style currency code.
- created_at: ISO-8601 UTC timestamp.
- updated_at: ISO-8601 UTC timestamp.
- source_updated_at: timestamp used for incremental extraction.
- source_record_id: source identifier when applicable.

### Order events

Suggested fields:

- event_id: stable, unique event identifier.
- order_id: foreign key to an order.
- event_type: event classification.
- event_timestamp: event occurrence time.
- ingested_at: pipeline ingestion timestamp.
- source_updated_at: source modification timestamp when supported.

Choose one entity or both based on what can be implemented and tested completely within the assessment time. Prefer both when they can be delivered without compromising correctness.

Document the grain, primary keys, foreign keys, nullability, uniqueness constraints, allowed values, timestamp semantics, money precision, and derived fields.

Clearly distinguish:

- Business keys from surrogate keys.
- Source timestamps from ingestion timestamps.
- Record creation from record updates.
- Event time from processing time.
- Duplicate business records from repeated delivery of the same event.
- Invalid records from valid but late-arriving records.

Use deterministic identifiers and snake_case naming.

Do not silently discard history when the source represents distinct events. For mutable order records, document whether the target stores the latest state only or preserves historical versions.


````
