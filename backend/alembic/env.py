import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.base import Base
from app.models import *  # noqa: F401,F403  (registers models on Base.metadata)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Phase 17B-1: migrations must run as the schema-owning role, never the
# restricted runtime application role DATABASE_URL may point at once an
# environment is cut over (see backend/app/core/config.py's
# DATABASE_MIGRATION_URL docstring and
# backend/scripts/db/provision_app_role.py). Falls back to DATABASE_URL
# when DATABASE_MIGRATION_URL is unset, matching every environment's
# existing single-role behavior until it is explicitly cut over.
_settings = get_settings()
config.set_main_option("sqlalchemy.url", _settings.DATABASE_MIGRATION_URL or _settings.DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
