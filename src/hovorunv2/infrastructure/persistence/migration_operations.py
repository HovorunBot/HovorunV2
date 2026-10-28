"""Stable Core helpers for preserved historical migration data transformations."""

from sqlalchemy import Connection, MetaData, Table
from sqlalchemy.dialects.postgresql import Insert as PostgreSQLInsert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import Insert as SQLiteInsert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


def migration_tables(connection: Connection, *names: str) -> tuple[Table, ...]:
    """Reflect the schema at this revision, never import evolving runtime models."""
    metadata = MetaData()
    return tuple(Table(name, metadata, autoload_with=connection) for name in names)


def insert_ignore(connection: Connection, table: Table) -> PostgreSQLInsert | SQLiteInsert:
    """Use backend conflict APIs for the supported target and historical source."""
    if connection.dialect.name == "postgresql":
        return postgres_insert(table).on_conflict_do_nothing()
    if connection.dialect.name == "sqlite":
        return sqlite_insert(table).on_conflict_do_nothing()
    msg = "Historical migrations support PostgreSQL and SQLite sources only."
    raise ValueError(msg)
