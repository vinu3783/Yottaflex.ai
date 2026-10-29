# Operations Runbook

Day-to-day operation of the ingestion pipeline. For *why* things behave this
way see [FAILURE_RECOVERY.md](FAILURE_RECOVERY.md).

## 0. Setup checklist

```bash
cp .env.example .env            # fill PIPELINE_DATABASE_URL, PIPELINE_API_TOKEN
uv sync && source .venv/bin/activate
ingest db-upgrade
ingest checkpoints show         # {} on a fresh database
```

Key settings (`.env`):

| Variable | Default | Tune when |
|---|---|---|
| `PIPELINE_PAGE_SIZE` | 500 (max 4,000) | Larger = fewer requests and transactions, more memory |
| `PIPELINE_REQUESTS_PER_SECOND` | 20 (0 = off) | Seeing 429s → lower it |
| `PIPELINE_REQUEST_TIMEOUT_SECONDS` | 10 | Slow source → raise it |
| `PIPELINE_MAX_ATTEMPTS` | 5 | Flaky source → raise it (bounded!) |
| `PIPELINE_BACKOFF_BASE_SECONDS` / `_MAX_SECONDS` | 0.5 / 30 | |
| `PIPELINE_MAX_RETRY_AFTER_SECONDS` | 60 | Longer Retry-After than this fails the run |
| `PIPELINE_LOOKBACK_SECONDS` | 3600 | Late data observed beyond it → raise it |
| `PIPELINE_MAX_PAGES_PER_RUN` | 10,000 | Safety budget per run |
| `PIPELINE_LOG_FORMAT` | json | `text` for humans at a terminal |

## 1. Routine operation

```bash
ingest run                    # schedule this (cron / Airflow) e.g. every 15 minutes
ingest reconcile              # schedule after each run; exit code 1 = investigate
```

Exit codes: `0` success · `1` run failed / reconciliation failed / lock held · `2` configuration error.

Health indicators to watch:

```sql
-- Freshness: last successful run and checkpoint lag
SELECT max(finished_at) AS last_success FROM pipeline_runs WHERE status = 'succeeded';
SELECT entity, now() - cursor_updated_at AS checkpoint_lag FROM checkpoints;

-- Quarantine rate per run
SELECT run_id, entity, records_quarantined::float / nullif(records_fetched, 0) AS rate
FROM run_entity_stats ORDER BY run_id DESC LIMIT 10;

-- Retry pressure
SELECT started_at, http_attempts, http_retries, http_rate_limited, http_server_errors, http_timeouts
FROM pipeline_runs ORDER BY started_at DESC LIMIT 10;
```

## 2. Inspecting runs

```bash
ingest runs --limit 20
ingest show-run latest
ingest show-run <run_id>
```

`show-run` prints run metadata, per-entity counters (with
`unexplained_difference`, which must be 0), HTTP attempts grouped by
outcome/status, and quarantine counts by error code.

Logs are JSON lines on stderr, each with `run_id` and `entity`:

```bash
ingest run 2> run.log
grep '"run_id": "<id>"' run.log | python -m json.tool --json-lines | less
grep '"level": "WARNING"' run.log        # retries, schema drift, early stop
```

## 3. Authentication failure

Symptom: run `failed`, `failure_category = authentication`, `http_attempts = 1`.
```bash
ingest show-run latest
# rotate/fix the token in the secret store / .env
ingest run
```
The pipeline does **not** retry 401/403 (avoids account lockouts).

## 4. Rate limiting

Symptom: `http_rate_limited` > 0, `retrying request … error_category=rate_limited` warnings.
* Occasional 429s are handled automatically.
* Persistent ones: lower `PIPELINE_REQUESTS_PER_SECOND`. If runs fail with
  "server asked to wait Ns (> allowed …)", the source is asking for a long pause:
  rerun later or raise `PIPELINE_MAX_RETRY_AFTER_SECONDS`.

Reproduce locally:
```bash
ingest mock-api --dataset-dir data/generated/review --rate-limit 2   # server allows 2 req/s
PIPELINE_REQUESTS_PER_SECOND=0 ingest run                           # client not throttling -> 429s
```

## 5. Source outage

Symptom: `failure_category = retries_exhausted`, message `… page N: giving up after K attempts (last error: source_unavailable …)`.
Everything before page N is committed. When the source recovers:
```bash
ingest run            # resumes from the checkpoint (lookback re-reads the last hour)
```

## 6. Database outage

Symptom: the CLI exits with a SQLAlchemy `OperationalError`; the run may stay
`running` (if the DB was unreachable to record failure).
1. Restore the database.
2. `ingest run`: marks the stuck run `abandoned` and resumes from the last committed page.

## 7. Partial failure / restart from checkpoint

```bash
ingest checkpoints show
ingest run                       # always resumes from the checkpoint
```
For a precise resume without the lookback overlap: `PIPELINE_LOOKBACK_SECONDS=0 ingest run`.

## 8. Reprocessing and backfills

```bash
ingest run --since 2026-01-01T00:00:00Z                               # backfill from a date
ingest run --since 2026-01-01T00:00:00Z --until 2026-01-01T12:00:00Z  # a window
ingest run --entity order_events --since 2026-01-01T00:00:00Z         # one entity
```
Safe to repeat; never creates duplicates; never moves checkpoints backwards.

## 9. Inspecting quarantined records

```sql
-- Summary
SELECT entity, code, count(*) FROM quarantine, unnest(error_codes) code
WHERE resolved_at IS NULL GROUP BY 1, 2 ORDER BY 3 DESC;

-- Details with the reason and the raw context
SELECT q.quarantine_id, q.source_record_id, q.business_key, q.errors, q.payload,
       r.page_number, r.request_id, r.retrieved_at
FROM quarantine q JOIN raw_responses r USING (raw_response_id)
WHERE q.entity = 'orders' AND 'NEGATIVE_AMOUNT' = ANY (q.error_codes)
ORDER BY q.rejected_at DESC LIMIT 20;

-- Events waiting for their order
SELECT business_key AS event_id, payload->>'order_id' AS missing_order
FROM quarantine WHERE 'UNKNOWN_ORDER_ID' = ANY(error_codes) AND resolved_at IS NULL;
```
After the source fixes data (or a missing order arrives), backfill from the
record's `source_updated_at`. Accepted records mark their quarantine entries
`resolved_at`.

## 10. Recovering after schema changes

* **Additive** (`unknown_fields` non-empty in `show-run`): nothing breaks. Decide whether to add the column (migration + schema + mapping).
* **Breaking** (`breaking_schema_change`): see [FAILURE_RECOVERY.md](FAILURE_RECOVERY.md#recover-after-a-breaking-schema-change).

Adding a column, step by step:
```bash
# 1. models.py + schemas.py change   2. generate and review a migration
uv run alembic revision --autogenerate -m "add sales_channel"
ingest db-upgrade
# 3. re-read the affected period so existing rows get the new field
ingest run --since <first date the field appeared>
```
Caveat: `orders` only changes when a strictly *newer* version arrives, so
re-reading unchanged versions will **not** fill a new column on existing rows.
Either populate it once from the retained raw data (an `UPDATE … FROM
raw_responses` keyed by `orders.raw_response_id` + `source_record_id`), or
reload the table (`TRUNCATE orders, order_events` then `--since` from the
beginning). Both are safe because the raw archive keeps every version.

## 11. Diagnosing count mismatches

```bash
ingest reconcile --out reports/tmp/now        # writes .json + .md, lists failing checks
psql "$DATABASE_URL" -f sql/reconciliation.sql
```
Then follow [FAILURE_RECOVERY.md → Diagnosing a count mismatch](FAILURE_RECOVERY.md#diagnosing-a-count-mismatch).

## 12. Mock API controls (local / test only)

```bash
ingest mock-admin stats
ingest mock-admin release-phase 2
ingest mock-admin set-faults '[{"entity":"orders","page":2,"kind":"status","status_code":503,"times":3}]'
ingest mock-admin set-faults '[{"entity":"orders","kind":"delay","delay_seconds":15,"times":1}]'      # timeout
ingest mock-admin set-faults '[{"entity":"orders","kind":"drop_field","field":"order_amount","times":1}]'
ingest mock-admin clear-faults
```

## 13. Housekeeping (not automated yet)

```sql
-- Size of the audit tables
SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
FROM pg_catalog.pg_statio_user_tables ORDER BY pg_total_relation_size(relid) DESC;

-- Example retention: drop bodies of committed pages older than 30 days (keeps metadata + hash)
UPDATE raw_responses r SET body = NULL
WHERE retrieved_at < now() - interval '30 days'
  AND EXISTS (SELECT 1 FROM record_outcomes o WHERE o.raw_response_id = r.raw_response_id);
```
Run retention only after agreeing the policy; once bodies are gone they can't be replayed.
