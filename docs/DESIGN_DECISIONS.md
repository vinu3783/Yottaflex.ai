# Design Decisions (ADR style)

Each record follows the same template. The "Interview explanation" is written
to be spoken aloud in 30-60 seconds.

| # | Decision |
|---|---|
| 1 | Python 3.12 + FastAPI (mock) + a CLI pipeline |
| 2 | PostgreSQL instead of a local-only database |
| 3 | httpx as the HTTP client |
| 4 | Keyset pagination with a composite cursor |
| 5 | Retry and rate-limit policy |
| 6 | Authentication configuration |
| 7 | Raw responses stored as JSONB in PostgreSQL |
| 8 | Checkpoint = composite watermark + lookback window |
| 9 | Idempotency through business-key upserts and a version rule |
| 10 | One transaction per page |
| 11 | Pydantic-based validation with stable error codes |
| 12 | Record-level quarantine, page-level schema-break detection |
| 13 | Run metadata, per-record ledger and structured logs |
| 14 | Docker Compose for reproducibility |
| 15 | Test strategy: real PostgreSQL + in-process mock + oracle |
| 16 | Performance: batched upserts per page |
| 17 | No distributed infrastructure |

---

## ADR-1: Python 3.12, FastAPI for the mock API, a CLI for the pipeline

**Decision:** Python 3.12 for everything; the mock source API is a FastAPI app; the pipeline is a synchronous CLI program (`ingest run`).

**Context:** The assessment specifies the stack. The pipeline is a batch job, not a service: something (cron, a scheduler, an operator) starts it, it runs, it exits with a status code.

**Reasoning:** FastAPI gives a real HTTP server in very little code, with automatic OpenAPI docs and an in-process `TestClient` that is also an `httpx.Client`, so the same pipeline code runs against it in tests. A synchronous pipeline is easiest to reason about: keyset pagination is sequential anyway, so async would add complexity without speed.

**Alternatives:** an async pipeline (`asyncio` + `httpx.AsyncClient`); Flask for the mock; a long-running pipeline service with an HTTP trigger.

**Trade-offs:** + simple control flow, stack traces are readable, easy to test. − one page at a time; parallelism would need partitioning (by entity or time range).

**Failure mode:** a slow source makes runs long, because nothing overlaps network and database time.

**Mitigation:** page size is configurable (fewer round-trips); entities could run in parallel processes; the throttle, not the CPU, is usually the limit with real APIs.

**Interview explanation:** "The pipeline is a batch CLI, because ingestion jobs are started by a scheduler and should exit with a clear status. I kept it synchronous on purpose: cursor pagination is inherently sequential (I can't ask for page 5 before I know where page 4 ended), so async wouldn't make it faster, only harder to follow. The mock API is FastAPI because it gives me a real HTTP server, and its TestClient is an httpx client, so my tests exercise the exact same client code as production."

**Likely follow-up:** "When would you switch to async?"

**Strong answer:** "When there's independent work to overlap: several entities or partitions fetched concurrently, or a source with high latency where we're mostly waiting. Then I'd use bounded concurrency, a semaphore of N workers each owning a partition with its own checkpoint, still sharing one rate limiter so we don't exceed the source's limits."

---

## ADR-2: PostgreSQL instead of a local-only database

**Decision:** PostgreSQL 16 holds both the pipeline state (runs, checkpoints, raw archive, quarantine, ledger) and the target tables.

**Context:** We need durable state, atomic commits across data + checkpoint, upserts, JSON storage and constraints, and the database must behave like production.

**Reasoning:** Postgres gives ACID transactions, `INSERT … ON CONFLICT` upserts, JSONB, CHECK/FK/UNIQUE constraints, row-value comparison for keyset cursors, and advisory locks, everything this design relies on, in one dependency.

**Alternatives:** SQLite (local file); DuckDB (analytics); separate stores (Redis for checkpoints, S3 for raw, a warehouse for targets).

**Trade-offs:** + one transactional boundary for everything; realistic. − needs a running server (solved by Docker Compose); raw archive grows inside the DB.

**Failure mode:** database unavailable or full mid-run.

**Mitigation:** the page transaction rolls back cleanly; the run is marked `failed` with `database_error` if possible (or `abandoned` later); the next run resumes from the last committed checkpoint. `pool_pre_ping` recovers stale connections.

**Interview explanation:** "I used Postgres for both the pipeline's state and the target tables. The key reason is atomicity: the checkpoint and the data live in the same database, so one transaction commits both, and progress can never get ahead of the data. Postgres also gives me upserts with ON CONFLICT, JSONB for raw responses, constraints as a safety net, and advisory locks to stop two runs overlapping."

**Likely follow-up:** "In production the target is usually a warehouse like Snowflake or BigQuery. What changes?"

**Strong answer:** "The checkpoint and target would no longer share a transaction. I'd use either a MERGE into the warehouse keyed by business key plus a checkpoint written only after the load commits (replays stay idempotent thanks to MERGE), or stage files with a manifest and record the checkpoint with the load job id. The idempotency rule becomes what guarantees correctness."

---

## ADR-3: httpx as the HTTP client

**Decision:** `httpx.Client` with an explicit `Timeout`, wrapped by our `ApiClient`.

**Context:** We need timeouts, connection reuse, testability without a network, and a clear exception hierarchy for timeouts versus connection errors.

**Reasoning:** httpx has first-class timeouts, `MockTransport` for scripted responses, a sync and async API with the same interface, and FastAPI's `TestClient` subclasses it, so injection is trivial.

**Alternatives:** `requests` (+ `responses`/`requests-mock`); `aiohttp`; `urllib3` directly.

**Trade-offs:** + testability, explicit timeouts, HTTP/2 option. − slightly less ubiquitous than `requests`.

**Failure mode:** misconfigured or missing timeouts hang the job; httpx logging full URLs.

**Mitigation:** timeout is mandatory configuration (`PIPELINE_REQUEST_TIMEOUT_SECONDS`); httpx/httpcore loggers set to WARNING; our client logs sanitised endpoints only.

**Interview explanation:** "I chose httpx mainly for testability and explicit timeouts. In unit tests I plug in a MockTransport that scripts 429s, 503s, timeouts and broken JSON; in integration tests I plug in FastAPI's TestClient. The production code path is identical, only the transport changes."

**Likely follow-up:** "Why not let the HTTP library retry for you?"

**Strong answer:** "Library-level retries can't see our semantics: they don't archive each attempt, don't distinguish a malformed 200 from a good one, and may retry things that shouldn't be retried, like 401s. Owning the loop lets me classify errors, honour Retry-After with a cap, count retries in run metrics and keep every attempt in the raw archive."

---

## ADR-4: Keyset pagination with a composite cursor

**Decision:** The API pages in `(source_updated_at, source_record_id)` order; the cursor is the last row's pair (`after_updated_at`, `after_id`, exclusive). An `updated_since` parameter starts inclusive scans.

**Context:** The feed changes while we read it, many rows share the same second-precision timestamp (the generator creates ties on purpose), and we need a resumable position.

**Reasoning:** A unique, totally ordered key means "everything after X" is well defined even with ties. It is stable under inserts elsewhere in the feed, and efficient on the server (index range scan, no OFFSET).

**Alternatives:** OFFSET/LIMIT (rows shift → skips/duplicates, slow for deep pages); timestamp-only `>` (loses rows sharing the boundary timestamp); timestamp `>=` + dedupe (re-reads ties every page); opaque server page tokens (fine, but can expire and cannot be built from a checkpoint).

**Trade-offs:** + no lost ties, resumable, doubles as checkpoint. − the source must support ordering/filtering on both columns; rows inserted later with an older timestamp land behind the cursor.

**Failure mode:** a buggy source returns `has_more=true` with the same cursor → infinite loop; late rows behind the cursor.

**Mitigation:** the pipeline requires each `next_cursor` to be strictly greater than the previous one (`NonAdvancingCursorError`, tested); a max-pages budget; lookback window and backfill for late rows (ADR-8). `cursor_record_id` uses `COLLATE "C"` so Postgres orders ids exactly like the source.

**Interview explanation:** "I use keyset pagination on a composite key: the update timestamp plus the unique source row id. Timestamp alone isn't safe. If ten records share 10:00:00 and my page ends after the third one, 'give me rows after 10:00:00' skips the other seven. Adding the unique id as a tie-breaker gives a total order, so 'after (10:00:00, id 3)' is exact. The same pair is what I store as my checkpoint."

**Likely follow-up:** "What if the source only offers page numbers?"

**Strong answer:** "Offset pagination over a changing dataset can skip or repeat rows, so I'd compensate: sort by a stable key if possible, overlap pages, and rely on idempotent upserts to absorb repeats. And I'd reconcile against the source's total count. I'd also ask the API owner for an updated_since filter, because offset alone can't support safe incremental loads."

---

## ADR-5: Retry and rate-limit policy

**Decision:** Client-side throttle (minimum interval, `PIPELINE_REQUESTS_PER_SECOND`), bounded retries (`PIPELINE_MAX_ATTEMPTS`) with exponential backoff and **full jitter**, `Retry-After` honoured as a minimum but capped (`PIPELINE_MAX_RETRY_AFTER_SECONDS`). Only retryable categories are retried.

**Context:** The source returns 429s and intermittent 5xx; many clients may retry at once; some errors can never succeed on retry.

**Reasoning:** Throttling avoids most 429s. Exponential backoff gives the source room to recover. Full jitter (`uniform(0, min(cap, base·2^(n-1)))`) desynchronises clients, preventing retry storms. A cap on attempts and on `Retry-After` guarantees the job ends.

| Error | Retried? |
|---|---|
| 429 | yes, wait ≥ Retry-After (≤ cap) |
| 500, 502, 503, 504 | yes |
| timeout, connection error | yes |
| malformed JSON / envelope | yes (configurable: may be a truncated response) |
| 401, 403 | **no**: credentials won't fix themselves |
| other 4xx | **no**: our request is wrong |
| breaking schema change, programming errors | **no** |

**Alternatives:** fixed delay; exponential without jitter; unlimited retries; a circuit breaker; the `tenacity` library.

**Trade-offs:** + bounded, polite, testable (sleep and RNG injected). − a long outage fails the run (by design) instead of waiting indefinitely.

**Failure mode:** the source asks to wait an hour (`Retry-After: 3600`); a persistent 503.

**Mitigation:** `RetryAfterTooLong` → the run fails fast with `retries_exhausted`, a clear message, and the checkpoint at the last good page. The next scheduled run resumes.

**Interview explanation:** "I separate errors that retrying can fix from ones it can't. 429s, 5xx and timeouts get bounded retries with exponential backoff and full jitter, so if a hundred clients hit an outage they don't all come back at the same instant. If the server sends Retry-After I never retry sooner, but I cap it, so a job doesn't hang for an hour. 401s and other 4xx fail immediately, because retrying wouldn't change anything except our request count."

**Likely follow-up:** "What happens on five consecutive 429s?"

**Strong answer:** "With the default of five attempts, the client waits at least Retry-After each time. After the fifth 429 it raises RetriesExhaustedError. The run is marked failed with category retries_exhausted, the checkpoint stays at the last committed page, and nothing partial is published. The next run resumes from there. That exact case is a unit test: `test_five_consecutive_429s_are_bounded`."

---

## ADR-6: Authentication configuration

**Decision:** Bearer token from `PIPELINE_API_TOKEN` (`SecretStr`), header name and scheme configurable (`PIPELINE_AUTH_HEADER`, `PIPELINE_AUTH_SCHEME`). No credentials in code, git, logs or the raw archive.

**Context:** The API needs auth; the repo is public; logs and raw data are stored.

**Reasoning:** Environment configuration is the standard, works with Docker/CI secret stores, and `SecretStr` prevents accidental printing. A configurable header supports `X-API-Key`-style APIs without code changes.

**Alternatives:** OAuth2 client-credentials with token refresh; credentials file; secrets manager SDK.

**Trade-offs:** + simple, portable. − no automatic token refresh (a static token is enough for this source).

**Failure mode:** wrong/expired token; token leaking into logs or stored responses.

**Mitigation:** 401/403 → `AuthenticationError`, fails after **one** attempt (no lockout risk); the header is built only at send time; endpoints are sanitised; a log redaction filter; tests assert the token never appears in `raw_responses`.

**Interview explanation:** "The token comes from the environment as a Pydantic SecretStr, so even if I print the settings object it shows asterisks. It's only turned into a header at the moment of sending, and I never store headers. Only the sanitised URL and the query parameters go into the raw archive, and a test checks the token string isn't anywhere in that table. A 401 fails the run immediately, because retrying a bad credential just risks getting the account locked."

**Likely follow-up:** "How would you support OAuth2 with expiring tokens?"

**Strong answer:** "Add a small token provider: fetch a token with client credentials, cache it with its expiry, refresh slightly before expiry, and on a 401 refresh once and retry that request a single time before treating it as a real auth failure."

---

## ADR-7: Raw responses stored as JSONB in PostgreSQL

**Decision:** Every HTTP attempt, successful or not, is inserted into `raw_responses` (metadata + `body` JSONB or `body_text`) in its own transaction **before** processing. The database row is the authoritative copy.

**Context:** We must be able to replay, debug transformations and prove what the source sent, including failures.

**Reasoning:** JSONB is queryable (`body->'data'->5`), joins to quarantine and the ledger, needs no extra infrastructure, and is backed up with everything else. Writing it in a separate transaction means evidence survives even when page processing rolls back.

**Alternatives:** gzip JSON files on local disk; object storage (S3/GCS/MinIO) with a DB pointer; storing only the hash.

**Trade-offs:** + traceability with SQL, no dual-write consistency problem. − database size grows (≈ one row per page of up to 1,000 records); not the cheapest storage per GB.

**Failure mode:** archive grows without bound; sensitive data stored.

**Mitigation:** only sanitised request data is stored; `body_text` truncated at 64 KB for errors. Future: a retention job (e.g. delete bodies older than N days whose pages were committed) and/or move bodies to object storage.

**Interview explanation:** "Before I touch the data, I store the raw response, every attempt including the 429s and timeouts, in a JSONB table, in its own transaction. If a transformation bug ships, I can see exactly what the API sent and replay it. Quarantine rows point to the raw response and the record's position, so I can prove the rejected payload is byte-for-byte what we received."

**Likely follow-up:** "How would you migrate this to object storage?"

**Strong answer:** "Write the body to a key like `raw/{entity}/{date}/{run_id}/{page}-{attempt}.json.gz` first, then insert the metadata row with the URI and the SHA-256. The DB row stays authoritative for 'this attempt happened', the object is the payload, and the hash ties them. A reconciliation job checks every row's object exists and matches. For existing data, a backfill job copies bodies out in batches, verifies hashes, then nulls the JSONB column."

---

## ADR-8: Checkpoint = composite watermark + lookback window

**Decision:** The checkpoint per entity is the keyset position `(source_updated_at, source_record_id)` of the last committed page. Incremental runs start at `checkpoint − lookback_seconds` (default 3,600 s, inclusive `updated_since`); `--since` runs a backfill. Checkpoints only move forward, and a backfill moves one only when its range is contiguous with it.

**Context:** Records can arrive late, with timestamps older than rows we already read. A strict "after the checkpoint" would never see them.

**Reasoning:** Re-reading a recent window catches rows published late but within the window; idempotent publishing makes the overlap cost only some duplicate reads. Rows later than the window need an explicit backfill, and the demo shows this happening.

**Alternatives:** no lookback (fastest, misses late rows); full reload each run (simple, expensive); a source change-log sequence number (best, if the source offers one).

**Trade-offs:** + recovers realistic lateness at low cost; operators can widen the window. − rows later than the window are missed until a backfill; overlap costs extra requests.

**Failure mode:** a row arrives two days late; a backfill started after a gap would advance the checkpoint past unread rows.

**Mitigation:** `ingest run --since …`. Reconciliation against source counts (or the oracle) detects the gap; the demo shows exactly 2 missing rows found and recovered. The contiguity rule stops a non-contiguous backfill from moving the checkpoint.

**Interview explanation:** "My checkpoint is the last committed position in the feed, the timestamp plus id pair. Each incremental run starts an hour before it, by default, to catch records the source published late with older timestamps. The overlap just shows up as 'duplicate' outcomes because writes are idempotent. Anything later than the window needs a backfill with --since. In the demo, reconciliation flags exactly the two very-late orders and the backfill brings them in."

**Likely follow-up:** "How do you choose the lookback size?"

**Strong answer:** "From data: measure how late records actually arrive, the gap between source_updated_at and when they first appear, and pick a window above a high percentile, like p99.9, plus margin. Then monitor: if reconciliation against source counts starts finding gaps, widen it or schedule periodic wider backfills."

---

## ADR-9: Idempotency through business-key upserts and a version rule

**Decision:** Orders: `INSERT … ON CONFLICT (order_id) DO UPDATE … WHERE orders.source_updated_at < excluded.source_updated_at`. Events: `ON CONFLICT (event_id) DO NOTHING`. Before writing, each record is classified against current state (`FOR UPDATE`): inserted / updated / duplicate / stale / conflict.

**Context:** At-least-once extraction means the same record can come many times and in any order; mutable orders must not regress to older states.

**Reasoning:** The business key plus a monotonic version (`source_updated_at`) gives a deterministic winner regardless of arrival order. A content hash separates an identical re-delivery from a conflicting one. The SQL `WHERE` guard repeats the rule in the database (defence in depth).

**Conflict policy:**
| Same key and … | Outcome |
|---|---|
| newer `source_updated_at` | update |
| same timestamp, same hash | duplicate (no write) |
| same timestamp, different hash | **quarantine** `CONFLICTING_DUPLICATE`; first-published version kept |
| older timestamp | stale (ignored) |

**Alternatives:** last-write-wins by arrival (wrong under replays); delete + insert per batch; append-only versions + "latest" view; tie-break conflicts by `source_record_id`.

**Trade-offs:** + replays are harmless, target stays small, conflicts are visible instead of silently resolved. − latest state only; a genuine same-timestamp correction needs human review.

**Failure mode:** the source re-uses a timestamp for a legitimate correction; clock skew in the source.

**Mitigation:** conflicts are quarantined with both payloads traceable, never silently resolved by row order. A source sequence number would remove the ambiguity.

**Interview explanation:** "Idempotency means processing the same input twice leaves the same result. Every order is upserted by its business key, and the update only happens if the incoming version is strictly newer. That rule is in Python and again in the SQL WHERE clause. If the same version arrives again with the same content it's a harmless duplicate. Same version but different content is a real conflict, and instead of picking a winner arbitrarily I quarantine it for review. I tested this by re-running the whole feed and checking the target is byte-for-byte unchanged."

**Likely follow-up:** "Why not just keep the last one received?"

**Strong answer:** "Arrival order isn't business order. A retry or a backfill can deliver an old version after a new one. Last-received-wins would make the target go backwards. The source's version timestamp is the only order that means something, so that's what decides."

---

## ADR-10: One database transaction per page

**Decision:** Each page's upserts, quarantine rows, ledger rows, quarantine resolution, checkpoint advance and counters commit in **one** transaction. Raw archive writes are separate, earlier transactions.

**Context:** A crash can happen at any instruction.

**Reasoning:** A page is the natural unit: it has a cursor. All-or-nothing per page means after any crash the database is in a state that corresponds exactly to "pages 1..k committed", and resuming from the checkpoint is correct.

**Alternatives:** one transaction per run (huge, and progress is lost on failure); per record (slow, and a checkpoint mid-page is awkward); autocommit (inconsistent states).

**Trade-offs:** + simple crash-consistency argument. − page size bounds transaction size, so very large pages mean longer locks.

**Failure mode:** crash before COMMIT; a DB error mid-page.

**Mitigation:** Postgres rolls the whole page back; tests simulate a crash inside the transaction (`SimulatedCrash` in `before_commit`) and a DB error after the upsert, then assert nothing from that page persisted and a restart produces the reference state.

**Interview explanation:** "My unit of progress is a page. Everything a page causes (target rows, quarantine, ledger entries, counters, and the checkpoint) commits together. So after a crash there are only two possibilities: the page is fully there and the checkpoint is past it, or it isn't there at all and the checkpoint is before it. Either way the next run does the right thing."

**Likely follow-up:** "So is this exactly-once?"

**Strong answer:** "Not end to end. The API can deliver a record more than once, and I may fetch a page again after a crash. What's exactly-once is the *effect* on the target, because the checkpoint and data commit atomically and the writes are idempotent. I'd call it at-least-once delivery with idempotent, transactional processing."

---

## ADR-11: Pydantic-based validation with stable error codes

**Decision:** `OrderRecord` / `OrderEventRecord` Pydantic models whose `mode="before"` validators call pure normalisers from `transformations.py`; errors become stable codes (`NEGATIVE_AMOUNT`, `INVALID_ENUM`, …). Validation is independent of HTTP and the database.

**Context:** Many rule types (presence, nulls, types, enums, money, timestamps, cross-field ordering); results must be machine-countable and human-readable.

**Reasoning:** Pydantic gives structured, multi-error reporting. Our own codes (instead of Pydantic's internal error types) make quarantine queryable and stable across library upgrades. Pure normalisers are unit-tested without any framework.

**Rules (transformations):** strings trimmed; enums lower-cased; currency upper-cased; timestamps must carry an offset and are converted to UTC; amounts parsed as `Decimal` from string or JSON number (via `str()` to avoid float artefacts), max 2 decimals, **never rounded or defaulted**.

**Alternatives:** hand-written `if` checks; JSON Schema; Great Expectations / Pandera on batches.

**Trade-offs:** + typed output, reusable, precise errors. − Pydantic's error model needs a small translation layer.

**Failure mode:** a new field type or format from the source is rejected wholesale.

**Mitigation:** unknown fields are allowed and reported (ADR-12); the breaking-change check fails fast with a clear category instead of quarantining an entire feed.

**Interview explanation:** "Validation is its own layer: it gets a dict and returns either a clean, typed record or a list of issues with stable codes. It doesn't know about HTTP or the database, so I can test every rule in microseconds. Transformations are deterministic: trim, normalise case, convert to UTC, parse money as Decimal. They never invent data. A bad amount is rejected with a reason, never turned into zero."

**Likely follow-up:** "Why reject naive timestamps rather than assume UTC?"

**Strong answer:** "Because it's a guess. If the source meant IST, assuming UTC shifts every record by five and a half hours and nothing would ever flag it. Rejecting makes the problem visible on day one, and if we confirm the source's convention we can add an explicit, documented rule."

---

## ADR-12: Record-level quarantine, page-level schema-break detection

**Decision:** Invalid records go to `quarantine` with payload, codes, messages and the raw-response location; the run continues. If a *required* field is missing from ≥ 90% of a page (≥ 5 records), the run fails with `breaking_schema_change` **before** publishing or quarantining that page. Unknown extra fields are accepted, counted (`run_entity_stats.unknown_fields`) and logged as warnings.

**Context:** One bad record shouldn't block the feed, but a changed contract shouldn't quietly quarantine everything either.

**Reasoning:** Isolated bad rows are data-quality issues; a field vanishing everywhere is a contract change that needs a human and probably a code change.

| Change | Policy |
|---|---|
| New optional field | accepted, counted, warning |
| Field missing / null / wrong type on some records | those records quarantined |
| Required field missing on (almost) the whole page | run fails, nothing from that page published, raw kept |
| Malformed whole response | retried as transport-level problem, then run fails |

**Alternatives:** strict schema (reject unknown fields); fail on first invalid record; quarantine everything.

**Trade-offs:** + resilient and explicit. − thresholds are heuristics; a breaking change on a small page (< 5 records) is handled record-by-record.

**Failure mode:** a renamed field (`order_amount` → `amount`).

**Mitigation:** detected as missing `order_amount` on the whole page → run fails, checkpoint unchanged, raw response kept for diagnosis (tested). After a code/config fix, the next run re-reads the page.

**Interview explanation:** "I separate three cases. A new field the source added is additive, so I accept the record and count the field so we notice it. A bad value on one record is a data problem, so that record goes to quarantine with the exact reason and a pointer to the raw response, and everything else flows. A required field disappearing from the whole page is a contract change, so I stop the run before publishing that page, because quarantining 100% of the feed would just hide an outage."

**Likely follow-up:** "How do you reprocess quarantined records?"

**Strong answer:** "If the source fixes the data, a backfill re-reads it, and accepted rows automatically mark their old quarantine entries resolved; that's how events whose order arrived late get resolved. If the fix is on our side, deploy the fix and backfill from the earliest affected timestamp. Idempotent writes make that safe."

---

## ADR-13: Run metadata, per-record ledger and structured logs

**Decision:** `pipeline_runs` (status, mode, params, durations, HTTP counters, checkpoints before/after, failure category); `run_entity_stats` (record counters, unknown fields); `record_outcomes` (one row per delivered record); JSON logs with `run_id` and `entity` attached automatically through context variables.

**Context:** Operators need to know what happened; reviewers need to verify counts without double-counting replays.

**Reasoning:** Counters answer "how many", the ledger answers "which ones", logs answer "when and why". Counters are written in the page transaction, so they can't disagree with the data. The ledger makes reconciliation exact and gives per-record lineage.

**Alternatives:** logs only (hard to aggregate, can be lost); Prometheus metrics (great for alerting, not for audit).

**Trade-offs:** + exact reconciliation, operator CLI (`runs`, `show-run`). − the ledger grows by one row per delivery.

**Failure mode:** a crashed process leaves a run `running` for ever.

**Mitigation:** the next run (holding the lock) marks it `abandoned` with `failure_category=process_died`.

**Interview explanation:** "Every run gets a row with its status, timings, HTTP counters and checkpoint before and after. Each page also writes a ledger row per record saying what happened to it: inserted, updated, duplicate, stale or quarantined. That gives me an identity I check automatically: fetched equals the sum of the outcomes equals the ledger rows. Logs are JSON with the run id on every line, so I can go from a failed run to its exact log lines."

**Likely follow-up:** "What would you alert on?"

**Strong answer:** "Run failed or abandoned; no successful run within the expected interval (freshness); quarantine rate above a baseline; rising retry or 429 rate; new unknown fields; reconciliation failures; and checkpoint lag, the distance between now and the checkpoint timestamp."

---

## ADR-14: Docker Compose for reproducibility

**Decision:** One image (Python 3.12-slim + `uv sync --frozen` from `uv.lock`) for mock API and pipeline; Compose adds PostgreSQL 16 with `pipeline`, `pipeline_test`, `pipeline_bench` databases.

**Context:** A reviewer must run everything from a clean machine.

**Reasoning:** Pinned versions (lock file + image tags) give the same environment everywhere; Compose health checks order startup.

**Alternatives:** local installs only; devcontainers; Testcontainers for tests.

**Trade-offs:** + one-command environment. − requires Docker. It was not available on the macOS development machine, so it was verified separately on Windows with Docker Desktop (build, migrations, run, 130 tests, demo: all passed).

**Failure mode:** image build issues (e.g. lock file/uv version mismatch).

**Mitigation:** uv version in the Dockerfile matches the one that produced `uv.lock`; the local path (`uv sync`) is fully verified and documented as the primary route.

**Interview explanation:** "Docker Compose gives a reviewer Postgres, the mock API and the pipeline with one command, with versions pinned by the lock file. My development Mac didn't have Docker, so I developed against a local Postgres and then ran the whole Compose path on a Windows machine with Docker Desktop: image build, migrations, a pipeline run, the 130 tests and the demo all passed there."

**Likely follow-up:** "How would you run the integration tests in CI?"

**Strong answer:** "A GitHub Actions job with a `postgres:16` service container, `uv sync --frozen`, then `pytest --cov` with TEST_DATABASE_URL pointing at the service. The tests rebuild the schema from migrations, so no other setup is needed."

---

## ADR-15: Test strategy

**Decision:** Unit tests for pure logic (normalisers, validation, retry maths, rate limiter, HTTP classification via `httpx.MockTransport`, generator, mock API); integration tests against a **real** PostgreSQL with the mock API in-process; fault injection for every failure scenario; a generator **oracle** to check final state.

**Context:** The hard bugs here are about state across failures, and a mocked database can't catch them.

**Reasoning:** Real Postgres tests catch real behaviour (we found that multi-row INSERT reports `rowcount = -1` under SQLAlchemy's insertmanyvalues, which a mock would never show). Comparing the target with an independent expected state after a crash-and-restart proves recovery, not just "no exception".

**Alternatives:** SQLite for tests (different SQL semantics); mocking the repository; Testcontainers (good, but needs Docker).

**Trade-offs:** + high confidence, deterministic (seeded data, injected sleep). − needs a Postgres to run integration tests (they skip clearly if absent).

**Failure mode:** tests depend on leftover state.

**Mitigation:** schema rebuilt from the migration per session; all tables truncated before each test; suite run twice in a row to confirm (130/130 both times, plus once more from a fresh clone).

**Interview explanation:** "I test pure logic with fast unit tests, and everything stateful against a real Postgres, because the bugs that matter in pipelines live in transactions and constraints. My strongest tests kill the run at a specific point, restart it, and compare the database with what a clean run produces. Coverage is 90%, but I treat that as 'which lines ran', not proof. The assertions are what prove behaviour."

**Likely follow-up:** "What's not covered?"

**Strong answer:** "The benchmark harness is exercised by running it, not by unit tests. The Docker path isn't automated. And the mock can only produce the failure modes I designed. A real API might fail in ways I haven't simulated, like partial pages or changed sort order, which is why reconciliation runs on every load."

---

## ADR-16: Performance: batched upserts per page

**Decision:** Default `write_mode=batch`: one `SELECT … FOR UPDATE` and one multi-row upsert per page per entity. `write_mode=row` (one statement per record) is kept for comparison.

**Context:** Database round-trips dominate when each record is written separately.

**Reasoning:** Measured on the same 116,218-record workload: batch **4,608.7 rec/s** vs row **1,468.5 rec/s** (3.1×), both with reconciliation PASS ([PERFORMANCE_REPORT.md](PERFORMANCE_REPORT.md)).

**Alternatives:** `COPY` into a staging table + set-based `INSERT … SELECT … ON CONFLICT`; concurrency; larger pages.

**Trade-offs:** + 3× throughput with simple code. − higher peak memory (112 MB vs 70 MB) because a page's rows are built in memory; very large pages mean big statements.

**Failure mode:** page size × columns exceeds the PostgreSQL bind-parameter limit (65,535).

**Mitigation:** page size is capped at 4,000 by config validation (15 columns × 4,000 = 60,000 parameters). Benchmarks used 1,000. For larger batches the statement would have to be chunked or replaced by `COPY` into a staging table.

**Interview explanation:** "The biggest cost was database round-trips. Writing record by record means one statement per record; batching means one select and one upsert per page. On 116k records that took throughput from about 1,470 to about 4,600 records per second on my laptop, and both versions passed the same reconciliation, so it got faster without getting less correct."

**Likely follow-up:** "How would you go further?"

**Strong answer:** "COPY the page into an unlogged staging table and do one set-based upsert. Move raw bodies out of the transactional DB. Parallelise by entity or time partition with separate checkpoints. Then profile again. I'd only claim gains I'd measured."

---

## ADR-17: No distributed infrastructure

**Decision:** No Kafka, Spark, Airflow, Redis, Kubernetes or cloud services.

**Context:** The workload is one API, two entities, up to a few hundred thousand records per run on a laptop.

**Reasoning:** Each would add operational surface without solving a problem we have: Postgres already provides durable state, atomicity, locking and storage. The design keeps clear seams to add them later (scheduler → Airflow DAG calling `ingest run`; raw archive → object storage; partitions → workers).

**Alternatives:** Airflow for scheduling; Kafka as a buffer; Spark for transformation.

**Trade-offs:** + anyone can run and understand it; fewer failure modes. − no built-in scheduling or horizontal scaling.

**Failure mode:** volume grows by orders of magnitude.

**Mitigation:** for much larger volumes (e.g. 10 million records a day): partition, parallelise, move raw to object storage, consider a warehouse target. Each is a separate, measurable step.

**Interview explanation:** "I deliberately didn't add Kafka or Airflow. The hard problems here are correctness problems: checkpoints, idempotency, late data. Postgres transactions solve those. Adding distributed systems would give me more things that can fail and that a reviewer has to run, without making the data more correct. The code is structured so a scheduler or object storage can be plugged in later."

**Likely follow-up:** "Where would Airflow fit?"

**Strong answer:** "As the scheduler and alerting layer: a DAG task that runs `ingest run` hourly, then `ingest reconcile`, failing the DAG if reconciliation fails. The pipeline already has the properties Airflow wants: idempotent, resumable, exit codes."
