"""Record validation and schema-drift policy.

Policy (also in docs/DESIGN_DECISIONS.md):

* Unknown **additional** fields -> record accepted, field name counted in the
  run's ``unknown_fields`` and logged as a warning (additive change).
* A required field missing / null / wrong type on an individual record ->
  that record is quarantined (record-level problem).
* The same required field missing on >= ``breaking_change_missing_ratio`` of a
  page (with at least ``breaking_change_min_records`` records) -> the run fails
  with ``BreakingSchemaChangeError`` *before* anything from that page is
  published, and the checkpoint is not advanced (source contract changed).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .errors import BreakingSchemaChangeError
from .schemas import RECORD_MODELS, SourceRecord, required_fields


@dataclass(frozen=True)
class Issue:
    code: str
    field: str | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "field": self.field, "message": self.message}


@dataclass
class ValidationResult:
    position: int
    payload: Any
    record: SourceRecord | None = None
    issues: list[Issue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.record is not None

    @property
    def source_record_id(self) -> str | None:
        return _peek_str(self.payload, "source_record_id")

    def business_key(self, entity: str) -> str | None:
        if self.record is not None:
            return self.record.business_key
        return _peek_str(self.payload, RECORD_MODELS[entity].business_key_field)


def _peek_str(payload: Any, key: str) -> str | None:
    """Best-effort identifier from an invalid payload, for traceability only."""
    if isinstance(payload, dict) and isinstance(payload.get(key), str):
        return payload[key].strip()[:64] or None
    return None


def _issue_from_pydantic(err: dict[str, Any]) -> Issue:
    loc = ".".join(str(p) for p in err.get("loc", ())) or None
    etype = err["type"]
    if etype == "missing":
        code = "MISSING_REQUIRED_FIELD"
    elif etype.isupper():  # our PydanticCustomError codes are UPPER_SNAKE_CASE
        code = etype
    else:  # any other pydantic built-in error is a type problem
        code = "INVALID_TYPE"
    return Issue(code=code, field=loc, message=err["msg"])


def validate_record(entity: str, position: int, payload: Any) -> ValidationResult:
    result = ValidationResult(position=position, payload=payload)
    if not isinstance(payload, dict):
        result.issues.append(
            Issue("NOT_AN_OBJECT", None, f"record is {type(payload).__name__}, expected object")
        )
        return result
    try:
        result.record = RECORD_MODELS[entity].model_validate(payload)
    except ValidationError as exc:
        result.issues = [_issue_from_pydantic(e) for e in exc.errors()]
    return result


def check_breaking_change(
    entity: str, records: list[Any], *, min_records: int, missing_ratio: float
) -> None:
    """Fail fast when a required field vanished from (almost) the whole page."""
    objects = [r for r in records if isinstance(r, dict)]
    if len(objects) < min_records:
        return
    missing = Counter(f for r in objects for f in required_fields(entity) if f not in r)
    broken = sorted(f for f, n in missing.items() if n / len(objects) >= missing_ratio)
    if broken:
        raise BreakingSchemaChangeError(
            f"{entity}: required field(s) {broken} missing from "
            f">= {missing_ratio:.0%} of {len(objects)} records on one page"
        )
