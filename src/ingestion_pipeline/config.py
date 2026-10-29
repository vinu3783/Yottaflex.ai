"""Pipeline configuration.

All settings come from environment variables (prefix ``PIPELINE_``) or a local
``.env`` file. Secrets are typed as ``SecretStr`` so they are masked in reprs,
logs and error messages.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENTITIES: tuple[str, ...] = ("orders", "order_events")
"""Entities in dependency order: events reference orders, so orders load first."""


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PIPELINE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://pipeline:pipeline@localhost:5432/pipeline"
    )

    # Source API
    api_base_url: str = "http://localhost:8000"
    api_token: SecretStr | None = None
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"

    # HTTP behaviour
    page_size: int = Field(default=500, ge=1, le=5000)
    request_timeout_seconds: float = Field(default=10.0, gt=0)
    requests_per_second: float = Field(default=20.0, ge=0, description="0 disables throttling")
    max_attempts: int = Field(default=5, ge=1, le=20)
    backoff_base_seconds: float = Field(default=0.5, ge=0)
    backoff_max_seconds: float = Field(default=30.0, ge=0)
    max_retry_after_seconds: float = Field(default=60.0, ge=0)
    retryable_status_codes: frozenset[int] = frozenset({429, 500, 502, 503, 504})
    retry_malformed_responses: bool = True

    # Incremental extraction
    lookback_seconds: int = Field(default=3600, ge=0)
    max_pages_per_run: int = Field(default=10_000, ge=1)

    # Data quality policy
    breaking_change_min_records: int = Field(default=5, ge=1)
    breaking_change_missing_ratio: float = Field(default=0.9, gt=0, le=1)

    # Publishing
    write_mode: str = Field(default="batch", pattern="^(batch|row)$")

    # Logging
    log_level: str = "INFO"
    log_format: str = Field(default="json", pattern="^(json|text)$")

    @field_validator("api_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    def database_url_value(self) -> str:
        return self.database_url.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> PipelineSettings:
    return PipelineSettings()
