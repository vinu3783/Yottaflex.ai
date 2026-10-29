# Technical Walkthrough (10-15 minute panel presentation)

**Before the call:** PostgreSQL running, `.env` configured, two terminals open
in the repo, and this repo open in the editor. Optionally run `scripts/demo.sh`
once beforehand so `reports/demo/` is fresh.

Timing guide: sections 1-2 ≈ 2 min · 3-7 ≈ 7 min · 8-10 ≈ 4 min.

---

## 1. Business problem and requirements (1 min)

**Say:** "The goal is a reliable copy of orders and order events that are only
available through a paginated API. The API rate-limits, fails intermittently,
needs a token, sends duplicates, sends some bad records and publishes some
changes late. I needed the copy to be complete, duplicate-free, restartable
after crashes, and above all *provable*: every number reconcilable, every
rejected record traceable."

**Show:** README → section 2 table ("What it does").

**Trade-off to mention:** I built a small, correct system rather than a big distributed one.

**Likely questions:** *"What did you consider out of scope?"* Customer entity, scheduling, object storage, SCD-2 history; all listed in Limitations.

## 2. Architecture and technology choices (1-2 min)

**Say:** "It's a modular monolith: one Python package, one process per run, one
Postgres. The data path is client → raw archive → validation → upsert or
quarantine. The control path is run manager → checkpoint → retry policy →
metrics. Postgres is the key choice: the checkpoint and the data commit in the
same transaction, so progress can never get ahead of stored data."

**Show:** README architecture diagram, then [ARCHITECTURE.md §5 Transaction boundaries](ARCHITECTURE.md#5-transaction-boundaries).

**Trade-off:** Postgres for raw JSON is simple and queryable but not the cheapest storage at scale (object storage later).

**Likely questions:** *"Why not Kafka/Airflow?"* (ADR-17) · *"Why synchronous?"* (ADR-1)

## 3. Source API and pagination (1 min)

**Say:** "The mock API is FastAPI. It pages by a keyset cursor, the timestamp
plus a unique source row id. Timestamp alone would lose records that share a
second; the id makes the order total."

**Show:** `src/ingestion_pipeline/mock_api/app.py` → `list_records` (the `bisect` lines) and [ARCHITECTURE.md §2](ARCHITECTURE.md#2-source-api-contract).

**Run** (terminal 1 / terminal 2):
```bash
ingest mock-api --dataset-dir data/generated/review --port 8000
curl -s -H "Authorization: Bearer $PIPELINE_API_TOKEN" "localhost:8000/v1/orders?limit=2" | python -m json.tool | head -30
curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/v1/orders      # 401
```
**Point out:** `next_cursor` with `after_updated_at` and `after_id`; the 401 without token.

**Likely question:** *"What if the cursor doesn't advance?"* → `NonAdvancingCursorError`, tested.

## 4. Retry and rate-limit handling (1-2 min)

**Say:** "Errors are classified. 429, 5xx, timeouts and malformed bodies are
retried with exponential backoff and full jitter, bounded attempts, and
Retry-After respected but capped. 401 and other 4xx fail immediately. Every
attempt, failed ones included, goes to the raw archive."

**Show:** `retry_policy.py` (`backoff`, `delay_for`) and `api_client.py` → `_classify`.

**Run:**
```bash
ingest mock-admin set-faults '[{"entity":"orders","status_code":503,"times":2},{"entity":"order_events","status_code":429,"retry_after":1,"times":1}]'
ingest run 2>&1 | grep -E "retrying|run finished"
```
**Point out:** JSON log lines with `attempt`, `status_code`, `retry_in_seconds`, `run_id`.

**Likely question:** *"Five 429s in a row?"* → bounded: run fails `retries_exhausted`, resumes later (test `test_five_consecutive_429s_are_bounded`).

## 5. Raw data retention (1 min)

**Say:** "Before any transformation, each attempt is stored with status, request id, sanitised
parameters, SHA-256 and the body as JSONB, in its own transaction, so it
survives even if processing fails. No auth headers are stored; a test checks
the token never appears."

**Run:**
```sql
SELECT raw_response_id, entity, page_number, attempt, outcome, status_code, record_count
FROM raw_responses ORDER BY raw_response_id DESC LIMIT 8;
```
**Point out:** 503/429 rows next to the 200 rows of the same page.

**Likely question:** *"How would this scale?"* → object storage + hash + URI (ADR-7).

## 6. Validation and quarantine (1-2 min)

**Say:** "Validation is independent of HTTP: Pydantic models that call pure
normalisers. Codes are stable: NEGATIVE_AMOUNT, INVALID_ENUM, and so on. Nothing is repaired:
a bad amount is rejected, never set to zero. Rejected records keep their
original payload and point to the exact response and position."

**Show:** `transformations.py` → `normalize_amount`; `schemas.py` → `OrderRecord`.

**Run:**
```sql
SELECT q.source_record_id, q.error_codes, q.errors->0->>'message' AS reason,
       r.body->'data'->q.record_position = q.payload AS matches_raw
FROM quarantine q JOIN raw_responses r USING (raw_response_id) LIMIT 6;
```
**Point out:** `matches_raw = true`.

**Trade-off:** schema drift policy: additive accepted and counted, breaking stops the run.

**Likely question:** *"How do you reprocess quarantined data?"* → backfill; accepted rows auto-resolve quarantine entries.

## 7. Idempotent publishing and checkpoints (2 min)

**Say:** "Orders are upserted by business key, and only a strictly newer source
version may overwrite. That's in Python and again in the SQL WHERE clause. Same version and same
content is a harmless duplicate; same version and different content is a real
conflict and gets quarantined rather than resolved by row order. All of a page's effects,
including the checkpoint, commit in one transaction."

**Show:** `repository.py` → `_publish_orders_batch` and `_upsert_orders`; `checkpoint.py` → `_ADVANCE_SQL`; `pipeline.py` → `_process_page` (`with self.engine.begin()`).

**Run:**
```bash
ingest run --since 2000-01-01T00:00:00Z | grep -E '"records_(inserted|updated|duplicate|stale)"'
```
**Point out:** replaying the entire feed: 0 inserted, 0 updated.

**Likely questions:** *"Crash after writing data but before the checkpoint?"* → impossible here, same transaction. *"Exactly-once?"* → at-least-once delivery + idempotent writes.

## 8. Tests and failure recovery (1-2 min)

**Say:** "129 tests: unit tests for pure logic and integration tests on a real
Postgres. The important ones break things on purpose: kill the process inside
page 2's transaction, make page 3 fail permanently, drop a required field, then
restart and compare the database to a clean run."

**Show:** `tests/integration/test_failure_recovery.py` → `test_crash_after_publish_before_commit_loses_nothing`.

**Run:**
```bash
uv run pytest -q            # ~10 s
```
**Point out:** 129 passed; coverage 90%, and the limits of what coverage proves.

**Likely question:** *"How do you know tests don't depend on each other?"* → truncation per test, ran the suite twice.

## 9. Reconciliation and performance results (1-2 min)

**Say:** "Reconciliation proves the numbers. Every fetched record has exactly one
ledger outcome, so fetched equals inserted plus updated plus duplicate plus
stale plus quarantined, for every run. With the generator's expected results I
also compare the final tables key by key."

**Show:** [reports/demo/03_reconcile_after_incremental.md](../reports/demo/03_reconcile_after_incremental.md) (one expected FAIL: 2 very-late orders) then [06_reconciliation.md](../reports/demo/06_reconciliation.md) (all PASS after backfill).

**Say (performance):** "Batching one upsert per page instead of one per record took
throughput from about 1,470 to 4,600 records per second on 116k records, same
reconciliation result. That's on my laptop with everything local; a real API's
rate limit would dominate."

**Show:** [PERFORMANCE_REPORT.md](PERFORMANCE_REPORT.md) results table.

**Likely question:** *"Why do 1,315 deliveries become 214 rows?"* → [DATA_DICTIONARY.md → count semantics](DATA_DICTIONARY.md#count-semantics).

## 10. Limitations and future improvements (1 min)

**Say:** "Being upfront about the limits: runs are sequential, one page at a time. Raw bodies live in
Postgres. Orders keep the latest state only. Data later than the lookback needs
a backfill. I couldn't run Docker or the 100k-order benchmark on my machine,
and that's marked in the acceptance report. Next steps: object storage for raw,
partitioned parallel runs, a scheduler with alerting on reconciliation, and a
retention job."

**Show:** [ACCEPTANCE_REPORT.md](ACCEPTANCE_REPORT.md).

---

## If you only have 5 minutes

1. README table (30 s) → 2. transaction-per-page idea (1 min) → 3. run with faults (1 min) → 4. replay = 0 inserted (1 min) → 5. reconciliation FAIL → backfill → PASS (1 min) → 6. limitations (30 s).

## Honest framing of AI use

**Say:** "I used an AI coding assistant heavily, and all my prompts are in
`prompt-history/`. What I personally own: the requirements and acceptance
criteria, reviewing each design decision, running and checking every
test and demo output, and being able to explain each module. The acceptance
report separates what was verified from what wasn't."

(Keep this accurate to what *you* actually did. See [AI_USAGE.md](AI_USAGE.md).)
