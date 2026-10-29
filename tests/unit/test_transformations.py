from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ingestion_pipeline.models import CURRENCIES, ORDER_STATUSES
from ingestion_pipeline.schemas import ORDER_ID_RE
from ingestion_pipeline.transformations import (
    InvalidValue,
    iso,
    normalize_amount,
    normalize_currency,
    normalize_enum,
    normalize_identifier,
    normalize_timestamp,
    record_hash,
)


def code_of(fn, *args) -> str:
    with pytest.raises(InvalidValue) as exc:
        fn(*args)
    return exc.value.code


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.50", Decimal("10.50")),
        ("0", Decimal("0.00")),
        ("0.00", Decimal("0.00")),
        (" 7.1 ", Decimal("7.10")),
        (149.5, Decimal("149.50")),  # JSON number: parsed via its shortest repr
        (19.99, Decimal("19.99")),  # would be 19.989999... if converted naively
        (12, Decimal("12.00")),
        ("9999999999.99", Decimal("9999999999.99")),
    ],
)
def test_amount_valid(raw, expected):
    assert normalize_amount(raw) == expected
    assert normalize_amount(raw).as_tuple().exponent == -2


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (None, "NULL_NOT_ALLOWED"),
        ("-0.01", "NEGATIVE_AMOUNT"),
        ("abc", "INVALID_AMOUNT"),
        ("12.3.4", "INVALID_AMOUNT"),
        ("NaN", "INVALID_AMOUNT"),
        ("Infinity", "INVALID_AMOUNT"),
        ("10.123", "INVALID_AMOUNT_PRECISION"),
        ("10000000000.00", "AMOUNT_OUT_OF_RANGE"),
        (True, "INVALID_TYPE"),  # bool is an int subclass - must not become 1.00
        ({"value": 1}, "INVALID_TYPE"),
        ([1], "INVALID_TYPE"),
    ],
)
def test_amount_invalid_is_never_silently_repaired(raw, code):
    assert code_of(normalize_amount, raw) == code


def test_timestamp_offset_is_normalised_to_utc():
    ts = normalize_timestamp("2026-01-01T10:00:00+05:30")
    assert ts == datetime(2026, 1, 1, 4, 30, tzinfo=UTC)
    assert ts.utcoffset().total_seconds() == 0
    assert normalize_timestamp("2026-01-01T04:30:00Z") == ts


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("2026-01-01T00:00:00", "INVALID_TIMESTAMP"),  # naive -> ambiguous
        ("31/01/2026 10:00", "INVALID_TIMESTAMP"),
        ("", "INVALID_TIMESTAMP"),
        (1735689600, "INVALID_TYPE"),
        (None, "NULL_NOT_ALLOWED"),
    ],
)
def test_timestamp_invalid(raw, code):
    assert code_of(normalize_timestamp, raw) == code


def test_enum_is_case_and_whitespace_insensitive():
    assert normalize_enum("  SHIPPED ", ORDER_STATUSES) == "shipped"
    assert code_of(normalize_enum, "teleported", ORDER_STATUSES) == "INVALID_ENUM"
    assert code_of(normalize_enum, 3, ORDER_STATUSES) == "INVALID_TYPE"


def test_currency():
    assert normalize_currency(" eur", CURRENCIES) == "EUR"
    assert code_of(normalize_currency, "XYZ", CURRENCIES) == "INVALID_CURRENCY"
    assert code_of(normalize_currency, "US", CURRENCIES) == "INVALID_CURRENCY"


def test_identifier_trimmed_but_format_enforced():
    assert normalize_identifier(" ORD-00000001 ", ORDER_ID_RE) == "ORD-00000001"
    assert code_of(normalize_identifier, "order#1", ORDER_ID_RE) == "INVALID_IDENTIFIER"
    assert code_of(normalize_identifier, 1, ORDER_ID_RE) == "INVALID_TYPE"


def test_record_hash_is_order_independent_and_type_canonical():
    a = record_hash({"x": Decimal("1.50"), "t": datetime(2026, 1, 1, tzinfo=UTC)})
    b = record_hash({"t": datetime(2026, 1, 1, tzinfo=UTC), "x": Decimal("1.50")})
    assert a == b
    assert a != record_hash({"x": Decimal("1.51"), "t": datetime(2026, 1, 1, tzinfo=UTC)})


def test_iso_formatting():
    assert iso(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-01-01T00:00:00Z"
    assert iso(datetime(2026, 1, 1, 0, 0, 0, 5, tzinfo=UTC)) == "2026-01-01T00:00:00.000005Z"
