# Interview Preparation Handbook

A step-by-step guide to the data-engineering ideas in this project, written
for someone coming from Python / full-stack / ML. Every concept points at the
real file and function, so you can open the code while reading.

Each concept follows the same pattern:
**1. Simple definition · 2. Why it matters here · 3. Concrete example ·
4. Where in the code · 5. Code/SQL snippet · 6. Common mistakes ·
7. How this implementation handles it · 8. Interview-ready answer.**

Suggested reading order: 1 → 4 → 9 → 7 → 8 → 14 → 15, then the rest.

---

## 1. Data engineering, and why this project needs it

1. **Definition:** Building reliable systems that move data from where it is produced to where it is used, in a correct, timely and explainable way.
2. **Why here:** Analysts need a trustworthy copy of orders and events that only exist behind an unreliable API.
3. **Example:** 637 order source rows, delivered 1,315 times across 4 runs, become exactly 214 correct rows in `orders`, and we can prove it.
4. **Code:** the whole `src/ingestion_pipeline/` package; entry point `cli.py:main`.
5. **Snippet:**
   ```bash
   ingest run && ingest reconcile --dataset data/generated/review   # RESULT: PASS
   ```
6. **Mistakes:** treating it as "just call the API in a loop"; measuring success by "the job didn't crash".
7. **Here:** success = reconciliation passes; every record has an explained outcome.
8. **Answer:** "Data engineering is making data movement reliable and provable. My project pulls orders from a flaky API, and the interesting part isn't the HTTP call. It's guaranteeing that after crashes, retries and late data, the target is complete, has no duplicates, and every number can be reconciled."

## 2. ETL vs ELT

1. **Definition:** ETL = Extract, Transform, then Load the cleaned result. ELT = Extract, Load raw data first, Transform later inside the warehouse.
2. **Why here:** we do both: raw responses are loaded first (ELT-style), and validated/normalised records are loaded into typed tables (ETL-style).
3. **Example:** an order with `"order_status": "  SHIPPED "` is stored verbatim in `raw_responses.body` and as `shipped` in `orders`.
4. **Code:** `raw_archive.make_archiver` (load raw), `schemas.OrderRecord` + `transformations.normalize_enum` (transform), `repository.publish_orders` (load).
5. **Snippet:**
   ```sql
   SELECT body->'data'->0->>'order_status' FROM raw_responses LIMIT 1;  -- raw
   SELECT order_status FROM orders LIMIT 1;                              -- transformed
   ```
6. **Mistakes:** transforming before saving the raw data, so bugs can't be replayed.
7. **Here:** raw is archived in its own transaction *before* any transformation.
8. **Answer:** "It's a hybrid. I keep the raw API responses untouched, the ELT idea, so I can always replay. Then I validate and normalise in Python before loading typed tables, the ETL part, because I want bad records stopped and quarantined before they reach analysts."

## 3. API ingestion vs file ingestion

1. **Definition:** File ingestion reads complete files (CSV/Parquet) that already exist; API ingestion requests data piece by piece from a live service.
2. **Why here:** APIs add pagination, auth, rate limits, timeouts and partial failures that files don't have.
3. **Example:** run 2 of the demo hit two 503s and one 429 before completing.
4. **Code:** `api_client.ApiClient.fetch_page`.
5. **Snippet:** `GET /v1/orders?limit=500&after_updated_at=…&after_id=…` with `Authorization: Bearer …`.
6. **Mistakes:** no timeouts; assuming one request returns everything; not handling 429.
7. **Here:** explicit timeout, classified errors, bounded retries, every attempt archived.
8. **Answer:** "A file is a finished snapshot; an API is a conversation that can fail at any step. That's why my client classifies every response, retries only what can succeed, and keeps a record of every attempt."

## 4. Pagination and cursor-based extraction

1. **Definition:** Getting a large result in pages. A *cursor* says "continue after this item" rather than "skip N items".
2. **Why here:** offset pagination over a changing feed skips or repeats rows; timestamp-only cursors lose rows that share a timestamp.
3. **Example:** many order rows share `2026-01-01T00:10:00Z`. With cursor `(00:10:00, ORDV-…003)` the next page starts exactly at `ORDV-…004`.
4. **Code:** server: `mock_api/app.py:list_records` (bisect on `(ts, id)`); client: `pipeline._ingest_entity` (uses `next_cursor`), `checkpoint.Cursor`.
5. **Snippet:**
   ```python
   start = bisect.bisect_right(index.keys, (after_ts, after_id))  # strictly after
   rows = index.rows[start : start + limit]
   ```
6. **Mistakes:** `WHERE updated_at > :last_ts` (loses ties); OFFSET (unstable); trusting the server never to repeat a cursor.
7. **Here:** composite key → total order; `NonAdvancingCursorError` if the cursor doesn't move.
8. **Answer:** "I page with a keyset cursor: the timestamp plus a unique id. The id breaks ties, so 'everything after this row' is exact even when hundreds of rows share a second. And I check the cursor actually advances, so a buggy API can't put me in an infinite loop."

## 5. Rate limiting and HTTP 429

1. **Definition:** The server limits how many requests you may make; exceeding it returns `429 Too Many Requests`, often with `Retry-After: <seconds>`.
2. **Why here:** the source enforces limits; ignoring them gets you blocked.
3. **Example:** `test_429_with_retry_after`: the server says wait 3 s; the pipeline sleeps ≥ 3 s, then succeeds.
4. **Code:** `rate_limiter.RateLimiter` (client-side throttle), `api_client._classify` (429 → `RateLimitedError`), `retry_policy.parse_retry_after`. Server side: `mock_api/app.TokenBucket`.
5. **Snippet:**
   ```python
   if status == 429:
       return RateLimitedError(
           "rate limited", retry_after=parse_retry_after(resp.headers.get("Retry-After"))
       )
   ```
6. **Mistakes:** retrying 429 immediately; ignoring Retry-After; waiting for ever when Retry-After is huge.
7. **Here:** throttle proactively, honour Retry-After as a minimum, cap it (`max_retry_after_seconds`).
8. **Answer:** "I throttle on my side so I rarely hit the limit. When I do get a 429, I wait at least what the server asks, never less, but I cap the wait so the job can't hang for an hour. Then the run fails clearly and resumes next time."

## 6. Exponential backoff and jitter

1. **Definition:** After each failure, wait longer (×2 each time), and randomise the wait so many clients don't retry at the same instant.
2. **Why here:** transient 5xx errors; a retry storm could prolong an outage.
3. **Example:** base 0.5 s, max 30 s → ceilings 0.5, 1, 2, 4, 8 s; actual delay is random in `[0, ceiling]` ("full jitter").
4. **Code:** `retry_policy.RetryPolicy.backoff` / `delay_for`.
5. **Snippet:**
   ```python
   ceiling = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
   return self.rng.uniform(0, ceiling)
   ```
6. **Mistakes:** fixed delays; no maximum; no cap on attempts; jitter that's too small.
7. **Here:** bounded attempts, bounded delay, injected RNG and sleep for deterministic tests.
8. **Answer:** "Backoff gives the server time to recover; jitter stops a thundering herd. If a thousand clients all failed at 10:00:00 and all retried after exactly one second, they'd knock the server over again. Full jitter spreads them across the whole window."

## 7. Idempotency

1. **Definition:** Doing the same operation twice has the same effect as doing it once.
2. **Why here:** pages are re-read after crashes, retries and lookbacks.
3. **Example:** demo run 4 re-read data: `inserted 0, updated 0, duplicate 1`; tables unchanged.
4. **Code:** `repository._upsert_orders` (`ON CONFLICT … WHERE older`), `_publish_events_batch` (`DO NOTHING`).
5. **Snippet:**
   ```sql
   INSERT INTO orders (...) VALUES (...)
   ON CONFLICT (order_id) DO UPDATE SET ... 
   WHERE orders.source_updated_at < EXCLUDED.source_updated_at;
   ```
6. **Mistakes:** plain `INSERT` (duplicates or errors); "last write wins" by arrival time; dedupe only within a batch.
7. **Here:** business-key upsert + version rule, verified by `test_rerunning_same_input_creates_no_duplicates` (target identical after full replay).
8. **Answer:** "Idempotency is what makes retries safe. Every write is an upsert on the business key, guarded by the version timestamp, so replaying a page, or the whole feed, changes nothing. I prove it with a test that replays everything and compares the tables before and after."

## 8. At-least-once vs exactly-once

1. **Definition:** *At-most-once*: may lose data, never duplicates. *At-least-once*: never loses, may repeat. *Exactly-once*: neither, which is hard across independent systems.
2. **Why here:** we must not lose data, so we accept repeats and neutralise them.
3. **Example:** crash after fetching page 2 but before COMMIT → page 2 is fetched again next run.
4. **Code:** `pipeline._process_page` (one transaction), idempotent `repository`.
5. **Snippet:** at-least-once delivery + idempotent processing = effectively-once results.
6. **Mistakes:** claiming "exactly-once" because there's a checkpoint.
7. **Here:** checkpoint + data commit atomically (same DB), writes are idempotent; the README states the guarantee precisely.
8. **Answer:** "I don't claim exactly-once delivery. The API may send a record twice, and I may fetch a page twice after a crash. What I guarantee is the target ends up as if each record were applied exactly once: at-least-once delivery plus idempotent, transactional writes."

## 9. Checkpoints, watermarks and incremental loads

1. **Definition:** *Incremental load* = only fetch what changed. *Watermark* = "I have everything up to here". *Checkpoint* = the saved watermark used to resume.
2. **Why here:** reloading everything each run is wasteful; resuming after a crash must be exact.
3. **Example:** `ingest checkpoints show` → `orders: {source_updated_at: 2026-01-01T14:32:51Z, source_record_id: ORDV-000000324}`.
4. **Code:** `checkpoint.advance` (monotonic SQL), `checkpoint.start_params`, `pipeline._start_position`.
5. **Snippet:**
   ```sql
   ON CONFLICT (pipeline_name, entity) DO UPDATE SET ...
   WHERE (c.cursor_updated_at, c.cursor_record_id) < (EXCLUDED.cursor_updated_at, EXCLUDED.cursor_record_id)
   ```
6. **Mistakes:** saving the checkpoint before data is committed (data loss); saving it in a different system without idempotent writes; letting it go backwards.
7. **Here:** saved in the same transaction as the page, only moves forward, backfill can't create gaps.
8. **Answer:** "My checkpoint is the last committed position, timestamp plus id, and it's written in the same database transaction as the data from that page. So the checkpoint can never claim something that isn't stored."

## 10. Event time vs processing time

1. **Definition:** *Event time*: when something happened. *Processing time*: when our system handled it.
2. **Why here:** events can be delivered long after they occurred; analyses must use event time.
3. **Example:** the `late:out_of_order_event` scenario: `event_timestamp` one day before its delivery; `ingested_at` = now.
4. **Code:** `models.order_events` (`event_timestamp` vs `ingested_at`), `datagen.event_edges`.
5. **Snippet:** `SELECT event_type, event_timestamp, ingested_at FROM order_events ORDER BY event_timestamp;`
6. **Mistakes:** ordering by ingestion time; overwriting event time with "now".
7. **Here:** both stored; distinct events always kept, whatever order they arrive in.
8. **Answer:** "Event time is when the customer actually paid; processing time is when my pipeline saw it. I store both, and analyses order by event time, so an event delivered a day late still lands in the right place in the timeline."

## 11. Late-arriving data

1. **Definition:** Records that show up after records with later timestamps were already processed.
2. **Why here:** a strict "after the checkpoint" read would never see them.
3. **Example:** demo phase 2 releases orders stamped 20 minutes before the watermark (recovered by the 1 h lookback) and 2 days before (recovered only by backfill).
4. **Code:** `checkpoint.start_params` (lookback), `cli.py run --since`, `datagen` scenarios `late:*`.
5. **Snippet:** `{"updated_since": iso(cursor.updated_at - timedelta(seconds=lookback_seconds))}`
6. **Mistakes:** assuming the source is append-only in timestamp order.
7. **Here:** lookback window + backfill + reconciliation that detects what's still missing.
8. **Answer:** "Late data is valid data that arrives out of order. Each run re-reads the last hour to catch it, and idempotent writes make the overlap free. For anything later, there's a backfill command, and my reconciliation flagged exactly the two orders the lookback couldn't reach."

## 12. Raw data retention and replay

1. **Definition:** Keeping the original data exactly as received, so you can reprocess it.
2. **Why here:** bugs in transformation, disputes about what the source sent, schema changes.
3. **Example:** every quarantine row links to `raw_responses` and its position; `body->'data'->position` equals the quarantined payload (tested).
4. **Code:** `raw_archive.make_archiver`, `api_client.AttemptRecord`.
5. **Snippet:**
   ```sql
   SELECT r.body->'data'->q.record_position = q.payload FROM quarantine q JOIN raw_responses r USING (raw_response_id);
   ```
6. **Mistakes:** keeping only transformed data; storing auth headers in the archive; never deleting anything.
7. **Here:** all attempts stored (incl. errors), sanitised, SHA-256 per body; retention policy documented as future work.
8. **Answer:** "I keep every raw response before transforming anything, including failed attempts, so I can always answer 'what exactly did the API send?' and replay after a bug fix."

## 13. Schema evolution

1. **Definition:** The structure of incoming data changes over time.
2. **Why here:** APIs add fields (harmless) or rename/remove them (breaking).
3. **Example:** `loyalty_tier` appears on some orders → accepted and counted; `order_amount` dropped from a whole page → run fails before publishing.
4. **Code:** `schemas.SourceRecord` (`extra="allow"`, `unknown_fields`), `validation.check_breaking_change`.
5. **Snippet:** `run_entity_stats.unknown_fields = {"loyalty_tier": 2, "sales_channel": 2}`
6. **Mistakes:** rejecting every unknown field (brittle); silently ignoring missing fields (corrupt data).
7. **Here:** additive = warn and count; record-level problems = quarantine; contract break = stop.
8. **Answer:** "I treat schema changes by impact. New fields are additive, so I accept and count them so someone notices. A field missing on one record is a data-quality issue, so that record is quarantined. A required field disappearing everywhere is a contract break, so I stop before publishing anything wrong."

## 14. Data validation and quarantine

1. **Definition:** Checking every record against rules; putting failures aside with reasons instead of loading or deleting them.
2. **Why here:** the source sends negative amounts, bad timestamps, unknown statuses, broken JSON elements.
3. **Example:** `ORDV-000000590` with `order_amount: "-15.00"` → quarantined `NEGATIVE_AMOUNT`, field `order_amount`, with its raw response id.
4. **Code:** `transformations.normalize_amount`, `validation.validate_record`, `quarantine.insert_quarantine`.
5. **Snippet:**
   ```python
   if amount < 0:
       raise InvalidValue("NEGATIVE_AMOUNT", f"amount {amount} is negative")
   ```
6. **Mistakes:** "fixing" data (`-15 → 0`, missing id → generated id); dropping rows with only a log line.
7. **Here:** stable codes, original payload kept, never repaired, all counted and reconciled against the generator's labels.
8. **Answer:** "Validation never repairs data. It accepts or rejects with a reason. Rejected records go to a quarantine table with the exact payload, all error codes, and a pointer to the raw HTTP response, so nothing disappears silently and an analyst can see exactly why."

## 15. Keys, indexes, transactions and upserts

1. **Definitions:** *Primary key*: unique row identity. *Foreign key*: a reference that must exist. *Index*: a lookup structure that speeds queries. *Transaction*: a group of changes that all happen or none do. *Upsert*: insert, or update if the key exists.
2. **Why here:** they are the database guarantees the whole design rests on.
3. **Example:** `order_events.order_id` FK → `orders`; inserting an event for `ORD-404` fails (tested in `test_database_constraints_back_up_application_rules`).
4. **Code:** `models.py` (constraints/indexes), `pipeline._process_page` (`with self.engine.begin()`).
5. **Snippet:**
   ```python
   with self.engine.begin() as conn:  # BEGIN
       publish(...)
       quarantine(...)
       ckpt.advance(...)
       tracker.save_entity(...)
   # COMMIT here, or ROLLBACK on any exception
   ```
6. **Mistakes:** relying only on application checks; autocommit per statement; missing indexes on lookup columns.
7. **Here:** app validation + DB constraints (defence in depth), one transaction per page, indexes on `customer_id`, `source_updated_at`, `(order_id, event_timestamp)`, ledger and quarantine lookups.
8. **Answer:** "Application validation catches problems with good messages; database constraints are the last line of defence if a bug slips through. And the transaction is what makes a page all-or-nothing."

## 16. Normalisation and query plans

1. **Definition:** *Normalisation*: store each fact once, link with keys. *Query plan*: how the database decides to execute a query (`EXPLAIN`).
2. **Why here:** orders and events are separate tables linked by `order_id`, so event facts don't repeat order data.
3. **Example:** finding an order's events uses the `ix_order_events_order_id (order_id, event_timestamp)` index.
4. **Code:** `models.py` indexes; `sql/sample_queries.sql` query 5.
5. **Snippet:**
   ```sql
   EXPLAIN SELECT * FROM order_events WHERE order_id = 'ORD-00000042' ORDER BY event_timestamp;
   -- Index Scan using ix_order_events_order_id ... (on larger tables; tiny tables may Seq Scan)
   ```
6. **Mistakes:** adding indexes "just in case" (slower writes); trusting a plan from a tiny table.
7. **Here:** indexes only where the pipeline or typical queries look things up.
8. **Answer:** "The model is normalised: orders hold the current state, events reference them. I index the columns the pipeline and analysts actually filter on, and I'd check with EXPLAIN on realistic volumes before adding more, because each index slows down writes."

## 17. REST APIs, status codes, timeouts and authentication

1. **Definition:** REST = resources over HTTP verbs. Status codes: 2xx success, 4xx client problem, 5xx server problem. Timeout = maximum time to wait for a response. Auth = proving who you are (here, a bearer token).
2. **Why here:** the error class decides whether to retry.
3. **Example:** 401 → fail after 1 attempt; 503 → retry; timeout → retry.
4. **Code:** `api_client._classify`, `errors.py`.
5. **Snippet:**
   | Code | Class | Retry |
   |---|---|---|
   | 200 | ok | n/a |
   | 400/404 | `ClientRequestError` | no |
   | 401/403 | `AuthenticationError` | no |
   | 429 | `RateLimitedError` | yes |
   | 5xx | `TransientServerError` | yes |
6. **Mistakes:** retrying 4xx; no timeout (job hangs forever); logging tokens.
7. **Here:** explicit timeout, classification, tokens never logged/stored.
8. **Answer:** "The status code tells me whose problem it is. 4xx means my request is wrong, so retrying won't help. 5xx and 429 are the server's problem right now, so I retry with backoff. And every request has a timeout, because 'no answer' is also a failure."

## 18. Structured logging and operational metrics

1. **Definition:** Logs as machine-readable key/value records (JSON) instead of free text; metrics = numbers describing behaviour over time.
2. **Why here:** you must find all log lines of a failed run and compute retry/quarantine rates.
3. **Example:** `{"event": "retrying request", "status_code": 503, "attempt": 1, "retry_in_seconds": 0.106, "run_id": "…", "entity": "orders"}`.
4. **Code:** `logging_config.JsonFormatter`, `ContextFilter` (adds `run_id`/`entity` via `contextvars`), `metrics.RunTracker`.
5. **Snippet:** `log.warning("retrying request", extra={"attempt": attempt, "status_code": ...})`
6. **Mistakes:** string-formatted logs that can't be filtered; secrets in logs; metrics only in logs.
7. **Here:** JSON logs with context, redaction filter, durable counters in tables.
8. **Answer:** "Logs are JSON with the run id attached automatically, so one grep or log query gives me a whole run. Counts aren't parsed from logs. They're written to run tables in the same transaction as the data, so they're exact."

## 19. Data reconciliation

1. **Definition:** Proving that what arrived, what was stored and what was rejected add up.
2. **Why here:** "job succeeded" ≠ "data complete".
3. **Example:** `orders: per-run fetched = inserted+updated+duplicate+stale+quarantined = ledger rows → PASS`.
4. **Code:** `reporting.reconcile`, `sql/reconciliation.sql`.
5. **Snippet:**
   ```sql
   SELECT count(*) FROM order_events e LEFT JOIN orders o USING (order_id) WHERE o.order_id IS NULL;  -- must be 0
   ```
6. **Mistakes:** comparing raw counts to target counts directly (they *should* differ); no per-record lineage.
7. **Here:** 14 checks including comparison with the generator's oracle; a test proves deleting one row makes it FAIL.
8. **Answer:** "Reconciliation is how I know the data is right, not just that the job ran. Every fetched record has exactly one outcome in a ledger, so I can show 1,315 deliveries became 214 orders and explain every one of the other 1,101: versions, duplicates, stale replays, quarantined. If a row silently disappears, the check fails. There's a test for that."

## 20. Integration testing and failure injection

1. **Definition:** *Integration test*: exercise real components together (here, real PostgreSQL). *Failure injection*: deliberately cause failures to test recovery.
2. **Why here:** the important bugs appear only with real transactions and failures.
3. **Example:** `test_crash_after_publish_before_commit_loses_nothing` kills the run inside page 2's transaction, restarts, and compares with a clean run.
4. **Code:** `tests/integration/test_failure_recovery.py`, `mock_api/faults.FaultRule`, `pipeline.Hooks`, `errors.SimulatedCrash`.
5. **Snippet:**
   ```python
   set_faults(api, [{"entity": "orders", "page": 3, "status_code": 503, "times": None}])
   ```
6. **Mistakes:** mocking the database; testing only the happy path; leftover state between tests.
7. **Here:** schema rebuilt from migrations, tables truncated per test, suite run twice.
8. **Answer:** "My most valuable tests break things on purpose: kill the process mid-transaction, make page 3 fail permanently, drop a field from the API, then restart and compare the database with what a clean run produces."

## 21. Throughput, batching, concurrency and memory

1. **Definition:** *Throughput*: records per second. *Batching*: grouping work into fewer operations. *Concurrency*: doing several things at once. *Memory*: peak RAM used.
2. **Why here:** database round-trips dominate per-record cost.
3. **Example:** batch 4,608.7 rec/s vs row 1,468.5 rec/s on 116,218 records; peak RSS 112 MB vs 70 MB.
4. **Code:** `repository._publish_orders_batch`, `config.write_mode`, `benchmark.run_benchmark`.
5. **Snippet:** `ingest benchmark --orders 20000 --modes batch row --database-url …/pipeline_bench`
6. **Mistakes:** optimising without measuring; claiming scale not tested; unbounded concurrency against a rate-limited API.
7. **Here:** measured both modes, same data, same checks; 100k-order run honestly marked NOT MEASURED.
8. **Answer:** "I measured before claiming anything. Batching one upsert per page instead of one per record gave about 3× throughput with identical, reconciled results. Against a real API the bottleneck would usually be the rate limit, not the database."

## 22. Docker and reproducible execution

1. **Definition:** Packaging the app and its dependencies into images so it runs the same everywhere; Compose starts several containers together.
2. **Why here:** reviewers need Postgres + mock + pipeline with minimal setup.
3. **Example:** `docker compose up -d db mock-api && docker compose run --rm pipeline ingest run`.
4. **Code:** `Dockerfile`, `docker-compose.yml`, `docker/initdb/`, `uv.lock`.
5. **Snippet:** `RUN uv sync --frozen`: install exactly what the lock file says.
6. **Mistakes:** unpinned dependencies; secrets baked into images; no health checks.
7. **Here:** lock file, `.dockerignore` excludes `.env`, health checks gate start-up. Honest caveat: Compose not executed on the dev machine.
8. **Answer:** "The lock file pins every dependency and the Compose file starts Postgres, the mock API and the pipeline with health checks. I'm upfront that I verified with a local Postgres because Docker wasn't available, so the Compose path is marked not verified."

---

## Practice: explain a full run in 60 seconds

> "When `ingest run` starts it takes an advisory lock so only one run happens,
> records a run row, and for orders then events computes where to start: the
> checkpoint minus a one-hour lookback. It fetches a page with a bearer token,
> throttled, retrying 429s and 5xx with jittered backoff, and stores every
> attempt in a raw table. Then, in one transaction, it validates each record,
> upserts valid ones by business key with a 'newer version wins' rule,
> quarantines invalid or conflicting ones with reasons, writes one ledger row
> per record, advances the checkpoint and updates the counters. Commit. Repeat
> until `has_more` is false. Afterwards, `ingest reconcile` proves the counts
> add up and the target matches the expected state."
