# Prompt 3 (master prompt, part 2 of 4)

**Tool:** Claude Code (VS Code extension), Claude Opus 5.5, same session as prompt 1.
**Note:** This file preserves a section of the original single master prompt; the prompt is divided at section boundaries.

````text
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


````
