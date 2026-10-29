# Glossary

Plain-English definitions, with where each idea appears in this project.

| Term | Meaning | In this project |
|---|---|---|
| **API** | A defined way for programs to request data or actions from another system. | The orders API (`/v1/orders`, `/v1/order_events`). |
| **Endpoint** | One URL of an API that returns a specific resource. | `GET /v1/orders` |
| **Pagination** | Splitting a large result into pages requested one after another. | `limit` + cursor parameters. |
| **Cursor** | A marker meaning "continue after this item". | `next_cursor = {after_updated_at, after_id}` |
| **Keyset pagination** | Pagination using the last row's sort key as the cursor, instead of an offset. | Sort key `(source_updated_at, source_record_id)`. |
| **Watermark** | The point up to which data is known to be processed. | The checkpoint position. |
| **Checkpoint** | A saved watermark used to resume after a stop or crash. | `checkpoints` table, advanced per committed page. |
| **Lookback window** | Re-reading a period before the checkpoint to catch late data. | `PIPELINE_LOOKBACK_SECONDS` (default 1 h). |
| **Incremental load** | Fetching only what changed since last time. | Default `ingest run`. |
| **Backfill** | Re-loading a historical range on purpose. | `ingest run --since … [--until …]` |
| **Idempotency** | Doing something twice has the same effect as doing it once. | Replaying the feed leaves the tables unchanged. |
| **Upsert** | Insert a row, or update it if the key already exists. | `INSERT … ON CONFLICT … DO UPDATE` |
| **Transaction** | A group of database changes that all succeed or all fail together. | One per page. |
| **ACID** | Atomic, Consistent, Isolated, Durable: transaction guarantees. | Why data + checkpoint commit together. |
| **Primary key** | Column(s) uniquely identifying a row. | `orders.order_id`, `order_events.event_id` |
| **Business key** | An identifier meaningful to the business. | `order_id`, `event_id` |
| **Surrogate key** | A system-generated identifier with no business meaning. | `raw_response_id`, `quarantine_id`, `run_id` |
| **Foreign key** | A column that must match a key in another table. | `order_events.order_id → orders.order_id` |
| **Referential integrity** | Every reference points at something that exists. | No event without its order (checked + enforced). |
| **Constraint** | A rule enforced by the database. | `order_amount >= 0`, allowed statuses. |
| **Index** | A structure that makes lookups fast (at some write cost). | `ix_order_events_order_id` |
| **JSONB** | PostgreSQL's binary JSON type: stored efficiently and queryable. | `raw_responses.body`, `quarantine.payload` |
| **Raw archive** | Unmodified copies of received data. | `raw_responses` |
| **Staging** | An intermediate holding area before final tables. | The in-memory validated batch of a page (no staging table). |
| **Quarantine** | Where invalid records are kept with reasons instead of being loaded or deleted. | `quarantine` table |
| **Ledger / lineage** | A record of where each item came from and what happened to it. | `record_outcomes` |
| **Schema** | The expected structure (fields, types) of data. | `OrderRecord`, `OrderEventRecord` |
| **Schema drift** | The structure of incoming data changes over time. | Unknown fields counted; missing required fields detected. |
| **Additive change** | A schema change that only adds something (usually safe). | New `loyalty_tier` field. |
| **Breaking change** | A schema change that removes/renames something consumers need. | `order_amount` missing on a whole page. |
| **Validation** | Checking data against rules. | `validation.validate_record` |
| **Normalisation (data)** | Converting values to one standard form. | `" SHIPPED "` → `shipped`, offsets → UTC. |
| **Normalisation (database)** | Storing each fact once and linking with keys. | orders vs order_events tables. |
| **Retry** | Trying a failed request again. | Up to `PIPELINE_MAX_ATTEMPTS`. |
| **Exponential backoff** | Waiting longer after each failure (doubling). | 0.5, 1, 2, 4 … s ceilings. |
| **Jitter** | Random variation in waits so clients don't retry together. | Full jitter: `uniform(0, ceiling)`. |
| **Rate limit** | A cap on requests per time unit set by the server. | 429 + `Retry-After`. |
| **Throttling** | Limiting our own request rate. | `PIPELINE_REQUESTS_PER_SECOND` |
| **HTTP 429** | "Too Many Requests". | Retried after `Retry-After`. |
| **Timeout** | Maximum time to wait before giving up on a request. | `PIPELINE_REQUEST_TIMEOUT_SECONDS` |
| **Transient error** | A failure likely to go away by itself. | 5xx, timeouts, 429. |
| **Permanent error** | A failure that retrying cannot fix. | 401, 403, 400. |
| **Throughput** | Work done per unit of time. | Records per second in the benchmark. |
| **Latency** | Time taken for one operation. | `raw_responses.elapsed_ms` |
| **Batching** | Grouping many small operations into one. | One upsert per page. |
| **Observability** | Being able to understand a system's behaviour from its outputs. | Run tables, ledger, JSON logs. |
| **Structured logging** | Logs as key/value records (e.g. JSON). | `{"event": "retrying request", "run_id": …}` |
| **Metrics** | Numbers describing behaviour (counts, rates, durations). | `run_entity_stats`, HTTP counters. |
| **Reconciliation** | Proving counts and contents match between stages. | `ingest reconcile` |
| **Oracle** | Independently known expected results used to check output. | Generator's `expected/` files. |
| **Event time** | When something actually happened. | `event_timestamp` |
| **Processing time** | When the system processed it. | `ingested_at` |
| **Late-arriving data** | Records that show up after later-timestamped records. | Phase-2 rows older than the watermark. |
| **Out-of-order data** | Records not delivered in event-time order. | Events with earlier `event_timestamp` delivered later. |
| **At-most-once** | Never duplicated, may be lost. | Not used. |
| **At-least-once delivery** | Never lost, may be repeated. | Our extraction guarantee. |
| **Exactly-once** | Neither lost nor repeated; hard across systems. | Not claimed; we give effectively-once results. |
| **Effectively-once** | At-least-once delivery + idempotent processing. | Our target-table guarantee. |
| **Advisory lock** | An application-defined lock managed by PostgreSQL. | Ensures one run at a time. |
| **Migration** | A versioned change to the database schema. | Alembic `0001_initial_schema` |
| **Mock API** | A fake implementation of an API for testing. | `mock_api/` (FastAPI). |
| **Fault injection** | Causing failures on purpose to test recovery. | `ingest mock-admin set-faults` |
| **Deterministic** | Same inputs always give the same outputs. | Generator with fixed seed. |
| **Seed** | Starting value for a random generator to make it repeatable. | `--seed 42` |
| **Peak RSS** | The most memory a process held at once. | Benchmark `peak_rss_mb`. |
| **WAL** | PostgreSQL's write-ahead log, written before data files for durability. | Grew large during big benchmarks. |
