-- Reconciliation queries (the same logic `ingest reconcile` runs, as plain SQL).
-- Every query below should return 0 rows / 0 unless stated otherwise.

\echo '== R1. Runs whose counters do not add up (expect 0 rows)'
SELECT s.run_id, s.entity, s.records_fetched,
       s.records_inserted + s.records_updated + s.records_duplicate + s.records_stale
         + s.records_quarantined AS explained,
       (SELECT count(*) FROM record_outcomes o
         WHERE o.run_id = s.run_id AND o.entity = s.entity) AS ledger_rows
FROM run_entity_stats s
WHERE s.records_fetched <> s.records_inserted + s.records_updated + s.records_duplicate
                           + s.records_stale + s.records_quarantined
   OR s.records_fetched <> (SELECT count(*) FROM record_outcomes o
                             WHERE o.run_id = s.run_id AND o.entity = s.entity);

\echo '== R2. Target rows vs inserted outcomes (expect equal per entity)'
SELECT 'orders' AS entity, (SELECT count(*) FROM orders) AS target_rows,
       (SELECT count(*) FROM record_outcomes WHERE entity = 'orders' AND outcome = 'inserted') AS inserted
UNION ALL
SELECT 'order_events', (SELECT count(*) FROM order_events),
       (SELECT count(*) FROM record_outcomes WHERE entity = 'order_events' AND outcome = 'inserted');

\echo '== R3. Raw records that were archived but never processed (crash / breaking change)'
SELECT r.run_id, r.entity, r.page_number, r.record_count
FROM raw_responses r
WHERE r.outcome = 'ok' AND r.record_count > 0
  AND NOT EXISTS (SELECT 1 FROM record_outcomes o WHERE o.raw_response_id = r.raw_response_id);

\echo '== R4. Referential integrity: events without an order (expect 0)'
SELECT count(*) AS orphan_events
FROM order_events e LEFT JOIN orders o USING (order_id) WHERE o.order_id IS NULL;

\echo '== R5. Every current order version was actually delivered (expect 0)'
SELECT count(*) AS versions_without_delivery
FROM orders t
WHERE NOT EXISTS (SELECT 1 FROM record_outcomes o
                  WHERE o.entity = 'orders' AND o.source_record_id = t.source_record_id
                    AND o.outcome IN ('inserted', 'updated'));

\echo '== R6. Source rows delivered more than once, and how each delivery was classified'
SELECT outcome, count(*) FROM record_outcomes
WHERE source_record_id IN (SELECT source_record_id FROM record_outcomes
                           GROUP BY 1 HAVING count(*) > 1)
GROUP BY 1 ORDER BY 1;
