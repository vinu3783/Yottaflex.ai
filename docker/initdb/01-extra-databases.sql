-- Runs once when the Postgres container initialises an empty volume.
-- pipeline_test: disposable database for the integration tests (wiped by every test session)
-- pipeline_bench: disposable database for `ingest benchmark` (wiped per benchmark mode)
CREATE DATABASE pipeline_test;
CREATE DATABASE pipeline_bench;
