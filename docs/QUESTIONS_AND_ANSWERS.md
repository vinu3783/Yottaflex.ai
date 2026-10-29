# Interview Questions and Answers (60)

Format: **Q** (level) → **Short answer** → *Deeper* → `Where in the code`.
Levels: 🟢 basic · 🟡 intermediate · 🔴 challenging.

---

## A. Architecture

**1. 🟢 Describe the architecture in one sentence.**
A single-process Python pipeline that pages through an API, archives raw responses, validates records, idempotently upserts valid ones and quarantines the rest, committing each page and its checkpoint atomically in PostgreSQL.
*Deeper:* data path (client → archive → validate → publish/quarantine) and control path (run manager → checkpoint → retry policy → metrics) are separate modules.
`pipeline.py: Pipeline.run, _process_page`

**2. 🟢 Why a modular monolith and not microservices?**
One team, one job, one database. Splitting would add network calls and distributed failure modes without benefit.
*Deeper:* module boundaries (client, validation, repository) are the same seams you'd split on later.
`docs/ARCHITECTURE.md §1`

**3. 🟡 What is the unit of progress and why?**
A page. It has a cursor, so it maps cleanly to a checkpoint, and its effects commit in one transaction.
*Deeper:* per-record commits are slow and checkpointing mid-page is awkward; per-run transactions lose all progress on failure.
`pipeline.py: _process_page`

**4. 🟡 Where does state live?**
Everything durable is in Postgres: runs, counters, checkpoints, raw responses, ledger, quarantine, targets. Retry timing is in memory.
*Deeper:* putting checkpoints with the data is what enables atomic progress.
`models.py`, `docs/ARCHITECTURE.md §6`

**5. 🔴 How would you trace one record from the API to the target?**
`record_outcomes` has one row per delivery with `raw_response_id` + position; join to `raw_responses` for the exact JSON, and to `orders`/`quarantine` for its effect.
*Deeper:* quarantine rows carry the same pointer; a test checks `body->'data'->position = payload`.
`docs/ARCHITECTURE.md §8`, `test_quarantine_traces_back_to_exact_raw_response`

## B. Python

**6. 🟢 Why `Decimal` for money?**
Floats can't represent most decimal fractions exactly (`0.1 + 0.2 != 0.3`). Decimal keeps cents exact.
*Deeper:* JSON numbers are converted with `Decimal(str(value))` so `19.99` becomes `19.99`, not `19.989999…`; precision beyond 2 decimals is rejected, not rounded.
`transformations.normalize_amount`

**7. 🟡 How do `run_id` and `entity` get onto every log line?**
`contextvars` set at run/entity start; a logging `Filter` copies them into each record; a JSON formatter serialises extras.
*Deeper:* context vars are safe across threads/async tasks, unlike globals.
`logging_config.ContextFilter, JsonFormatter`

**8. 🟡 How are retries tested without waiting?**
Sleep and the RNG are injected. Tests pass a `FakeSleep` that records delays, and a seeded `random.Random`.
*Deeper:* the rate limiter takes an injectable clock too.
`retry_policy.RetryPolicy(rng=…)`, `tests/conftest.FakeSleep`

**9. 🟡 Why is `SimulatedCrash` a `BaseException`?**
So the pipeline's `except Exception` handlers *don't* catch it: like a real `kill -9`, the run row stays `running` and nothing else is recorded.
*Deeper:* that lets tests verify the abandoned-run detection on the next run.
`errors.SimulatedCrash`, `test_crash_after_publish_before_commit_loses_nothing`

**10. 🔴 What happens to an unexpected Python exception (a bug)?**
The run is recorded as `failed` with `unexpected_error` (or `database_error`), then the exception is **re-raised**: never swallowed.
*Deeper:* expected operational errors (`PipelineError`) are recorded and turned into exit code 1 without a stack trace.
`pipeline.Pipeline.run (except Exception … raise)`

## C. SQL and PostgreSQL

**11. 🟢 What does `ON CONFLICT DO UPDATE … WHERE` do here?**
Inserts a new order, or updates the existing one **only if** the incoming `source_updated_at` is newer.
*Deeper:* rows filtered by the WHERE aren't returned by RETURNING, so we count real writes.
`repository._upsert_orders`

**12. 🟡 Why `SELECT … FOR UPDATE` before upserting?**
To classify records against the current version while holding row locks, so a concurrent writer can't change it in between.
*Deeper:* with the advisory lock there is no concurrent writer today; this keeps it correct if that changes. Keys are sorted to keep lock order stable.
`repository._existing_orders`

**13. 🟡 How is the checkpoint kept from moving backwards?**
`ON CONFLICT … WHERE (old_ts, old_id) < (new_ts, new_id)`: a row-value comparison.
*Deeper:* `cursor_record_id` uses `COLLATE "C"` so the database orders ids byte-wise, the same order as the source.
`checkpoint._ADVANCE_SQL`

**14. 🟡 Why are there CHECK constraints if Python already validates?**
Defence in depth: if a bug bypasses validation, the database still refuses a negative amount or unknown status.
*Deeper:* tested by inserting bad rows directly.
`models.py`, `test_database_constraints_back_up_application_rules`

**15. 🔴 How do you prevent two runs at the same time?**
`pg_try_advisory_lock` on a key derived from the pipeline name; the second run fails immediately.
*Deeper:* session-level advisory locks are released automatically when the session ends, so a crashed process can't leave a stale lock (unlike a lock row/file).
`db.pipeline_lock`, `test_only_one_run_at_a_time`

## D. REST APIs

**16. 🟢 Which status codes are retried?**
429, 500, 502, 503, 504, plus timeouts, connection errors and malformed bodies. Not 401/403 or other 4xx.
`config.retryable_status_codes`, `api_client._classify`

**17. 🟡 Why not retry a 401?**
The credential won't fix itself; retrying wastes calls and can lock the account.
`errors.AuthenticationError`, `test_invalid_credentials`

**18. 🟡 What does a timeout protect against?**
A server that accepts the connection but never answers would hang the job for ever.
*Deeper:* the real-HTTP test uses a mock `delay` fault longer than the client timeout.
`test_real_timeout_is_retried`

**19. 🟡 What is `X-Request-ID` used for?**
Correlation: it's stored with each raw response so you can match our record with the provider's logs.
`raw_responses.request_id`

**20. 🔴 How do you treat a 200 response whose body is broken?**
As a transport-level failure: `MalformedResponseError`, archived with `body_text`, retried (configurable), then the run fails clearly. Distinct from a single invalid *record*, which is quarantined.
`api_client._classify`, `test_malformed_response_retried_then_fails_clearly`

## E. Pagination and incremental ingestion

**21. 🟢 Why not offset pagination?**
Inserts during the scan shift offsets → skipped or repeated rows; deep offsets are slow.
`docs/DESIGN_DECISIONS.md ADR-4`

**22. 🟡 What happens when two records share the same update timestamp?**
Nothing is lost: the cursor is `(timestamp, source_record_id)`, a total order, so the page boundary falls *between* them precisely.
*Deeper:* the generator uses second-precision timestamps to create many ties; the mock test checks every row is returned exactly once.
`test_keyset_pagination_returns_every_row_exactly_once`

**23. 🟡 What does the checkpoint represent?**
The keyset position of the last row of the last committed page, per entity.
`checkpoint.Cursor`, `checkpoints` table

**24. 🔴 How would you support a historical backfill?**
`ingest run --since 2026-01-01T00:00:00Z [--until …]`. It re-reads the range, upserts idempotently, never moves the checkpoint backwards, and only moves it forward if the range is contiguous with it.
`pipeline._start_position`, `docs/FAILURE_RECOVERY.md`

**25. 🔴 How are late-arriving records recovered?**
Each incremental run starts `lookback_seconds` before the checkpoint (inclusive `updated_since`). Later-than-window rows need a backfill; reconciliation detects them.
*Deeper:* demo: 2 rows 20 min late recovered automatically, 2 rows 2 days late flagged, then backfilled.
`checkpoint.start_params`, `test_incremental_phases_lookback_and_backfill`

## F. Retries and rate limiting

**26. 🟢 What is jitter?**
Randomness added to retry delays so clients don't retry in sync.
`retry_policy.RetryPolicy.backoff`

**27. 🟡 What happens if an API returns 429 for five consecutive requests?**
With `max_attempts=5`: four waits of ≥ Retry-After each, then `RetriesExhaustedError`. The run is marked `failed/retries_exhausted`, the checkpoint stays at the last committed page, and the next run resumes.
`test_five_consecutive_429s_are_bounded`

**28. 🟡 What if Retry-After is 3,600 seconds?**
Above `max_retry_after_seconds` (60) → fail fast with a clear message rather than hang.
`retry_policy.RetryAfterTooLong`

**29. 🟡 What's the difference between the client throttle and the server rate limit?**
The throttle (`requests_per_second`) is our politeness; the server limit is enforcement (429). Throttling avoids most 429s; retries handle the rest.
`rate_limiter.RateLimiter`, `mock_api/app.TokenBucket`

**30. 🔴 How do you avoid a retry storm?**
Bounded attempts, exponential backoff, full jitter, Retry-After respected, throttle pushed back after a 429 (`penalize`), and no retries for permanent errors.
`api_client.fetch_page`

## G. Data quality

**31. 🟢 What happens to a record with a negative amount?**
Quarantined with `NEGATIVE_AMOUNT`, field `order_amount`, original payload and raw-response pointer. Never converted to 0.
`transformations.normalize_amount`

**32. 🟡 How do you distinguish an invalid record from a late one?**
Invalid breaks a rule (quarantined). Late is valid but out of order (accepted, via lookback/backfill). Different codes, different tables.
`docs/DATA_DICTIONARY.md → Key concepts`

**33. 🟡 How would you handle an additive vs a breaking schema change?**
Additive (new field): accepted, counted in `unknown_fields`, warning logged. Breaking (required field missing on ≥ 90% of a page): run fails before publishing; raw kept; fix and rerun.
`validation.check_breaking_change`, `test_breaking_schema_change_stops_before_publishing`

**34. 🟡 What if an event references an order that doesn't exist?**
Quarantined `UNKNOWN_ORDER_ID`. If the order arrives later and the event is re-read (backfill), it's inserted and its quarantine entry is marked resolved.
`repository._publish_events_batch`, `quarantine.resolve_quarantine`

**35. 🔴 Why reject timestamps without a time zone?**
Assuming UTC could silently shift every record by hours. Rejecting surfaces the problem immediately.
`transformations.normalize_timestamp`

## H. Idempotency and recovery

**36. 🟢 What happens if you run the pipeline twice on the same data?**
Second run: all `duplicate`/`stale`, zero inserts or updates; tables identical.
`test_rerunning_same_input_creates_no_duplicates`

**37. 🟡 What happens if the API fails on page 8?**
Retries first. If they're exhausted, pages 1-7 are committed (checkpoint after page 7), page 8 is not, and the run is `failed`. The next run starts at page 8 (minus lookback overlap) and the end state equals an uninterrupted run.
`test_exhausted_retries_fail_run_and_restart_resumes` (uses page 3)

**38. 🟡 What if the process crashes after writing data but before updating the checkpoint?**
That window doesn't exist here: data and checkpoint are in one transaction, and a crash before COMMIT rolls back both. If they were in different systems, the page would be replayed and idempotent upserts would absorb it.
`test_crash_after_publish_before_commit_loses_nothing`

**39. 🟡 How do you avoid overwriting a newer order with an older replay?**
Version rule: update only if `source_updated_at` is strictly greater, checked in Python and in the upsert's WHERE.
`test_older_version_never_overwrites_newer`, `test_sql_guard_blocks_older_version_even_without_python_check`

**40. 🔴 Two deliveries with the same order and version but different content: which wins?**
Neither is chosen by arrival order. The first published stays, the conflicting one is quarantined `CONFLICTING_DUPLICATE` for review. Arbitrary resolution would hide a source bug.
`repository._publish_orders_batch`

## I. Testing

**41. 🟢 How many tests, and what kinds?**
130: 97 unit (no DB) and 33 integration (real PostgreSQL, mock API in-process or on a local port).
`tests/`

**42. 🟡 Why a real database in tests instead of mocks?**
The risky behaviour is transactional. A real Postgres caught a real bug: multi-row INSERT reporting `rowcount = -1`.
`test_sql_guard_blocks_older_version_even_without_python_check`

**43. 🟡 What does 90% coverage prove?**
That 90% of lines/branches executed. Not that they're correct: that comes from assertions such as comparing the target with the oracle after a crash/restart.
`reports/test_results.txt`

**44. 🟡 How is the suite repeatable?**
Schema rebuilt from migrations per session, all tables truncated before each test, seeded data, injected sleeps; ran twice in a row with identical results.
`tests/conftest.py`

**45. 🔴 What is the oracle and why is it separate?**
Expected final keys/versions, quarantine codes and per-row scenario labels written by the generator to `expected/`. The pipeline never reads it, so it can't "cheat"; reconciliation compares against it.
`datagen.write_dataset`, `reporting._oracle_checks`

## J. Performance

**46. 🟢 What throughput did you measure?**
4,608.7 records/s batch vs 1,468.5 row-by-row on 116,218 records, locally, both reconciled.
`docs/PERFORMANCE_REPORT.md`

**47. 🟡 Why is batching faster?**
~4 statements per page instead of ~2 per record: far fewer round-trips and statement executions.
`repository._publish_orders_batch`

**48. 🟡 What limits page size?**
PostgreSQL's 65,535 bind parameters per statement: 15 per order row → capped at 4,000 (tested).
`config.page_size`, `test_max_page_size_fits_postgres_parameter_limit`

**49. 🔴 What would you change if the source produced 10 million records daily?**
~115 rec/s average, but bursty. Steps, each measured: (1) raw bodies to object storage, (2) `COPY` + set-based upserts, (3) partition by entity/time with per-partition checkpoints and a shared rate limiter, (4) partition target tables by date, retention on ledger/raw, (5) probably a warehouse target with MERGE, (6) a scheduler with freshness/reconciliation alerts.
`docs/PERFORMANCE_REPORT.md → Further optimisations`

**50. 🔴 Where's the bottleneck against a real API?**
Usually the rate limit: max throughput ≈ `requests_per_second × page_size`. Bigger pages help more than faster inserts.

## K. Security

**51. 🟢 Where do credentials come from?**
Environment variables / an untracked `.env`; `.env.example` has placeholders only.
`config.PipelineSettings`

**52. 🟡 How do you make sure the token never leaks?**
`SecretStr`; header built only at send time; only sanitised endpoint + params archived; log redaction filter; tests search the raw table for the token.
`api_client._headers`, `logging_config.redact`, `test_archive_never_contains_credentials`

**53. 🟡 What about SQL injection?**
All values are bound parameters through SQLAlchemy; the only interpolated identifiers are internal constants.
`repository.py`, `reporting.py`

**54. 🟡 Is there sensitive data in the repo?**
No: synthetic data only, no real customers; generated datasets are gitignored; evidence files were checked for secrets.
`.gitignore`, `reports/README.md`

**55. 🔴 Any security caveats in the mock?**
`/_admin/*` endpoints are unauthenticated for local testing; disable with `MOCK_ENABLE_ADMIN=false`. It must never be exposed publicly.
`mock_api/app.py`

## L. Production deployment

**56. 🟡 How would you schedule this?**
Cron or an Airflow task running `ingest run` then `ingest reconcile`; non-zero exit fails the task. The advisory lock makes overlapping schedules safe.
`cli.py`

**57. 🟡 What would you monitor and alert on?**
Failed/abandoned runs, time since last success, checkpoint lag, quarantine rate, 429/5xx/retry rates, new unknown fields, reconciliation failures.
`docs/OPERATIONS_RUNBOOK.md §1`

**58. 🔴 How would you migrate raw responses to object storage?**
Write body to `raw/{entity}/{date}/{run_id}/{page}-{attempt}.json.gz`, then insert the metadata row with URI + SHA-256 (DB row authoritative). Backfill existing rows in batches, verify hashes, then null the JSONB. Reconcile URIs ↔ objects periodically.
`docs/DESIGN_DECISIONS.md ADR-7`

**59. 🔴 How would you detect a silent data loss?**
Ledger identity per run; target rows = inserted outcomes; source-side counts (or the oracle) vs target keys; raw records with no ledger rows. A test deletes one order and reconciliation fails.
`reporting.reconcile`, `test_reconciliation_detects_silent_data_loss`

**60. 🔴 How would you explain a difference between source counts and target counts?**
They measure different things. Demo: 1,315 order deliveries → 214 rows = 214 inserted + 385 updated (versions) + 252 duplicates + 395 stale (replays) + 69 quarantined. Every delivery has exactly one outcome, so there is zero unexplained difference.
`docs/DATA_DICTIONARY.md → Count semantics`

---

### Bonus scenario: "Walk me through what happens at 3 a.m. when the source is down for 20 minutes."
The scheduled run retries page N with backoff (≈ 0.5 + 1 + 2 + 4 s), exhausts 5 attempts, and records `failed / retries_exhausted` with "page N" in the message. Pages before N are committed. The next scheduled run starts from the checkpoint minus the lookback, re-reads the overlap as duplicates and completes. Reconciliation passes. No human action, unless an alert on "no success in X hours" fires.
