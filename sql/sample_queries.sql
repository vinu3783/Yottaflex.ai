-- Sample queries for exploring the pipeline database.
-- Run with:  psql "$DATABASE_URL" -f sql/sample_queries.sql
-- (DATABASE_URL without the "+psycopg" driver suffix, e.g. postgresql://pipeline:...@localhost:5432/pipeline)

\echo '== 1. Recent runs'
SELECT run_id, status, mode, started_at, duration_seconds, http_attempts, http_retries,
       http_rate_limited, http_server_errors, failure_category
FROM pipeline_runs ORDER BY started_at DESC LIMIT 10;

\echo '== 2. Per-run, per-entity counters (fetched = inserted+updated+duplicate+stale+quarantined)'
SELECT r.started_at, s.entity, s.pages_processed, s.records_fetched, s.records_inserted,
       s.records_updated, s.records_duplicate, s.records_stale, s.records_quarantined,
       s.records_fetched - (s.records_inserted + s.records_updated + s.records_duplicate
                            + s.records_stale + s.records_quarantined) AS unexplained
FROM run_entity_stats s JOIN pipeline_runs r USING (run_id)
ORDER BY r.started_at, s.entity;

\echo '== 3. Current checkpoints'
SELECT entity, cursor_updated_at, cursor_record_id, last_run_id, updated_at FROM checkpoints;

\echo '== 4. Orders: status distribution and money totals per currency'
SELECT currency, order_status, count(*) AS orders, sum(order_amount) AS total_amount
FROM orders GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 5. One order with its events in event-time order'
SELECT o.order_id, o.order_status, o.order_amount, o.currency, o.source_record_id AS version,
       e.event_type, e.event_timestamp
FROM orders o JOIN order_events e USING (order_id)
WHERE o.order_id = (SELECT order_id FROM order_events GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1)
ORDER BY e.event_timestamp;

\echo '== 6. Quarantine by entity and error code (distinct source rows, unresolved)'
SELECT entity, code, count(DISTINCT coalesce(source_record_id, md5(payload::text))) AS rows
FROM quarantine, unnest(error_codes) AS code
WHERE resolved_at IS NULL GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo '== 7. Trace quarantined records back to the raw HTTP response'
SELECT q.quarantine_id, q.entity, q.source_record_id, q.error_codes,
       r.raw_response_id, r.page_number, r.attempt, r.request_id, r.retrieved_at,
       (r.body -> 'data' -> q.record_position) = q.payload AS payload_matches_raw
FROM quarantine q JOIN raw_responses r USING (raw_response_id)
ORDER BY q.quarantine_id LIMIT 10;

\echo '== 8. Full history of one source row across runs (lineage ledger)'
SELECT o.source_record_id, p.started_at, p.mode, o.outcome, o.raw_response_id
FROM record_outcomes o JOIN pipeline_runs p USING (run_id)
WHERE o.source_record_id = (SELECT source_record_id FROM record_outcomes
                            WHERE source_record_id IS NOT NULL
                            GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1)
ORDER BY o.outcome_id;

\echo '== 9. HTTP attempts by outcome (retries, 429s, timeouts are all archived)'
SELECT entity, outcome, status_code, count(*) FROM raw_responses GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== 10. Additive schema drift observed per run'
SELECT run_id, entity, unknown_fields FROM run_entity_stats WHERE unknown_fields <> '{}'::jsonb;
