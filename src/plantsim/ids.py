"""Deterministic, zero-padded identifiers. IDs are assigned once and never reused."""

from __future__ import annotations

import re

FORMATS = {
    "FAC": "FAC-{:03d}",
    "PUMP": "PUMP-{:03d}",
    "TANK": "TANK-{:03d}",
    "VALVE": "VALVE-{:03d}",
    "PIPE": "PIPE-{:03d}",
    "SNS": "SNS-{:03d}",
    "MODE": "MODE-{:03d}",
    "ASM": "ASM-{:03d}",
    "SIM": "SIM-2026-{:06d}",
    "TEL": "TEL-{:07d}",
    "PSE": "PSE-{:06d}",
    "VSE": "VSE-{:06d}",
    "CSE": "CSE-{:06d}",
    "ALM": "ALM-{:06d}",
    "AEV": "AEV-{:06d}",
    "ARV": "ARV-{:04d}",
    "GT": "GT-{:04d}",
}

PATTERNS = {
    "FAC": r"^FAC-\d{3}$",
    "PUMP": r"^PUMP-\d{3}$",
    "TANK": r"^TANK-\d{3}$",
    "VALVE": r"^VALVE-\d{3}$",
    "NODE": r"^(PUMP|TANK|VALVE)-\d{3}$",
    "PIPE": r"^PIPE-\d{3}$",
    "SNS": r"^SNS-\d{3}$",
    "MODE": r"^MODE-\d{3}$",
    "ASM": r"^ASM-\d{3}$",
    "SIM": r"^SIM-\d{4}-\d{6}$",
    "TEL": r"^TEL-\d{7}$",
    "PSE": r"^PSE-\d{6}$",
    "VSE": r"^VSE-\d{6}$",
    "CSE": r"^CSE-\d{6}$",
    "ALM": r"^ALM-\d{6}$",
    "AEV": r"^AEV-\d{6}$",
    "ARV": r"^ARV-\d{4}$",
    "GT": r"^GT-\d{4}$",
}


def make(kind: str, n: int) -> str:
    return FORMATS[kind].format(n)


def matches(kind: str, value: str) -> bool:
    return bool(re.match(PATTERNS[kind], value or ""))


class Counter:
    """Hands out sequential IDs per prefix."""

    def __init__(self, start: dict[str, int] | None = None):
        self._next = dict(start or {})

    def next(self, kind: str) -> str:
        n = self._next.get(kind, 0) + 1
        self._next[kind] = n
        return make(kind, n)

    def peek(self, kind: str) -> int:
        return self._next.get(kind, 0)
