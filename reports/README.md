# Evidence from real executions

Every file here was produced by running the code in this repository on the
development machine (macOS 15.5, Apple Silicon, 8 cores, Python 3.12.10,
PostgreSQL 16.15). Nothing was edited by hand. Regenerate with the commands shown.

| File | Produced by | What it shows |
|---|---|---|
| `test_results.txt` | `uv run pytest --cov` | 130 tests passing + line/branch coverage per module (run from a fresh clone) |
| `demo_console.txt` | `scripts/demo.sh` | Console output of the full demo (initial load, faults, backfill, rerun) |
| `demo/00_dataset_manifest.json` | `ingest generate-data` | Source rows per phase, expected target counts, expected quarantine by code |
| `demo/01_run_initial.*` | run 1 | Initial load summary (`.json`) and structured JSON logs (`.log`) |
| `demo/02_run_incremental_with_faults.*` | run 2 | 2 x HTTP 503 and 1 x HTTP 429 retried, lookback re-read counted as duplicates |
| `demo/02_mock_stats.json` | `ingest mock-admin stats` | Server-side view of requests and injected faults |
| `demo/03_reconcile_after_incremental.*` | `ingest reconcile --dataset` | **Expected FAIL**: only the 2 orders older than the lookback window are missing |
| `demo/04_run_backfill.*` | `ingest run --since ...` | Backfill inserts exactly those 2 orders |
| `demo/05_run_rerun.*` | run 4 | Idempotency: 0 inserted / 0 updated on rerun |
| `demo/06_reconciliation.*` | `ingest reconcile --dataset` | All 14 checks PASS, incl. comparison with the generator's oracle |
| `demo/07_*` | `ingest runs / show-run / checkpoints` | Operator views of runs and checkpoints |
| `demo/08_sample_queries.txt` | `psql -f sql/sample_queries.sql` | Sample analytical / lineage queries |
| `demo/08_reconciliation_sql.txt` | `psql -f sql/reconciliation.sql` | The reconciliation checks as plain SQL |
| `benchmark_20k.json` | `ingest benchmark --orders 20000` | 116,218 records: batch vs row-by-row writes (see docs/PERFORMANCE_REPORT.md) |

Run IDs and wall-clock timestamps differ on every execution; record counts are
deterministic for a given seed and dataset size.
