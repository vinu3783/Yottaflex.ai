# Failure Recovery

What happens in each failure, what the pipeline does **automatically**, and
what needs an **operator**. Each scenario names the test that demonstrates it.

Legend: 🤖 automatic · 👤 manual operator action

## The one idea behind all recovery

> Progress is recorded only by committing a page, and a page commit includes
> its checkpoint. Writes are idempotent. **Therefore re-running is always safe**:
> after any failure, the correct recovery is to fix the cause (if any) and run again.

## Scenario table

| # | Failure | Automatic behaviour 🤖 | Operator action 👤 | Test |
|---|---|---|---|---|
| 1 | HTTP 429 | Wait ≥ `Retry-After` (capped at `PIPELINE_MAX_RETRY_AFTER_SECONDS`), retry; throttle pushed back | None. If frequent, lower `PIPELINE_REQUESTS_PER_SECOND` | `test_429_with_retry_after`, `test_429_honours_retry_after_then_recovers` |
| 2 | 429 with very long Retry-After | Fail fast (`retries_exhausted`), no hanging | Rerun later | `test_retry_after_beyond_limit_fails_instead_of_hanging` |
| 3 | 5xx / timeout / connection reset | Backoff with jitter, up to `PIPELINE_MAX_ATTEMPTS` | None | `test_transient_503_then_recovery`, `test_real_timeout_is_retried` |
| 4 | Source outage (retries exhausted, e.g. on page 8) | Run `failed` / `retries_exhausted`; pages 1-7 committed; checkpoint after page 7 | Rerun when source is back: resumes at page 8 | `test_exhausted_retries_fail_run_and_restart_resumes` |
| 5 | Invalid credentials (401/403) | Fails after **one** attempt (`authentication`), nothing written | Fix `PIPELINE_API_TOKEN`, rerun | `test_invalid_credentials` |
| 6 | Malformed response (non-JSON / wrong envelope) | Archived (`outcome=malformed`, `body_text`), retried | If persistent: investigate raw body, contact source | `test_malformed_response_retried_then_fails_clearly` |
| 7 | Invalid records | Quarantined with codes; run continues | Review quarantine; fix source; backfill if needed | `test_quarantine_traces_back_to_exact_raw_response` |
| 8 | Breaking schema change (required field gone) | Run fails **before** publishing that page; raw kept; checkpoint unchanged | Update code/mapping, deploy, rerun | `test_breaking_schema_change_stops_before_publishing` |
| 9 | Additive schema change (new field) | Accepted; counted in `unknown_fields`; warning logged | Decide whether to model the field | `test_unknown_fields_are_kept_and_reported_not_rejected` |
| 10 | Cursor does not advance | Run fails (`non_advancing_cursor`), no infinite loop | Report to source owner | `test_stuck_cursor_is_detected_not_looped` |
| 11 | Process killed mid-page | Postgres rolls back the page; run row stays `running` | Just rerun: it marks the old run `abandoned` and resumes | `test_crash_after_publish_before_commit_loses_nothing` |
| 12 | Crash right after a page commit | Checkpoint already includes that page | Rerun resumes after it | `test_crash_after_commit_resumes_from_checkpoint` |
| 13 | Database error / outage mid-page | Page rolled back; run `failed` / `database_error` (if DB reachable to record it) | Restore DB, rerun | `test_failed_publish_rolls_back_whole_page` |
| 14 | Two runs started at once | Second fails immediately (`another run … holds the pipeline lock`) | None | `test_only_one_run_at_a_time` |
| 15 | Late data within lookback | Re-read automatically next run | None | `test_incremental_phases_lookback_and_backfill` |
| 16 | Late data beyond lookback | **Not** picked up automatically | Backfill with `--since` (detected by reconciliation) | same test |
| 17 | Event arrives before its order | Quarantined `UNKNOWN_ORDER_ID` | Backfill after the order arrives; entry auto-resolved | `test_quarantined_event_resolved_when_parent_order_arrives_later` |
| 18 | Page budget reached (`--max-pages`) | Run `succeeded` with `stopped_early`; checkpoint at last page | Next run continues | `test_page_budget_stops_early_and_next_run_continues` |

## The two crash windows, precisely

```
 fetch page ──► archive raw ──► BEGIN … publish … checkpoint … COMMIT ──► next page
            (A)             (B)                                   (C)
```

* **Crash at A or B** (data received, not yet committed): nothing from this
  page is in the target; the checkpoint still points before it. The next run
  fetches the page again. The raw archive may contain the response twice
  (two runs). That is expected and visible.
* **Crash between "publish" and COMMIT**: the database discards the whole
  transaction, so this is the same as A/B.
* **Crash at C** (after COMMIT): page and checkpoint are both durable. The
  next run continues after it.
* **"Published but checkpoint not advanced" cannot happen**, because they are
  in the same transaction. If the target were a separate system, this window
  would exist and idempotent upserts would make the replay harmless. See
  ADR-10.

## Recovery procedures 👤

All commands assume the environment is configured (`.env`) and `ingest` is on PATH.

### Restart after any failed or abandoned run
```bash
ingest runs                         # find the failed run
ingest show-run <run_id>            # failure_category, error_message, counters
# fix the cause (token, source outage, code change) and simply run again:
ingest run
```

### Reprocess a run's time range
Runs are not "re-executed" by id; instead re-read the range they covered:
```bash
ingest show-run <run_id> | grep -A3 checkpoint_before   # where it started
ingest run --since 2026-01-01T03:00:00Z                 # re-read from there
```
Idempotent publishing guarantees no duplicates; outcomes will be mostly `duplicate`/`stale`.

### Backfill a date range
```bash
ingest run --since 2026-01-01T00:00:00Z --until 2026-01-02T00:00:00Z
ingest reconcile
```
* `--since` is inclusive; `--until` is page-granular (the run stops after the
  first page whose cursor passes it, so a few extra records may be processed, which is harmless).
* The checkpoint is only advanced if the backfill range is contiguous with it, and never moved backwards.

### Re-read everything (full reload, without deleting anything)
```bash
ingest run --since 2000-01-01T00:00:00Z
```

### Reset checkpoints (forces the next incremental run to start from the beginning)
```bash
ingest checkpoints show
ingest checkpoints reset --entity orders --yes
```

### Recover after a breaking schema change
1. `ingest show-run latest`: `failure_category = breaking_schema_change`, message names the field.
2. Inspect the stored response:
   ```sql
   SELECT raw_response_id, page_number, jsonb_object_keys(body->'data'->0) AS keys_in_first_record
   FROM raw_responses WHERE run_id = '<run_id>' AND outcome = 'ok' ORDER BY raw_response_id DESC LIMIT 20;
   ```
3. Update `schemas.py` (rename/mapping) and add a migration if the target changes.
4. `ingest run` re-reads from the unchanged checkpoint.

### Avoiding duplicate writes during recovery
Nothing special is required. That's the point of the design:
* orders: newer-version-wins upsert; same version = `duplicate` (no write);
* events: `ON CONFLICT (event_id) DO NOTHING`;
* checkpoints: monotonic;
* the advisory lock prevents two recoveries running concurrently.

Verify after any recovery:
```bash
ingest reconcile          # identity checks must all PASS
```

### Diagnosing a count mismatch
```sql
-- 1. Which run/entity does not add up?
\i sql/reconciliation.sql

-- 2. Records archived but never processed (crash or schema break)?
SELECT run_id, entity, page_number, record_count FROM raw_responses r
WHERE outcome='ok' AND record_count > 0
  AND NOT EXISTS (SELECT 1 FROM record_outcomes o WHERE o.raw_response_id = r.raw_response_id);

-- 3. Where did a specific record go?
SELECT run_id, outcome, raw_response_id FROM record_outcomes
WHERE source_record_id = '<id>' ORDER BY outcome_id;
```
Common, *legitimate* reasons for source count ≠ target count: multiple
versions per order, re-deliveries, lookback overlap, quarantine, and data later
than the lookback window that hasn't been backfilled. Each shows up as a
specific outcome or as a reconciliation check.
