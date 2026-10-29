"""Deterministic field normalisers.

Each function takes a raw JSON value and either returns the normalised value
or raises :class:`InvalidValue` with a stable error code. They never "repair"
data by guessing (an unparsable amount is rejected, never turned into 0).

These functions are pure and independent of HTTP, Pydantic and the database,
which keeps them trivially unit-testable.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

MONEY_QUANTUM = Decimal("0.01")
MAX_AMOUNT = Decimal("9999999999.99")  # fits NUMERIC(12, 2)


class InvalidValue(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _require(value: Any) -> Any:
    if value is None:
        raise InvalidValue("NULL_NOT_ALLOWED", "value must not be null")
    return value


def normalize_identifier(value: Any, pattern: re.Pattern[str]) -> str:
    """Trim surrounding whitespace and check the identifier format.
    Case is preserved: identifiers are opaque."""
    _require(value)
    if not isinstance(value, str):
        raise InvalidValue(
            "INVALID_TYPE", f"expected string identifier, got {type(value).__name__}"
        )
    cleaned = value.strip()
    if not pattern.fullmatch(cleaned):
        raise InvalidValue("INVALID_IDENTIFIER", f"identifier {cleaned!r} does not match format")
    return cleaned


def normalize_enum(value: Any, allowed: tuple[str, ...]) -> str:
    """Case/whitespace-insensitive match: '  Shipped ' -> 'shipped'."""
    _require(value)
    if not isinstance(value, str):
        raise InvalidValue("INVALID_TYPE", f"expected string, got {type(value).__name__}")
    cleaned = re.sub(r"[\s-]+", "_", value.strip().lower())
    if cleaned not in allowed:
        raise InvalidValue("INVALID_ENUM", f"{value!r} is not one of {', '.join(allowed)}")
    return cleaned


def normalize_currency(value: Any, allowed: tuple[str, ...]) -> str:
    _require(value)
    if not isinstance(value, str):
        raise InvalidValue("INVALID_TYPE", f"expected string, got {type(value).__name__}")
    cleaned = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", cleaned) or cleaned not in allowed:
        raise InvalidValue("INVALID_CURRENCY", f"{value!r} is not a supported ISO-4217 code")
    return cleaned


def normalize_amount(value: Any) -> Decimal:
    """Money is parsed as Decimal (never float) and must have at most 2 decimals."""
    _require(value)
    if isinstance(value, bool) or not isinstance(value, str | int | float | Decimal):
        raise InvalidValue("INVALID_TYPE", f"expected decimal amount, got {type(value).__name__}")
    try:
        # str() keeps a float's shortest repr (19.99 -> "19.99") instead of its binary value.
        amount = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except InvalidOperation:
        raise InvalidValue("INVALID_AMOUNT", f"{value!r} is not a decimal number") from None
    if not amount.is_finite():
        raise InvalidValue("INVALID_AMOUNT", f"{value!r} is not a finite number")
    if amount < 0:
        raise InvalidValue("NEGATIVE_AMOUNT", f"amount {amount} is negative")
    if amount != amount.quantize(MONEY_QUANTUM):
        raise InvalidValue("INVALID_AMOUNT_PRECISION", f"amount {amount} has more than 2 decimals")
    if amount > MAX_AMOUNT:
        raise InvalidValue("AMOUNT_OUT_OF_RANGE", f"amount {amount} exceeds {MAX_AMOUNT}")
    return amount.quantize(MONEY_QUANTUM)


def normalize_timestamp(value: Any) -> datetime:
    """Parse ISO-8601 and convert to UTC. Naive timestamps are rejected because
    their time zone is ambiguous."""
    _require(value)
    if not isinstance(value, str):
        raise InvalidValue("INVALID_TYPE", f"expected ISO-8601 string, got {type(value).__name__}")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise InvalidValue("INVALID_TIMESTAMP", f"{value!r} is not ISO-8601") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidValue("INVALID_TIMESTAMP", f"{value!r} has no time zone offset")
    return parsed.astimezone(UTC)


def iso(ts: datetime) -> str:
    """Canonical UTC timestamp string used in API parameters and reports.
    Microseconds are kept only when present."""
    ts = ts.astimezone(UTC)
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ" if ts.microsecond else "%Y-%m-%dT%H:%M:%SZ"
    return ts.strftime(fmt)


def _canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def record_hash(fields: dict[str, Any]) -> str:
    """Stable SHA-256 of normalised business fields (key order independent).
    Used to tell an identical re-delivery from a conflicting one."""
    canonical = {k: _canonical(v) for k, v in sorted(fields.items())}
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()
