#!/usr/bin/env bash
# End-to-end demonstration over real HTTP. Writes evidence to reports/demo/.
#
#   1. reset the database, generate the review dataset, start the mock API (phase 1)
#   2. run 1: initial load
#   3. release phase 2 + inject faults (503 x2 on orders, 429 on events)
#   4. run 2: incremental run with lookback - retries are visible in the logs
#   5. reconcile: everything passes except the 2 "very late" orders beyond the lookback
#   6. run 3: backfill with --since recovers them
#   7. run 4: plain rerun - proves idempotency (0 inserted / 0 updated)
#   8. reconcile again: PASS; dump sample SQL queries
#
# Requirements: PIPELINE_DATABASE_URL points at a database you are happy to WIPE.
# Usage: scripts/demo.sh            (uses `ingest` from the active environment)
set -euo pipefail

cd "$(dirname "$0")/.."
OUT=reports/demo
DATASET=data/generated/review
PORT=${DEMO_PORT:-8000}
export PIPELINE_API_BASE_URL="http://127.0.0.1:${PORT}"
export PIPELINE_API_TOKEN=${PIPELINE_API_TOKEN:-demo-token}
export MOCK_API_TOKEN=$PIPELINE_API_TOKEN
export PIPELINE_LOG_FORMAT=json
INGEST=${INGEST:-ingest}

mkdir -p "$OUT"
rm -f "$OUT"/*

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

step "reset database + generate dataset"
$INGEST db-reset --yes
$INGEST generate-data --out "$DATASET" --orders 200 --seed 42 > "$OUT/00_dataset_manifest.json"

step "start mock API (phase 1)"
$INGEST mock-api --dataset-dir "$DATASET" --port "$PORT" --phase 1 &
MOCK_PID=$!
trap 'kill $MOCK_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do
  curl -sf "$PIPELINE_API_BASE_URL/health" >/dev/null && break
  sleep 0.2
done
curl -s "$PIPELINE_API_BASE_URL/health"; echo
echo "unauthenticated request -> HTTP $(curl -s -o /dev/null -w '%{http_code}' "$PIPELINE_API_BASE_URL/v1/orders")"

step "run 1: initial incremental load"
$INGEST run > "$OUT/01_run_initial.json" 2> "$OUT/01_run_initial.log"
grep -E '"status"|"records_(fetched|inserted|quarantined)"' "$OUT/01_run_initial.json"

step "release phase 2 and inject faults"
$INGEST mock-admin release-phase 2
$INGEST mock-admin set-faults '[
  {"entity": "orders", "kind": "status", "status_code": 503, "times": 2},
  {"entity": "order_events", "kind": "status", "status_code": 429, "retry_after": 1, "times": 1}]'

step "run 2: incremental with lookback (expect retries in the log)"
$INGEST run > "$OUT/02_run_incremental_with_faults.json" 2> "$OUT/02_run_incremental_with_faults.log"
grep -E 'retrying request' "$OUT/02_run_incremental_with_faults.log" || true
$INGEST mock-admin stats > "$OUT/02_mock_stats.json"

step "reconcile after incremental runs (very-late rows still missing)"
$INGEST reconcile --dataset "$DATASET" --out "$OUT/03_reconcile_after_incremental" || true

step "run 3: backfill from 2025-12-29"
$INGEST run --since 2025-12-29T00:00:00Z > "$OUT/04_run_backfill.json" 2> "$OUT/04_run_backfill.log"
grep -E '"records_inserted"' "$OUT/04_run_backfill.json"

step "run 4: rerun - idempotency check"
$INGEST run > "$OUT/05_run_rerun.json" 2> "$OUT/05_run_rerun.log"
grep -E '"records_(inserted|updated|duplicate)"' "$OUT/05_run_rerun.json"

step "final reconciliation"
$INGEST reconcile --dataset "$DATASET" --out "$OUT/06_reconciliation"

step "run list, latest run summary, checkpoints"
$INGEST runs > "$OUT/07_runs.txt"
$INGEST show-run latest > "$OUT/07_show_run_latest.json"
$INGEST checkpoints show > "$OUT/07_checkpoints.json"
cat "$OUT/07_runs.txt"

if command -v psql >/dev/null 2>&1; then
  step "sample SQL queries"
  export PGTZ=UTC
  PSQL_URL=${PIPELINE_DATABASE_URL/+psycopg/}
  psql "$PSQL_URL" -X -f sql/sample_queries.sql > "$OUT/08_sample_queries.txt"
  psql "$PSQL_URL" -X -f sql/reconciliation.sql > "$OUT/08_reconciliation_sql.txt"
  echo "wrote $OUT/08_*.txt"
fi

step "done - evidence in $OUT/"
