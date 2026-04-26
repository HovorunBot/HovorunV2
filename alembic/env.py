import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hovorunv2.infrastructure.config import settings
from hovorunv2.domain import Base
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.command import CommandDB

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = f"sqlite+aiosqlite:///{settings.db_path}"
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # set url dynamically from settings
    section = config.get_section(config.config_ini_section)
    if section is None:
        section = {}
    section["sqlalchemy.url"] = f"sqlite+aiosqlite:///{settings.db_path}"

    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations():
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(run_migrations_online())
        else:
            import threading
            def _run():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    new_loop.run_until_complete(run_migrations_online())
                finally:
                    new_loop.close()
            thread = threading.Thread(target=_run)
            thread.start()
            thread.join()

run_migrations()
