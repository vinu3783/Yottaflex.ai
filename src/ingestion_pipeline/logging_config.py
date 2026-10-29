"""Structured (JSON) logging with run context.

``run_id`` and ``entity`` are stored in context variables and attached to every
log record automatically, so callers do not have to pass them around.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
entity_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("entity", default=None)

# Attributes every LogRecord has; anything else was passed through ``extra=``.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization|api[_-]?key|token|password)(\s*[:=]\s*)([^\s,&]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]+)"),
    re.compile(r"(postgres(?:ql)?(?:\+\w+)?://[^:/@\s]+:)([^@\s]+)(@)"),
]


def redact(text: str) -> str:
    """Mask credentials that might appear in free text (URLs, headers, messages)."""
    text = _SECRET_PATTERNS[0].sub(r"\1\2***", text)
    text = _SECRET_PATTERNS[1].sub(r"\1***", text)
    text = _SECRET_PATTERNS[2].sub(r"\1***\3", text)
    return text


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "run_id"):
            record.run_id = run_id_var.get()
        if not hasattr(record, "entity"):
            record.entity = entity_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": redact(record.getMessage()),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = {
            k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS and v is not None
        }
        base = f"{record.levelname:<7} {record.name}: {redact(record.getMessage())}"
        if extras:
            base += " " + " ".join(f"{k}={v}" for k, v in extras.items())
        if record.exc_info:
            base += "\n" + redact(self.formatException(record.exc_info))
        return base


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    handler.addFilter(ContextFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # httpx logs full URLs at INFO; keep it quiet, our client logs sanitized endpoints.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
