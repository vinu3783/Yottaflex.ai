# Performance Report

All numbers below were **measured**; the raw output is
[reports/benchmark_20k.json](../reports/benchmark_20k.json). Anything not
measured is marked **NOT MEASURED**.

## Method

`ingest benchmark` ([src/ingestion_pipeline/benchmark.py](../src/ingestion_pipeline/benchmark.py)):

1. Generate (or reuse) a deterministic dataset (`--orders 20000 --seed 7`, edge
   cases included, all rows in one release phase).
2. Start the mock API **as a separate process** serving that dataset over real
   HTTP on localhost, with no rate limit and no faults.
3. For each write mode: drop and re-migrate the dedicated benchmark database,
   then run `ingest run --json` **as a separate process**, so peak memory
   (`ru_maxrss`) belongs to the pipeline alone.
4. Duration = the run's own monotonic-clock duration (`time.monotonic()`),
   excluding process start-up; the process wall time is reported too.
5. After each run, count rows written and run the full reconciliation
   **including the oracle comparison**, so a fast but wrong run would show
   `FAIL`.

Command:

```bash
createdb -O pipeline pipeline_bench
ingest benchmark --orders 20000 --seed 7 --page-size 1000 --modes batch row \
  --database-url postgresql+psycopg://pipeline:<pw>@localhost:5432/pipeline_bench \
  --out reports/benchmark_20k
```

## Environment

| Item | Value |
|---|---|
| Machine | Apple Silicon laptop, macOS 15.5 (arm64), 8 CPU cores, 8 GB RAM |
| Python | 3.12.10 |
| PostgreSQL | 16.15 (Homebrew), default settings, same machine |
| Topology | mock API, pipeline and PostgreSQL all local, so network latency is ~0 |
| Throttling | disabled (`PIPELINE_REQUESTS_PER_SECOND=0`) |
| Page size | 1,000 |

## Workload

| | Rows |
|---|---|
| Orders generated | 20,000 (+ 500 edge-case orders) |
| Order source rows (versions, duplicates, invalid) | 58,959 |
| Order-event source rows | 57,259 |
| **Total source records processed** | **116,218** |
| HTTP requests | 117 (117 pages; 0 retries) |

## Results

| Metric | batch (default) | row |
|---|---|---|
| Duration (monotonic, in-run) | **25.217 s** | 79.143 s |
| Process wall time | 25.535 s | 79.532 s |
| Throughput | **4,608.7 records/s** | 1,468.5 records/s |
| Page transactions | 117 | 117 |
| Rows written: `orders` / `order_events` | 20,500 / 56,759 | 20,500 / 56,759 |
| Rows written: `record_outcomes` / `quarantine` / `raw_responses` | 116,218 / 2,100 / 117 | same |
| Peak RSS of the pipeline process | 112.2 MB | 70.4 MB |
| Reconciliation (incl. oracle) | **PASS** | **PASS** |

Throughput calculation: 116,218 records ÷ 25.217 s = 4,608.7 records/s;
116,218 ÷ 79.143 s = 1,468.5 records/s. Batch is **3.14× faster**.

## The optimisation: batched upserts

**Row mode** does, for every record: one `SELECT … FOR UPDATE` + one
`INSERT … ON CONFLICT`. For 116k records that is about 230k statements, each a
client-server round-trip with its own parse/plan/execute.

**Batch mode** (default) does, per page: one `SELECT … WHERE key IN (…) FOR UPDATE`,
classification in Python, then one multi-row `INSERT … VALUES (…), (…) … ON CONFLICT`
with the final state per key. About 4 statements per page instead of ~2,000.

The identical outcome counts and the PASS in both modes show the
optimisation changed speed, not results.

**Cost:** batch mode builds a page of rows in memory, so peak RSS is higher (112 MB vs 70 MB).

## What the numbers do and do not show

* They show relative cost on one laptop with everything local. A real API adds
  network latency per request and usually a rate limit, and then throughput is
  bounded by `requests_per_second × page_size` (e.g. 20 req/s × 500 =
  10,000 records/s at best), not by the database.
* Per-record overhead includes writing the audit ledger and quarantine; that
  is a deliberate correctness cost.
* **NOT MEASURED:** the 100,000-order workload (~580k records). The run
  started, but the development machine's system disk was nearly full
  (≈ 0.5 GB free) and PostgreSQL stopped with *No space left on device* while
  writing WAL. To avoid destabilising the machine the run was not repeated.
  Reproduce elsewhere with ≥ 3 GB free:
  `ingest benchmark --orders 100000 --modes batch --database-url …/pipeline_bench`.
* **NOT MEASURED:** concurrency, `COPY`-based loading, and performance under
  throttling.

## Further optimisations (not yet measured)

| Idea | Expected effect | Risk |
|---|---|---|
| `COPY` page into an unlogged staging table + one set-based upsert | Fewer bytes parsed, faster for large pages | More code; staging cleanup |
| Raw bodies to object storage | Smaller WAL and DB, faster commits | Two-store consistency (hash + URI) |
| Parallel entities / time partitions | Near-linear with partitions when not rate-limited | Per-partition checkpoints, shared rate limit |
| Drop ledger rows for `duplicate` outcomes | Fewer writes on heavy replays | Weaker audit |
| Tune `synchronous_commit`, larger `max_wal_size` | Faster commits | Durability trade-off |
