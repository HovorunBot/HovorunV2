"""Preserved revision chain with explicit supplied connections and standalone tooling."""

from logging.config import fileConfig

from sqlalchemy import Connection, create_engine, pool

from alembic import context
from hovorunv2.domain import Base
from hovorunv2.infrastructure.config import DatabaseSettings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    """Own a synchronous CLI connection; async callers supply one through run_sync."""
    sqlite_path = config.attributes.get("sqlite_path")
    url = "sqlite+pysqlite:///" + str(sqlite_path) if sqlite_path else DatabaseSettings().database_url
    engine = create_engine(url, poolclass=pool.NullPool, hide_parameters=True)
    try:
        with engine.connect() as connection:
            do_run_migrations(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=DatabaseSettings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    do_run_migrations(config.attributes["connection"])
else:
    run_online()
