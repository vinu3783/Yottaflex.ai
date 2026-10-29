"""Local, deterministic mock of the source orders API (FastAPI)."""

from .app import MockSettings, create_app

__all__ = ["MockSettings", "create_app"]
