"""Alembic environment.

The database URL comes from application settings (i.e. the environment), never
from alembic.ini, so no credential is ever committed.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.db.models import Base
from backend.db.types import PortableJSON
from backend.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Emit the import for our custom column types.

    Without this, autogenerate writes ``backend.db.types.PortableJSON()`` into
    the migration but never imports it, and the migration dies with NameError
    partway through — after some tables already exist.
    """
    if type_ == "type" and isinstance(obj, PortableJSON):
        autogen_context.imports.add("import backend.db.types")
        return "backend.db.types.PortableJSON()"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_item=render_item,
            # SQLite cannot ALTER most things in place; batch mode rewrites the
            # table instead. Harmless on PostgreSQL.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
