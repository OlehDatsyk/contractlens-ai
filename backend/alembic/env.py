"""Alembic environment. Uses DATABASE_URL from the application settings."""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

import app.models  # noqa: F401 - register models
from alembic import context
from app.core.config import get_settings
from app.core.db import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    return config.attributes.get("database_url") or get_settings().database_url


def _render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    # Render the UTCDateTime decorator as a plain timezone-aware DateTime in migrations.
    if type_ == "type" and type(obj).__name__ == "UTCDateTime":
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, render_item=_render_item,
                      render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config({"sqlalchemy.url": _url()}, prefix="sqlalchemy.", poolclass=pool.NullPool)
    schema = get_settings().database_schema
    with connectable.connect() as connection:
        extra: dict[str, object] = {}
        if schema and connection.dialect.name == "postgresql":
            # Keep every ContractLens table (and the Alembic version table) inside its own schema.
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.commit()
            extra["version_table_schema"] = schema
        context.configure(connection=connection, target_metadata=target_metadata, render_item=_render_item,
                          render_as_batch=connection.dialect.name == "sqlite", compare_type=True, **extra)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
