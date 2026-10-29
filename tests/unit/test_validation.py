import pytest

from ingestion_pipeline.errors import BreakingSchemaChangeError
from ingestion_pipeline.validation import check_breaking_change, validate_record

ORDER = {
    "order_id": "ORD-00000001",
    "customer_id": "CUST-000001",
    "order_status": "paid",
    "order_amount": "25.00",
    "currency": "USD",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:05:00Z",
    "source_updated_at": "2026-01-01T00:05:01Z",
    "source_record_id": "ORDV-000000001",
}
EVENT = {
    "event_id": "EVT-0000000001",
    "order_id": "ORD-00000001",
    "event_type": "order_created",
    "event_timestamp": "2026-01-01T00:00:00Z",
    "source_updated_at": "2026-01-01T00:00:02Z",
    "source_record_id": "EVTV-000000001",
}


def codes(entity, payload):
    return [i.code for i in validate_record(entity, 0, payload).issues]


def test_valid_order_is_normalised():
    res = validate_record("orders", 3, dict(ORDER, order_status=" Paid "))
    assert res.is_valid and res.issues == []
    assert res.record.order_status == "paid"
    assert res.business_key("orders") == "ORD-00000001"
    assert res.source_record_id == "ORDV-000000001"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("order_status", "teleported", "INVALID_ENUM"),
        ("order_status", None, "NULL_NOT_ALLOWED"),
        ("order_amount", "-1.00", "NEGATIVE_AMOUNT"),
        ("currency", "XYZ", "INVALID_CURRENCY"),
        ("created_at", "yesterday", "INVALID_TIMESTAMP"),
        ("updated_at", "2025-12-31T00:00:00Z", "TIMESTAMP_ORDER"),
        ("customer_id", 12345, "INVALID_TYPE"),
    ],
)
def test_order_rule_violations_have_stable_codes(field, value, code):
    assert codes("orders", dict(ORDER, **{field: value})) == [code]


@pytest.mark.parametrize("field", list(ORDER))
def test_every_required_order_field_is_enforced(field):
    payload = {k: v for k, v in ORDER.items() if k != field}
    res = validate_record("orders", 0, payload)
    assert not res.is_valid
    assert [(i.code, i.field) for i in res.issues] == [("MISSING_REQUIRED_FIELD", field)]


def test_missing_primary_key_is_traceable_by_source_record_id():
    payload = {k: v for k, v in ORDER.items() if k != "order_id"}
    res = validate_record("orders", 0, payload)
    assert res.business_key("orders") is None
    assert res.source_record_id == "ORDV-000000001"


def test_multiple_problems_are_all_reported():
    found = set(codes("orders", dict(ORDER, order_amount="-5", currency="ZZZ")))
    assert found == {"NEGATIVE_AMOUNT", "INVALID_CURRENCY"}


def test_non_object_record():
    assert codes("orders", "corrupted") == ["NOT_AN_OBJECT"]
    assert codes("orders", None) == ["NOT_AN_OBJECT"]
    assert codes("orders", [ORDER]) == ["NOT_AN_OBJECT"]


def test_unknown_fields_are_kept_and_reported_not_rejected():
    res = validate_record("orders", 0, dict(ORDER, loyalty_tier="gold", channel="web"))
    assert res.is_valid
    assert res.record.unknown_fields == ["channel", "loyalty_tier"]
    # unknown fields do not change the business content hash
    assert res.record.content_hash() == validate_record("orders", 0, ORDER).record.content_hash()


def test_event_validation():
    assert validate_record("order_events", 0, EVENT).is_valid
    assert codes("order_events", dict(EVENT, event_type="order_teleported")) == ["INVALID_ENUM"]
    assert codes("order_events", dict(EVENT, order_id="nope")) == ["INVALID_IDENTIFIER"]


def test_content_hash_ignores_version_metadata():
    a = validate_record("orders", 0, ORDER).record
    b = validate_record(
        "orders",
        0,
        dict(ORDER, source_record_id="ORDV-9", source_updated_at="2027-01-01T00:00:00Z"),
    ).record
    c = validate_record("orders", 0, dict(ORDER, order_amount="25.01")).record
    assert a.content_hash() == b.content_hash() != c.content_hash()


# ------------------------------------------------------------------ breaking changes
def page_without(field, n, missing):
    return [{k: v for k, v in ORDER.items() if not (k == field and i < missing)} for i in range(n)]


def test_breaking_change_when_field_vanishes_from_page():
    with pytest.raises(BreakingSchemaChangeError, match="order_amount"):
        check_breaking_change(
            "orders", page_without("order_amount", 10, 10), min_records=5, missing_ratio=0.9
        )


def test_isolated_missing_fields_are_record_level_not_breaking():
    check_breaking_change(
        "orders", page_without("order_amount", 10, 2), min_records=5, missing_ratio=0.9
    )


def test_small_pages_never_trigger_breaking_change():
    check_breaking_change(
        "orders", page_without("order_amount", 3, 3), min_records=5, missing_ratio=0.9
    )
