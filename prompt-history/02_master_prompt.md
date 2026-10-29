# Prompt 2

**Tool:** Claude Code (VS Code extension), Claude Opus 5.5, same session as prompt 1.
The message consisted of a pasted specification followed by three short lines.

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

## 6. Incremental Extraction and Checkpoint Design

Implement a durable checkpoint system that supports safe restarts.

Explicitly decide and document:

- Whether the source supports a stable cursor, page token, update timestamp, or another incremental mechanism.
- What the checkpoint represents.
- When a checkpoint is committed.
- How the system handles a crash after receiving data but before target publication.
- How it handles a crash after target publication but before checkpoint advancement.
- How it prevents data loss when several records share the same update timestamp.
- How late-arriving records are recovered.
- How overlapping extraction windows and idempotent upserts interact.
- How operators can perform a backfill.

Prefer a source-supported stable cursor when available.

If using a timestamp watermark, do not rely on timestamp alone when ties can occur. Implement a composite cursor or documented overlap strategy, such as a timestamp plus unique identifier, where source ordering supports it.

Advance checkpoints only after the corresponding data is safely persisted and processed. Use transactions and idempotent writes so a restart can safely replay data.

Do not claim exactly-once delivery across independent systems unless the implementation actually provides it. Explain at-least-once extraction combined with idempotent target publishing when that is the real guarantee.

Demonstrate recovery with a test that deliberately interrupts a run and then resumes it.

## 7. Raw Response Retention

Retain the original HTTP response payload before business transformations.

Store enough metadata to trace every raw response:

- run_id.
- request URL or sanitized endpoint.
- page token or cursor, where applicable.
- response status code.
- retrieval timestamp.
- response body or content hash.
- request attempt number.
- correlation or request identifier when available.

Use PostgreSQL JSONB for retained response bodies in the initial implementation, or a clearly justified local file archive if that is more appropriate.

If storing both an archive file and a database reference, define which is authoritative and how the two are reconciled.

Never store API secrets, authorization headers, passwords, or sensitive information in raw logs. Avoid logging complete sensitive request or response bodies.

Document retention, storage trade-offs, and future object-storage options.

## 8. Validation, Transformation, and Quarantine

Build a validation layer independent of HTTP retrieval.

Validate:

- Required fields.
- Field types.
- Primary-key presence.
- Referential integrity.
- Allowed status and event values.
- Monetary amount constraints.
- Timestamp formats and ordering.
- Duplicate identifiers.
- Unexpected schema changes.
- Nullability rules.

Use explicit validation policies.

Distinguish additive unknown fields from breaking schema changes. Document which fields are tolerated, which are recorded as warnings, and which cause a batch to fail.

Implement deterministic transformations, such as timestamp normalization, decimal normalization, whitespace normalization where appropriate, and consistent status handling.

Invalid records must be quarantined with:

- run_id.
- source entity.
- stable source identifier if available.
- original record payload.
- validation error codes.
- human-readable failure reasons.
- rejection timestamp.

A malformed entire HTTP response must be handled separately from an individual invalid record.

Do not silently convert invalid monetary values to zero, invent missing identifiers, or discard invalid rows without recording why.

## 9. Idempotency and Target Publishing

Repeatedly processing the same source input must not create unintended duplicates.

Implement deterministic business keys, database uniqueness constraints, and PostgreSQL upserts.

Document how conflicting duplicate records are handled. For example, for mutable orders, use a documented source-version or update-timestamp rule to prevent an older replay from overwriting newer state.

Preserve distinct order events even when they arrive out of order.

Use appropriate transactions so partial target publishing does not leave inconsistent state.

Define and test behavior for:

- Replaying the same page.
- Replaying the same full dataset.
- Reprocessing a failed run.
- Receiving duplicate identifiers with identical payloads.
- Receiving duplicate identifiers with conflicting payloads.
- Replaying an older version after a newer version.

Use stable event IDs and a clear conflict policy. Do not resolve conflicting records by arbitrary row order.

## 10. Rate Limits and Failure Recovery

Implement configurable settings for:

- Requests per second or minimum request interval.
- Maximum concurrent requests, if concurrency is supported.
- Request timeout.
- Maximum attempts.
- Base and maximum retry delays.
- Exponential backoff.
- Random jitter.
- Retryable status codes.
- HTTP 429 Retry-After handling.
- Authentication settings.
- Maximum pages or records per run.

Avoid retry storms and unbounded loops.

Use bounded retries and fail clearly when the source remains unavailable. Make the retry policy injectable or testable so tests do not sleep for long periods.

Do not retry every error indiscriminately. Distinguish permanent client errors, transient server errors, rate limiting, invalid authentication, malformed payloads, and programming errors.

Add tests for rate limiting, transient failure followed by success, exhausted retries, invalid credentials, and timeout handling.

## 11. Run Tracking and Observability

Create durable pipeline-run metadata.

At minimum, record:

- run_id.
- pipeline name and version.
- start and end timestamps.
- status.
- source and target identifiers.
- pages requested and successfully processed.
- HTTP attempts and retries.
- records fetched.
- records accepted.
- records inserted and updated.
- duplicates detected.
- records quarantined.
- records skipped by a documented policy.
- checkpoint before and after.
- duration.
- failure category and sanitized error message.

Use structured logs with run_id on relevant messages.

Expose a summary command or endpoint that allows an operator to inspect a run and its errors.

Define row-count semantics carefully. A fetched record, validated record, inserted row, updated row, and quarantined row are not interchangeable.

Make count reconciliation possible without double-counting records that appear on multiple pages or are replayed across runs.

## 12. Sample Data and Scalability

Create a deterministic data generator with a fixed random seed.

Support commands such as:

- Generate a small review dataset.
- Generate a larger dataset with configurable row count.
- Generate a dataset containing labelled edge cases.
- Start the mock API.
- Run ingestion.
- Inspect a run.
- Generate a reconciliation report.

Provide a small default dataset suitable for a quick evaluation and a configurable 100,000+ record workload where practical.

Include both valid and invalid cases:

- Ordinary records.
- Boundary values.
- Nulls and missing required fields.
- Duplicate identifiers.
- Conflicting duplicates.
- Invalid reference keys.
- Malformed payloads.
- Late and out-of-order updates.
- Empty datasets.
- Unexpected fields.

Use a fixed seed so the same parameters produce repeatable input.

Keep any expected-answer labels or test-oracle metadata separate from the source records consumed by the pipeline.

Explain which invalid scenarios are represented as data records and which must be injected as HTTP or transport failures.

## 13. Testing Requirements

Write meaningful automated tests. Do not create superficial tests that merely assert that a function returns something.

Cover at least:

1. Successful multi-page ingestion.
2. Empty source dataset.
3. Malformed HTTP response.
4. Missing required fields.
5. Null values.
6. Duplicate records.
7. Conflicting duplicate records.
8. Invalid foreign keys.
9. Unexpected schema changes.
10. HTTP 429 with Retry-After.
11. Transient 500 or 503 followed by recovery.
12. Exhausted retries.
13. Invalid authentication.
14. Timeout or simulated interruption.
15. Checkpoint persistence.
16. Restart after partial failure.
17. Rerunning the same input without duplicate target rows.
18. Late or out-of-order updates.
19. Older versions not overwriting newer target state.
20. Raw-response retention.
21. Quarantine traceability.
22. Database transaction and uniqueness behavior.
23. Reconciliation correctness.

Use unit tests for pure logic and integration tests against a real disposable PostgreSQL instance, preferably using Docker Compose or Testcontainers if the environment supports it.

Provide a test mode that does not depend on external internet access or third-party credentials.

Test repeatability: running the same suite twice must not depend on leftover database state.

Measure test coverage and explain what the percentage does and does not prove.

## 14. Reconciliation and Demonstrable Results

Create SQL queries and a machine-readable or human-readable validation report that show:

- Total source records generated.
- Total records fetched.
- Unique source keys observed.
- Accepted records.
- Quarantined records.
- Inserted and updated target records.
- Duplicate deliveries.
- Unexplained count differences.
- Referential-integrity violations.
- Checkpoint progress.
- Number of retries and failed requests.
- Runtime and throughput.

Make the report consistent with actual run metadata and target database contents.

Provide sample SQL queries for examining orders, order events, run summaries, and quarantined records.

Explain why raw response counts may differ from accepted target counts.

A successful demonstration must use actual program output, not invented example numbers presented as execution evidence.

## 15. Performance Evaluation

Implement a repeatable benchmark for at least one realistic dataset size.

Measure:

- Input record count.
- Total elapsed time.
- Records processed per second.
- Number of HTTP requests.
- Retry count.
- Database writes.
- Memory usage if practical.

Use a monotonic timer and explain the benchmark environment.

Identify one measured or reasoned optimization, such as batched upserts, database indexing, bounded concurrency, or reduced redundant queries.

If comparing two approaches, keep the dataset and environment comparable and report the observed results honestly.

Do not invent benchmark results or claim scalability beyond what has been tested.

## 16. Suggested Repository Structure

Use a clear structure similar to:

api-ingestion-pipeline/
├── README.md
├── pyproject.toml
├── uv.lock or another appropriate lock file
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Dockerfile
├── alembic.ini
├── migrations/
├── docs/
│   ├── ARCHITECTURE.md
│   ├── DATA_DICTIONARY.md
│   ├── DESIGN_DECISIONS.md
│   ├── FAILURE_RECOVERY.md
│   ├── OPERATIONS_RUNBOOK.md
│   ├── PERFORMANCE_REPORT.md
│   ├── INTERVIEW_PREPARATION.md
│   ├── TECHNICAL_WALKTHROUGH.md
│   ├── QUESTIONS_AND_ANSWERS.md
│   ├── GLOSSARY.md
│   └── diagrams/
├── src/
│   └── ingestion_pipeline/
│       ├── config.py
│       ├── logging_config.py
│       ├── models.py
│       ├── schemas.py
│       ├── api_client.py
│       ├── retry_policy.py
│       ├── rate_limiter.py
│       ├── raw_archive.py
│       ├── validation.py
│       ├── transformations.py
│       ├── quarantine.py
│       ├── checkpoint.py
│       ├── repository.py
│       ├── pipeline.py
│       ├── metrics.py
│       ├── reporting.py
│       ├── cli.py
│       └── mock_api/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── fixtures/
│   └── conftest.py
├── scripts/
│   ├── generate_data.py
│   ├── run_pipeline.py
│   ├── benchmark.py
│   └── verify_results.py
├── data/
│   ├── samples/
│   └── expected/
└── reports/
    └── .gitkeep

Adapt this layout when necessary. Avoid excessive fragmentation or empty modules. Every module must have a clear responsibility.

Do not commit generated secrets, local databases, temporary files, large unnecessary datasets, or personal information.

## 17. Documentation Must Teach Me Everything

This is a critical requirement.

I am a recent AI/ML engineering graduate with Python full-stack and AI project experience. I want to learn the data-engineering concepts through this implementation, not merely receive code I cannot explain.

Create the following documentation.

### A. README.md

Include:

- Business problem.
- Project objectives.
- Features.
- Architecture diagram.
- Technology stack and reasons for each choice.
- Prerequisites.
- Installation and environment configuration.
- Database setup and migrations.
- Mock API startup.
- Data generation.
- Pipeline execution.
- Testing commands.
- Sample queries.
- Expected verification conditions.
- Performance benchmark instructions.
- Troubleshooting.
- Security notes.
- Limitations and future improvements.

A reviewer must be able to clone the public repository and run the project using the README alone.

### B. ARCHITECTURE.md

Explain:

- System components.
- Data flow and control flow.
- Responsibility of every module.
- How components communicate.
- Why the architecture is appropriate for a small assessment project.
- How to trace a record from the source API to the final target or quarantine.
- Where transactions begin and end.
- How failures propagate.
- Where state is stored and why.
- Include a Mermaid architecture diagram and sequence diagram.

For every major component, answer:

1. What problem does it solve?
2. Why is it needed?
3. What alternatives were considered?
4. Why was the chosen design selected?
5. What are its limitations?

### C. DATA_DICTIONARY.md

Document each table and important column with:

- Column name.
- Data type.
- Nullable status.
- Primary or foreign key status.
- Uniqueness constraints.
- Meaning.
- Example.
- Validation rules.
- Source and transformation logic.

Explain table grain, relationships, business rules, and how quarantined records can be traced back to raw source data.

### D. DESIGN_DECISIONS.md

Create an Architecture Decision Record-style explanation for every important decision.

Include at least:

- Python and the selected web framework.
- PostgreSQL rather than a local-only database.
- HTTP client selection.
- Pagination strategy.
- Retry and rate-limit policy.
- Authentication configuration.
- Raw response storage.
- Timestamp and cursor strategy.
- Idempotency and upserts.
- Transaction boundaries.
- Schema validation.
- Quarantine design.
- Run metadata and observability.
- Docker and local reproducibility.
- Test strategy.
- Performance optimization.
- Why distributed infrastructure was not introduced.

For each decision use this template:

**Decision:** What we chose.

**Context:** What problem we faced.

**Reasoning:** Why this choice makes sense.

**Alternatives:** At least one realistic alternative.

**Trade-offs:** Benefits and costs.

**Failure mode:** What can go wrong.

**Mitigation:** How the implementation handles it.

**Interview explanation:** A natural 30–60 second answer I can speak aloud.

**Likely follow-up:** A challenging question a panelist may ask.

**Strong answer:** An accurate, technically grounded response.

Do not make up measured performance or implementation features.

### E. INTERVIEW_PREPARATION.md

Create a detailed, step-by-step teaching handbook written for a beginner-to-intermediate engineer.

Explain, with examples:

- What data engineering is and why this project needs it.
- ETL versus ELT.
- API ingestion versus file ingestion.
- Pagination and cursor-based extraction.
- Rate limiting and HTTP 429.
- Exponential backoff and jitter.
- Idempotency.
- At-least-once versus exactly-once processing.
- Checkpoints, watermarks, and incremental loads.
- Event time versus processing time.
- Late-arriving data.
- Raw data retention and replay.
- Schema evolution.
- Data validation and quarantine.
- Primary keys, foreign keys, indexes, transactions and upserts.
- Database normalization and query plans.
- REST APIs, status codes, timeouts and authentication.
- Structured logging and operational metrics.
- Data reconciliation.
- Integration testing and failure injection.
- Throughput, batching, concurrency and memory usage.
- Docker and reproducible execution.

For each concept, explain:

1. The simplest possible definition.
2. Why it matters in this project.
3. A concrete example from the actual implementation.
4. The relevant source file and function.
5. A small code or SQL example.
6. Common mistakes.
7. How the implementation handles the issue.
8. A short interview-ready explanation.

Use practical examples with small datasets and actual schema names. Do not produce generic textbook content disconnected from the repository.

### F. TECHNICAL_WALKTHROUGH.md

Prepare a guided walkthrough I can follow during a panel discussion.

Structure it as a 10–15 minute presentation:

1. Business problem and requirements.
2. Architecture and technology choices.
3. Source API and pagination.
4. Retry and rate-limit handling.
5. Raw data retention.
6. Validation and quarantine.
7. Idempotent publishing and checkpoints.
8. Tests and failure recovery.
9. Reconciliation and performance results.
10. Limitations and future improvements.

For each section provide:

- What to say aloud.
- Which file or diagram to show.
- Which command to execute.
- What output to point out.
- Which design trade-off to explain.
- Likely follow-up questions.

Write in natural, professional English that I can learn and speak. Keep it technically accurate; do not exaggerate my achievements or claim work I have not performed.

### G. QUESTIONS_AND_ANSWERS.md

Prepare at least 50 project-specific interview questions, grouped into:

- Architecture.
- Python.
- SQL and PostgreSQL.
- REST APIs.
- Pagination and incremental ingestion.
- Retries and rate limiting.
- Data quality.
- Idempotency and recovery.
- Testing.
- Performance.
- Security.
- Production deployment.

Include a mix of basic, intermediate and challenging questions.

For each question provide a concise answer, a deeper explanation, and the actual implementation file or function that demonstrates the concept.

Include scenario questions such as:

- What happens if the API fails on page 8?
- What happens if the process crashes after writing data but before updating the checkpoint?
- What happens when two records share the same update timestamp?
- How do you avoid overwriting a newer order with an older replay?
- What happens if an API returns 429 for five consecutive requests?
- How would you detect a silent data loss?
- How would you support a historical backfill?
- How would you handle an additive or breaking schema change?
- What would you change if the source produced 10 million records daily?
- How would you migrate raw responses to object storage?
- How would you explain a difference between source counts and target counts?

### H. FAILURE_RECOVERY.md and OPERATIONS_RUNBOOK.md

Document operational procedures for:

- Authentication failures.
- Rate limiting.
- Source outages.
- Database outages.
- Partial failures.
- Restarting from a checkpoint.
- Reprocessing a run.
- Backfilling a date range.
- Inspecting quarantined records.
- Recovering after schema changes.
- Diagnosing count mismatches.
- Avoiding duplicate writes during recovery.

Include exact commands and SQL queries that work with the actual implementation.

Clearly distinguish automated recovery from manual operator intervention.

### I. PERFORMANCE_REPORT.md

Include the benchmark method, environment, workload, command, measured results, throughput calculations, limitations, and one justified optimization.

Only include actual measurements after running the benchmark. Mark any planned experiment as not yet measured.

### J. GLOSSARY.md

Explain important terms in plain English, including:

API, endpoint, pagination, cursor, watermark, checkpoint, idempotency, upsert, transaction, foreign key, referential integrity, JSONB, raw archive, staging, quarantine, schema drift, backfill, retry, jitter, rate limit, throughput, latency, observability, reconciliation, event time, processing time, and at-least-once delivery.

## 18. Coding Standards

- Use type hints and clear function names.
- Keep configuration separate from application logic.
- Make retry and validation logic independently testable.
- Use context managers for resources.
- Close HTTP clients and database connections properly.
- Use parameterized SQL and ORM-safe operations.
- Avoid broad exception handling that hides root causes.
- Never silently swallow errors.
- Never commit secrets.
- Never use real external credentials in automated tests.
- Keep tests deterministic.
- Use UTC and timezone-aware timestamps.
- Use Decimal or appropriate database numeric types for monetary values.
- Use database constraints in addition to application validation.
- Avoid unnecessary abstractions and premature optimization.
- Comment on non-obvious reasoning rather than restating code.
- Add useful error messages and actionable logs.
- Keep generated test fixtures reproducible.
- Ensure commands work from a clean checkout.

## 19. GitHub and Submission Readiness

Prepare the project for a public GitHub repository.

Include:

- A professional README.
- A clear project description.
- An architecture diagram.
- A data dictionary.
- A documented setup process.
- Tests and test instructions.
- Example output reports generated by real executions.
- A `.env.example` with placeholder values only.
- A comprehensive `.gitignore`.
- No credentials, personal data, local secrets or unnecessary binary artifacts.
- A concise commit plan with meaningful commit messages.

Do not claim the project has been pushed to GitHub unless the push has actually succeeded. If GitHub credentials or access are unavailable, leave a clean repository and provide the exact commands I should run to create and push a public repository.

## 20. Required Implementation Workflow

Follow these milestones in order.

### Milestone 1: Inspect and plan

Inspect the existing workspace before changing files.

Identify existing code worth reusing. Propose the smallest complete architecture, define the entities, explain the cursor strategy, and identify the main risks.

Write a checklist of acceptance criteria.

### Milestone 2: Build the foundation

Create project configuration, environment settings, database models, migrations, the mock API, and deterministic sample data.

Run the database and smoke-test the mock API.

### Milestone 3: Build ingestion

Implement the HTTP client, authentication, pagination, rate limiting, retries, raw-response retention, validation, quarantine, transformations, checkpoints, and target publishing.

Explain the important decisions as they are implemented.

### Milestone 4: Verify correctness

Implement automated unit and integration tests.

Run the complete suite, fix failures, and demonstrate reruns, transient failures, partial failures, and restart recovery.

### Milestone 5: Produce evidence

Generate sample outputs, run summaries, reconciliation reports, and benchmark results from actual executions.

Investigate any unexplained discrepancy.

### Milestone 6: Write the documentation

Generate all documentation files based on the implementation that actually exists.

Ensure code references, commands, table names, diagrams, and examples agree with the repository.

### Milestone 7: Final acceptance review

Execute all applicable checks from a clean environment.

Check dependencies, migrations, Docker startup, mock API, ingestion, reruns, tests, reporting, and benchmark execution.

Inspect Git status and ensure secrets or unnecessary artifacts are not committed.

Produce a final acceptance report marking each requirement PASS, FAIL, or NOT VERIFIED, with the relevant evidence.

## 21. Working Rules

- Do not stop after providing the repository structure or initial code.
- Do not replace real implementation with pseudocode.
- Do not invent successful test results, benchmark figures, screenshots, or execution logs.
- Do not claim external services were tested if they were not.
- If an environmental limitation prevents a test, state the limitation and provide a reproducible command for running it elsewhere.
- If a technical requirement is ambiguous, choose a reasonable design, document the assumption, and continue unless the ambiguity blocks correctness.
- If a test fails, investigate the root cause rather than weakening the test merely to achieve a green result.
- Prefer a complete vertical slice over a large collection of unfinished features.
- Keep the system understandable enough that a junior engineer can explain it.
- Explain unfamiliar concepts before relying on them in complex implementation decisions.
- Keep all documentation synchronized with the real source code.
- Do not expose secrets in logs or documentation.
- Make all examples and expected results reproducible.

## 22. Final Deliverables

When finished, provide:

1. The complete working source code.
2. Reproducible environment and setup files.
3. The mock API and synthetic data generator.
4. Database models and migrations.
5. The ingestion pipeline and command-line interface.
6. Automated unit and integration tests.
7. Actual test execution results and coverage report.
8. Actual sample ingestion output and reconciliation report.
9. A performance benchmark with real measurements.
10. README and all requested technical documentation.
11. The interview preparation handbook and project walkthrough.
12. A final acceptance checklist with evidence.
13. GitHub publication instructions or confirmation of a verified successful push.
14. A concise list of remaining limitations and next steps.

Start by inspecting the workspace, then proceed through the milestones. My goal is not only to submit a working project but also to understand it well enough to defend every major design decision in a technical interview.

https://github.com/vinu3783/Yottaflex.ai
use Vinayaka G C 
vinayakagc210@gmail.com
dont add cluade as contruber alone me
````
