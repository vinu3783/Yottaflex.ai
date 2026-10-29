# Final Acceptance Report

Status legend: **PASS** = implemented and verified by an actual execution
(evidence linked) · **FAIL** = not met · **NOT VERIFIED** = implemented but
could not be executed in the development environment.

Environment of the final review: fresh `git clone` of this repository,
`uv sync --frozen` into a new virtual environment, PostgreSQL 16.15 local,
macOS 15.5 arm64, Python 3.12.10.

## 1. Final review checks (Milestone 7)

| Check | Result | Evidence |
|---|---|---|
| Dependencies install from lock file in a fresh clone | **PASS** | `uv sync --frozen` succeeded in a fresh clone |
| Lint / format | **PASS** | `ruff check .` → "All checks passed!"; `ruff format --check .` → 55 files formatted |
| Migrations apply to an empty database | **PASS** | `ingest db-reset --yes` / test session setup; `test_migration_matches_models` |
| Mock API starts, health 200, unauthenticated 401 | **PASS** | [reports/demo_console.txt](../reports/demo_console.txt) |
| Ingestion end to end over real HTTP | **PASS** | [reports/demo/](../reports/demo/) |
| Reruns create no duplicates | **PASS** | [05_run_rerun.json](../reports/demo/05_run_rerun.json): 0 inserted / 0 updated |
| Full test suite | **PASS** | 130 passed, fresh clone ([test_results.txt](../reports/test_results.txt)); also 130/130 twice in a row in the working copy |
| Reporting / reconciliation | **PASS** | [06_reconciliation.md](../reports/demo/06_reconciliation.md): 14/14 checks PASS |
| Benchmark executes | **PASS** | [benchmark_20k.json](../reports/benchmark_20k.json); re-executed (2,000 orders) from the fresh clone: batch 5,645 rec/s, row 1,526 rec/s, both reconciled |
| Demo reproducible from fresh clone | **PASS** | record counts of all four demo runs identical to the committed evidence |
| Docker image build / Compose startup | **NOT VERIFIED** | Docker not installed on the development machine |
| Git: no secrets, no `.env`, no generated data, no AppleDouble files tracked | **PASS** | `git grep` scan for tokens/keys/paths; `.gitignore`; 89 tracked files |

## 2. Requirements checklist

### Ingestion behaviour

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Paginated ingestion | PASS | `test_full_multi_page_load_matches_oracle` (> 5 pages), `test_keyset_pagination_returns_every_row_exactly_once` |
| 2 | Configurable auth, no committed credentials | PASS | `config.py` (`SecretStr`, header/scheme configurable), `.env.example` placeholders, `test_invalid_credentials` |
| 3 | Rate-limit handling, backoff + jitter | PASS | `test_429_with_retry_after`, `test_backoff_is_exponential_bounded_and_jittered`, `test_server_side_rate_limit_returns_retry_after` |
| 4 | 429 + transient 5xx handling | PASS | `test_transient_503_then_recovery`, `test_transient_5xx_then_recovery`, demo run 2 |
| 5 | Timeouts and bounded retries | PASS | `test_real_timeout_is_retried` (real HTTP), `test_exhausted_retries_raise_with_last_error`, `test_five_consecutive_429s_are_bounded` |
| 6 | Incremental checkpoints + restart recovery | PASS | `test_exhausted_retries_fail_run_and_restart_resumes`, `test_crash_after_*` |
| 7 | Raw response retention | PASS | `test_raw_response_retained_verbatim_and_without_secrets`, `raw_responses` table |
| 8 | Schema validation + deterministic transforms | PASS | `tests/unit/test_validation.py`, `test_transformations.py` |
| 9 | Duplicate detection + idempotent publishing | PASS | `test_versions_duplicates_and_conflicts_in_one_page[batch,row]`, `test_rerunning_same_input_creates_no_duplicates` |
| 10 | Explicit quarantine | PASS | `test_quarantine_traces_back_to_exact_raw_response`, oracle quarantine checks |
| 11 | Run metadata, logs, metrics, failure reporting | PASS | `test_run_metadata_is_complete`, JSON logs in `reports/demo/*.log`, `ingest show-run` |
| 12 | Local mock API, deterministic | PASS | `mock_api/`, `test_generator_is_deterministic`, identical counts from fresh clone |
| 13 | Starter dataset + configurable large dataset | PASS | `generate-data --orders N`; 200-order review set; 20,000-order (116,218 records) benchmark set |
| 14 | 100,000+ record workload | PASS | 116,218 source records processed in the benchmark |
| 14b | 100,000-*order* (~580k records) workload | NOT VERIFIED | run aborted: development disk full (see Limitations) |
| 15 | Automated tests, reconciliation queries, performance measurement | PASS | 130 tests; `sql/`; `PERFORMANCE_REPORT.md` |
| 16 | Public GitHub-ready repository | PASS | see §4 |

### Mock API simulation (spec §4)

| Behaviour | Status | How |
|---|---|---|
| Pagination, configurable page and dataset size | PASS | `limit`, `MOCK_MAX_PAGE_SIZE`, `MOCK_ORDER_COUNT` / dataset dir |
| Authentication | PASS | bearer token, 401 |
| Rate limiting / 429 | PASS | token bucket (`--rate-limit`) + `status` fault with `retry_after` |
| Transient 500/503 | PASS | `status` fault |
| Timeouts / interruption | PASS | `delay` fault (`test_real_timeout_is_retried`) |
| Empty pages / empty dataset | PASS | `test_empty_source`; `stuck_cursor` empty page |
| Duplicates, malformed payloads, missing/unexpected fields | PASS | generator scenarios + `malformed`, `invalid_envelope`, `drop_field` faults |
| Late / out-of-order records | PASS | release phases; `late:*` scenarios |
| Invalid reference keys | PASS | `invalid:unknown_order_id` |
| Deterministic mode | PASS | seeded generator; faults by page/count |

Which scenarios are data vs transport: invalid values, duplicates, conflicts,
unknown fields, unknown order ids, late and out-of-order rows are **data
records** produced by the generator. 429/5xx, timeouts, malformed bodies,
missing envelope fields, a field dropped from a whole page and a stuck cursor
are **transport/HTTP faults** injected with `mock-admin set-faults`.

### Required test list (spec §13)

| # | Scenario | Test(s) | Status |
|---|---|---|---|
| 1 | Multi-page ingestion | `test_full_multi_page_load_matches_oracle` | PASS |
| 2 | Empty source | `test_empty_source` | PASS |
| 3 | Malformed HTTP response | `test_malformed_response_retried_then_fails_clearly`, `test_malformed_json_is_archived_and_retried` | PASS |
| 4 | Missing required fields | `test_every_required_order_field_is_enforced` | PASS |
| 5 | Null values | `test_order_rule_violations_have_stable_codes[order_status-None-…]`, amount/timestamp null cases | PASS |
| 6 | Duplicate records | `test_versions_duplicates_and_conflicts_in_one_page`, `test_replaying_same_page_is_idempotent` | PASS |
| 7 | Conflicting duplicates | same + `test_events_out_of_order_unknown_order_and_conflicts` | PASS |
| 8 | Invalid foreign keys | `test_events_out_of_order_unknown_order_and_conflicts`, `test_database_constraints_back_up_application_rules` | PASS |
| 9 | Unexpected schema changes | `test_unknown_fields_are_kept_and_reported_not_rejected`, `test_breaking_schema_change_stops_before_publishing` | PASS |
| 10 | 429 with Retry-After | `test_429_with_retry_after`, `test_429_honours_retry_after_then_recovers` | PASS |
| 11 | Transient 5xx then recovery | `test_transient_503_then_recovery` | PASS |
| 12 | Exhausted retries | `test_exhausted_retries_fail_run_and_restart_resumes` | PASS |
| 13 | Invalid authentication | `test_invalid_credentials`, `test_invalid_credentials_fail_fast_without_retry` | PASS |
| 14 | Timeout / interruption | `test_real_timeout_is_retried`, `test_timeout_is_retried_then_recovers` | PASS |
| 15 | Checkpoint persistence | `test_exhausted_retries_…` (asserts checkpoint row), `test_page_budget_stops_early_and_next_run_continues` | PASS |
| 16 | Restart after partial failure | `test_crash_after_publish_before_commit_loses_nothing`, `test_crash_after_commit_resumes_from_checkpoint` | PASS |
| 17 | Rerun without duplicates | `test_rerunning_same_input_creates_no_duplicates` | PASS |
| 18 | Late / out-of-order updates | `test_incremental_phases_lookback_and_backfill`, out-of-order events test | PASS |
| 19 | Older version does not overwrite | `test_older_version_never_overwrites_newer`, `test_sql_guard_blocks_older_version_even_without_python_check` | PASS |
| 20 | Raw-response retention | `test_raw_response_retained_verbatim_and_without_secrets` | PASS |
| 21 | Quarantine traceability | `test_quarantine_traces_back_to_exact_raw_response` | PASS |
| 22 | Transactions + uniqueness | `test_failed_publish_rolls_back_whole_page`, `test_database_constraints_back_up_application_rules` | PASS |
| 23 | Reconciliation correctness | `test_reconciliation_detects_silent_data_loss`, `test_reconciliation_detects_counter_drift` | PASS |
| - | Repeatability | suite run twice; truncation per test | PASS |
| - | Coverage measured and explained | 90% ([test_results.txt](../reports/test_results.txt)); README §8 | PASS |

### Documentation (spec §17)

| Document | Status |
|---|---|
| README (all listed sections) | PASS |
| ARCHITECTURE (components, flows, transactions, failure propagation, tracing, Mermaid architecture + sequence diagrams, 5-question review per component) | PASS |
| DATA_DICTIONARY (every table/column, grain, keys, rules, count semantics, tracing) | PASS |
| DESIGN_DECISIONS (17 ADRs, full template) | PASS |
| INTERVIEW_PREPARATION (22 concepts × 8 parts) | PASS |
| TECHNICAL_WALKTHROUGH (10 sections with say/show/run/point out/trade-off/questions) | PASS |
| QUESTIONS_AND_ANSWERS (60 questions, 12 groups, all required scenarios) | PASS |
| FAILURE_RECOVERY + OPERATIONS_RUNBOOK (automatic vs manual, exact commands and SQL) | PASS |
| PERFORMANCE_REPORT (measured only; unmeasured items marked) | PASS |
| GLOSSARY (all requested terms) | PASS |
| Prompt history + AI disclosure | PASS (Part B of [AI_USAGE.md](AI_USAGE.md) must be completed by the candidate) |

Note on layout: the suggested `scripts/generate_data.py`, `run_pipeline.py`,
`benchmark.py`, `verify_results.py` are provided as CLI sub-commands
(`ingest generate-data`, `run`, `benchmark`, `reconcile`) to avoid duplicate
entry points; `scripts/demo.sh` chains them. `docs/diagrams/` is not a
separate folder: diagrams are inline Mermaid in the documents.

## 3. Deviations and assumptions

* **Orders store latest state only**; history is kept in `raw_responses` + `record_outcomes` (documented, ADR-9).
* **Conflicting duplicates** (same key and version, different content) are quarantined rather than resolved (spec: "do not resolve by arbitrary row order").
* **No customer entity**: `customer_id` is validated for format only (no reference table in scope).
* **Staging** is the validated in-memory page batch inside the page transaction, not a staging table.
* **Concurrency** is not implemented (`max concurrent requests` = 1); keyset pagination is sequential (ADR-1).
* **`--until`** is page-granular (documented).

## 4. GitHub publication

Repository: <https://github.com/vinu3783/Yottaflex.ai> (public, initially empty).
Commits are authored as **Vinayaka G C &lt;vinayakagc210@gmail.com&gt;** with no
co-author trailer, as instructed. Push status is recorded in the commit that
follows this report (see `git log`).

If a push ever needs to be repeated manually:

```bash
cd Yottaflex.ai
git remote -v                     # origin https://github.com/vinu3783/Yottaflex.ai.git
git push -u origin main
```

## Remaining limitations and next steps

| Limitation | Next step |
|---|---|
| Docker/Compose not executed | Run `docker compose up -d db mock-api && docker compose run --rm pipeline pytest` on a machine with Docker; add a CI workflow with a Postgres service |
| 100,000-order benchmark not measured | Run `ingest benchmark --orders 100000 --modes batch` with ≥ 3 GB free disk |
| Sequential, single runner | Partition by entity/time with per-partition checkpoints and a shared rate limiter |
| Raw bodies in PostgreSQL, no retention job | Object storage (URI + SHA-256 in DB) and a retention policy |
| Orders latest-state only | Optional SCD-2 `order_versions` table if history queries are needed |
| Late data beyond lookback needs manual backfill | Periodic wide backfill job; alert when reconciliation against source counts drifts |
| No token refresh (static bearer token) | OAuth2 client-credentials provider with refresh-on-401-once |
| Mock admin endpoints unauthenticated | Keep local-only; disable via `MOCK_ENABLE_ADMIN=false` |
| Tested against the mock only | Contract tests against the real API's sandbox, if one exists |
