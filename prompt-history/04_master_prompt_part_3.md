# Prompt 4 (master prompt, part 3 of 4)

**Tool:** Claude Code (VS Code extension), Claude Opus 5.5, same session as prompt 1.
**Note:** This file preserves a section of the original single master prompt; the prompt is divided at section boundaries.

````text
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


````
