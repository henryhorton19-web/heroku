"""Alembic environment, wired to `arb.db` metadata.

`render_as_batch` is on because SQLite cannot ALTER most things in place; Alembic
emulates it by rebuilding the table. Without it, the first column change in Step 1
would fail and invite someone to hand-edit the DB instead.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from arb.config import get_settings
from arb.db import Base

config = context.config
target_metadata = Base.metadata

if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", get_settings().db_url)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
