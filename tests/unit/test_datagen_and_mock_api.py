import json
from collections import Counter
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from ingestion_pipeline.datagen import generate, load_expected, load_feed, write_dataset
from ingestion_pipeline.mock_api import MockSettings, create_app
from ingestion_pipeline.mock_api.app import TokenBucket

AUTH = {"Authorization": "Bearer t"}


# ------------------------------------------------------------------ generator
def test_generator_is_deterministic(tmp_path):
    write_dataset(generate(42, 120), tmp_path / "a")
    write_dataset(generate(42, 120), tmp_path / "b")
    for rel in (
        "source/orders.jsonl",
        "source/order_events.jsonl",
        "expected/labels.jsonl",
        "expected/final_state.json",
    ):
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes()
    assert generate(1, 120).orders[0].payload != generate(2, 120).orders[0].payload


def test_labels_are_kept_out_of_source_payloads(tmp_path):
    write_dataset(generate(42, 120), tmp_path)
    feed = load_feed(tmp_path / "source")
    for row in feed["orders"] + feed["order_events"]:
        if isinstance(row.payload, dict):
            assert not {"scenario", "expected_outcome", "release_phase"} & set(row.payload)
    exp = load_expected(tmp_path)
    assert exp["manifest"]["params"]["seed"] == 42
    assert all(
        lab["expected_error_code"]
        for lab in exp["labels"]
        if lab["expected_outcome"] == "quarantined"
    )


def test_dataset_contains_ties_duplicates_and_late_rows():
    ds = generate(42, 200)
    stamps = Counter(r.source_updated_at for r in ds.orders)
    assert any(n > 1 for n in stamps.values()), "timestamp ties must exist"
    scenarios = Counter(lab.scenario for lab in ds.labels)
    for s in (
        "duplicate:identical",
        "duplicate:conflicting",
        "late:new_order_beyond_lookback",
        "late:older_version",
        "invalid:unknown_order_id",
        "schema:unknown_fields",
    ):
        assert scenarios[s] >= 1, s
    assert {r.release_phase for r in ds.orders} == {1, 2}


def test_empty_and_small_datasets():
    empty = generate(42, 0)
    assert empty.orders == [] and empty.order_events == []
    with pytest.raises(ValueError):
        generate(42, 10)  # edge cases need room
    assert len(generate(42, 10, edge_cases=False).expected_orders) == 10


# ------------------------------------------------------------------ mock API
@pytest.fixture
def api():
    return TestClient(create_app(MockSettings(api_token="t", order_count=200)))


def test_mock_requires_auth(api):
    assert api.get("/v1/orders").status_code == 401
    assert api.get("/v1/orders", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert api.get("/health").status_code == 200


def test_keyset_pagination_returns_every_row_exactly_once(api):
    seen, params, pages = [], {"limit": 7}, 0
    while True:
        body = api.get("/v1/orders", params=params, headers=AUTH).json()
        pages += 1
        seen += [p["source_record_id"] if isinstance(p, dict) else p for p in body["data"]]
        if not body["has_more"]:
            break
        params = {"limit": 7, **body["next_cursor"]}
    total = api.get("/health").json()["rows"]["orders"]
    assert len(seen) == total == len(set(seen))
    assert pages == -(-total // 7)


def test_updated_since_is_inclusive(api):
    first = api.get("/v1/orders", params={"limit": 1}, headers=AUTH).json()["data"][0]
    ts = first["source_updated_at"]
    again = api.get("/v1/orders", params={"limit": 1, "updated_since": ts}, headers=AUTH).json()
    assert again["data"][0]["source_updated_at"] == ts


def test_bad_parameters(api):
    assert api.get("/v1/orders", params={"after_id": "x"}, headers=AUTH).status_code == 400
    assert api.get("/v1/orders", params={"updated_since": "nope"}, headers=AUTH).status_code == 400
    assert api.get("/v1/customers", headers=AUTH).status_code == 404


def test_release_phase_exposes_more_rows(api):
    before = api.get("/health").json()["rows"]["orders"]
    after = api.post("/_admin/release-phase", json={"phase": 2}).json()["rows"]["orders"]
    assert after > before


def test_fault_rule_budget_is_consumed(api):
    api.post("/_admin/faults", json=[{"entity": "orders", "status_code": 503, "times": 2}])
    codes = [api.get("/v1/orders", headers=AUTH).status_code for _ in range(3)]
    assert codes == [503, 503, 200]
    assert api.get("/v1/order_events", headers=AUTH).status_code == 200


def test_fault_kinds(api):
    api.post(
        "/_admin/faults",
        json=[
            {"kind": "malformed", "times": 1},
            {"kind": "drop_field", "field": "order_amount", "times": 1},
        ],
    )
    with pytest.raises(json.JSONDecodeError):
        json.loads(api.get("/v1/orders", headers=AUTH).text)
    data = api.get("/v1/orders", headers=AUTH).json()["data"]
    assert all("order_amount" not in p for p in data if isinstance(p, dict))


def test_server_side_rate_limit_returns_retry_after():
    api = TestClient(create_app(MockSettings(api_token="t", order_count=60, rate_limit_rps=1)))
    first = api.get("/v1/orders", headers=AUTH)
    second = api.get("/v1/orders", headers=AUTH)
    assert first.status_code == 200 and second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1


def test_token_bucket_refills():
    now = [0.0]
    bucket = TokenBucket(2, clock=lambda: now[0])
    assert bucket.try_acquire() == 0 and bucket.try_acquire() == 0
    assert bucket.try_acquire() == pytest.approx(0.5)
    now[0] += 0.5
    assert bucket.try_acquire() == 0


def test_request_id_header(api):
    r1 = api.get("/health").headers["X-Request-ID"]
    r2 = api.get("/health").headers["X-Request-ID"]
    assert r1 != r2 and r1.startswith("req-")


def test_cursor_timestamps_are_utc_iso(api):
    cur = api.get("/v1/orders", params={"limit": 3}, headers=AUTH).json()["next_cursor"]
    assert datetime.fromisoformat(cur["after_updated_at"]).utcoffset().total_seconds() == 0
