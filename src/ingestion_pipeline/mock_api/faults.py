"""Fault-injection rules for the mock API.

A rule matches requests for an entity (optionally a specific page of the
current scan) and applies a failure a limited number of ``times``
(``None`` = until the rule is cleared). Rules are evaluated in order; the first
match with remaining budget wins. Because the pipeline requests pages
sequentially, the same rules always produce the same sequence of responses.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FaultKind = Literal[
    "status",  # respond with `status_code` (e.g. 429/500/503/401)
    "malformed",  # 200 with a non-JSON body
    "invalid_envelope",  # 200 JSON without the required `data` / `has_more`
    "delay",  # sleep `delay_seconds` before answering normally (client timeouts)
    "drop_field",  # remove `field` from every record on the page (breaking schema change)
    "stuck_cursor",  # empty page, has_more=true, cursor does not advance
]


class FaultRule(BaseModel):
    entity: Literal["orders", "order_events", "*"] = "*"
    page: int | None = Field(default=None, ge=1, description="1-based page of the current scan")
    kind: FaultKind = "status"
    status_code: int = 503
    retry_after: float | None = None
    delay_seconds: float = 0.0
    field: str | None = None
    times: int | None = Field(default=1, ge=1, description="None = always")

    def matches(self, entity: str, page: int) -> bool:
        if self.times is not None and self.times <= 0:
            return False
        if self.entity not in ("*", entity):
            return False
        return self.page is None or self.page == page

    def consume(self) -> None:
        if self.times is not None:
            self.times -= 1
