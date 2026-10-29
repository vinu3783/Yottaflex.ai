"""Pydantic schemas: the API page envelope and the validated record models.

Record models use ``mode="before"`` validators that delegate to the pure
normalisers in :mod:`transformations`, translating :class:`InvalidValue` into
Pydantic errors whose ``type`` is our stable error code (e.g. ``NEGATIVE_AMOUNT``).
``extra="allow"`` keeps unknown fields so schema drift can be detected and
reported instead of silently dropped.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic_core import PydanticCustomError

from .models import CURRENCIES, EVENT_TYPES, ORDER_STATUSES
from .transformations import (
    InvalidValue,
    normalize_amount,
    normalize_currency,
    normalize_enum,
    normalize_identifier,
    normalize_timestamp,
    record_hash,
)

ORDER_ID_RE = re.compile(r"ORD-[0-9]{6,12}")
CUSTOMER_ID_RE = re.compile(r"CUST-[0-9]{4,12}")
EVENT_ID_RE = re.compile(r"EVT-[0-9]{6,14}")
SOURCE_RECORD_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _apply(fn: Callable[..., Any], value: Any, *args: Any) -> Any:
    try:
        return fn(value, *args)
    except InvalidValue as exc:
        raise PydanticCustomError(exc.code, exc.message) from None


# ------------------------------------------------------------------ envelope
class PageCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    after_updated_at: datetime
    after_id: str


class PageEnvelope(BaseModel):
    """Contract of one page from ``GET /v1/{entity}``.

    ``data`` is deliberately ``list[Any]``: individual records are validated
    separately so one bad record does not reject the whole page."""

    model_config = ConfigDict(extra="ignore")
    data: list[Any]
    has_more: bool
    next_cursor: PageCursor | None = None


# ------------------------------------------------------------------ records
class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    entity: ClassVar[str]
    business_key_field: ClassVar[str]
    hash_fields: ClassVar[tuple[str, ...]]

    source_record_id: str
    source_updated_at: datetime

    @field_validator("source_record_id", mode="before")
    @classmethod
    def _source_record_id(cls, v: Any) -> str:
        return _apply(normalize_identifier, v, SOURCE_RECORD_ID_RE)

    @field_validator("source_updated_at", mode="before")
    @classmethod
    def _source_updated_at(cls, v: Any) -> datetime:
        return _apply(normalize_timestamp, v)

    @property
    def business_key(self) -> str:
        return getattr(self, self.business_key_field)

    @property
    def unknown_fields(self) -> list[str]:
        return sorted((self.model_extra or {}).keys())

    def content_hash(self) -> str:
        return record_hash({f: getattr(self, f) for f in self.hash_fields})


class OrderRecord(SourceRecord):
    entity: ClassVar[str] = "orders"
    business_key_field: ClassVar[str] = "order_id"
    # Version metadata (source_updated_at, source_record_id) is excluded so two
    # deliveries of the same business content hash identically.
    hash_fields: ClassVar[tuple[str, ...]] = (
        "order_id",
        "customer_id",
        "order_status",
        "order_amount",
        "currency",
        "created_at",
        "updated_at",
    )

    order_id: str
    customer_id: str
    order_status: str
    order_amount: Decimal
    currency: str
    created_at: datetime
    updated_at: datetime

    @field_validator("order_id", mode="before")
    @classmethod
    def _order_id(cls, v: Any) -> str:
        return _apply(normalize_identifier, v, ORDER_ID_RE)

    @field_validator("customer_id", mode="before")
    @classmethod
    def _customer_id(cls, v: Any) -> str:
        return _apply(normalize_identifier, v, CUSTOMER_ID_RE)

    @field_validator("order_status", mode="before")
    @classmethod
    def _status(cls, v: Any) -> str:
        return _apply(normalize_enum, v, ORDER_STATUSES)

    @field_validator("order_amount", mode="before")
    @classmethod
    def _amount(cls, v: Any) -> Decimal:
        return _apply(normalize_amount, v)

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, v: Any) -> str:
        return _apply(normalize_currency, v, CURRENCIES)

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _timestamps(cls, v: Any) -> datetime:
        return _apply(normalize_timestamp, v)

    @model_validator(mode="after")
    def _timestamp_order(self) -> OrderRecord:
        if self.updated_at < self.created_at:
            raise PydanticCustomError("TIMESTAMP_ORDER", "updated_at is earlier than created_at")
        return self


class OrderEventRecord(SourceRecord):
    entity: ClassVar[str] = "order_events"
    business_key_field: ClassVar[str] = "event_id"
    hash_fields: ClassVar[tuple[str, ...]] = (
        "event_id",
        "order_id",
        "event_type",
        "event_timestamp",
    )

    event_id: str
    order_id: str
    event_type: str
    event_timestamp: datetime

    @field_validator("event_id", mode="before")
    @classmethod
    def _event_id(cls, v: Any) -> str:
        return _apply(normalize_identifier, v, EVENT_ID_RE)

    @field_validator("order_id", mode="before")
    @classmethod
    def _order_id(cls, v: Any) -> str:
        return _apply(normalize_identifier, v, ORDER_ID_RE)

    @field_validator("event_type", mode="before")
    @classmethod
    def _event_type(cls, v: Any) -> str:
        return _apply(normalize_enum, v, EVENT_TYPES)

    @field_validator("event_timestamp", mode="before")
    @classmethod
    def _event_timestamp(cls, v: Any) -> datetime:
        return _apply(normalize_timestamp, v)


RECORD_MODELS: dict[str, type[SourceRecord]] = {
    "orders": OrderRecord,
    "order_events": OrderEventRecord,
}


def required_fields(entity: str) -> list[str]:
    return [name for name, f in RECORD_MODELS[entity].model_fields.items() if f.is_required()]
