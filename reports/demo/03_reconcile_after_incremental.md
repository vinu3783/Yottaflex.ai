# Reconciliation report

Generated: 2026-10-29T15:37:04.343360+00:00  
Overall result: **FAIL**

## Checks

| Result | Check | Detail |
|---|---|---|
| PASS | orders: per-run fetched = inserted+updated+duplicate+stale+quarantined = ledger rows | `{"runs_checked": 2, "mismatched_runs": []}` |
| PASS | orders: target rows = 'inserted' outcomes (each key inserted once) | `{"target_rows": 212, "inserted": 212}` |
| PASS | orders: raw 'ok' records = ledger rows + unprocessed raw records | `{"raw_ok_records": 677, "ledger_rows": 677, "raw_ok_records_not_processed": 0}` |
| PASS | order_events: per-run fetched = inserted+updated+duplicate+stale+quarantined = ledger rows | `{"runs_checked": 2, "mismatched_runs": []}` |
| PASS | order_events: target rows = 'inserted' outcomes (each key inserted once) | `{"target_rows": 587, "inserted": 587}` |
| PASS | order_events: raw 'ok' records = ledger rows + unprocessed raw records | `{"raw_ok_records": 634, "ledger_rows": 634, "raw_ok_records_not_processed": 0}` |
| PASS | referential integrity: every order_event has an order | `{"violations": 0}` |
| PASS | orders: business rules hold in target | `{"violations": 0}` |
| FAIL | oracle: orders target = expected keys and latest valid version | `{"expected": 214, "actual": 212, "missing": ["ORD-00000240", "ORD-00000242"], "missing_count": 2, "unexpected": [], "wrong_version": [], "wrong_version_count": 0}` |
| PASS | oracle: order_events target = expected events | `{"expected": 587, "actual": 587, "missing_count": 0, "unexpected": [], "wrong": []}` |
| PASS | oracle: orders quarantine = labelled invalid rows with expected codes | `{"expected": 34, "actual": 34, "missing": [], "unexpected": [], "wrong_code": []}` |
| PASS | oracle: orders first-delivery outcome matches scenario label | `{"labelled_rows_observed": 633, "mismatches": [], "mismatch_count": 0}` |
| PASS | oracle: order_events quarantine = labelled invalid rows with expected codes | `{"expected": 8, "actual": 8, "missing": [], "unexpected": [], "wrong_code": []}` |
| PASS | oracle: order_events first-delivery outcome matches scenario label | `{"labelled_rows_observed": 597, "mismatches": [], "mismatch_count": 0}` |

## Per-entity counts

| Metric | orders | order_events |
|---|---|---|
| fetched_per_runs | 677 | 634 |
| ledger_rows | 677 | 634 |
| raw_ok_records | 677 | 634 |
| raw_ok_records_not_processed | 0 | 0 |
| distinct_source_rows_observed | 633 | 597 |
| observed_rows_without_id | 2 | 0 |
| source_rows_delivered_more_than_once | 42 | 37 |
| target_rows | 212 | 587 |
| quarantine_rows | 37 | 8 |
| quarantine_distinct_source_rows | 34 | 8 |
| quarantine_unresolved_distinct | 34 | 8 |
| outcome: inserted | 212 | 587 |
| outcome: updated | 385 | 0 |
| outcome: duplicate | 38 | 39 |
| outcome: stale | 5 | 0 |
| outcome: quarantined | 37 | 8 |

## Unresolved quarantine by error code (distinct source rows)

- **orders**: AMOUNT_OUT_OF_RANGE=2, CONFLICTING_DUPLICATE=2, INVALID_AMOUNT=2, INVALID_AMOUNT_PRECISION=2, INVALID_CURRENCY=2, INVALID_ENUM=4, INVALID_IDENTIFIER=2, INVALID_TIMESTAMP=4, INVALID_TYPE=2, MISSING_REQUIRED_FIELD=4, NEGATIVE_AMOUNT=2, NOT_AN_OBJECT=2, NULL_NOT_ALLOWED=2, TIMESTAMP_ORDER=2
- **order_events**: CONFLICTING_DUPLICATE=2, INVALID_ENUM=2, MISSING_REQUIRED_FIELD=2, UNKNOWN_ORDER_ID=2

## Runs

| status | runs | http_attempts | retries | 429s | 5xx | timeouts | seconds |
|---|---|---|---|---|---|---|---|
| succeeded | 2 | 8 | 3 | 1 | 2 | 0 | 2.18 |

## Checkpoints

```json
{
  "order_events": {
    "source_updated_at": "2026-01-01T14:32:55Z",
    "source_record_id": "EVTV-000000324"
  },
  "orders": {
    "source_updated_at": "2026-01-01T14:32:51Z",
    "source_record_id": "ORDV-000000324"
  }
}
```
