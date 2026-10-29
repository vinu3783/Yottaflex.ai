"""Alembic environment.

The URL comes from (in order): a value passed programmatically by
``ingestion_pipeline.db.run_migrations``, or the pipeline settings
(``PIPELINE_DATABASE_URL`` / ``.env``).
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from ingestion_pipeline.config import PipelineSettings
from ingestion_pipeline.models import metadata

config = context.config
if config.config_file_name is not None and not config.attributes.get("database_url"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = metadata


def _url() -> str:
    return config.attributes.get("database_url") or PipelineSettings().database_url_value()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
