"""One-time SQLite 0.2.1 snapshot import followed by newer migrations on PostgreSQL."""

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

import aiosqlite
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, DateTime, MetaData, Table, func, insert, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from hovorunv2.domain.audit import AuditEventDB
from hovorunv2.domain.audit_schema import validate_audit_changes
from hovorunv2.domain.enums import ActorOrigin
from hovorunv2.infrastructure.config import ROOT_DIR, DatabaseSettings
from hovorunv2.infrastructure.persistence.locking import scoped_lock_key

RELEASE_021_REVISION = "f3a4b5c6d7e8"
IMPORT_BATCH_SIZE = 500
IMPORT_LOCK_SCOPE = "sqlite-import"


def migration_config() -> Config:
    """Use the preserved revision chain, independent of the current working directory."""
    config = Config(str(ROOT_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT_DIR / "alembic"))
    return config


def _upgrade(connection: Connection, revision: str) -> None:
    config = migration_config()
    config.attributes["connection"] = connection
    command.upgrade(config, revision)


def _upgrade_snapshot(snapshot: Path) -> None:
    config = migration_config()
    config.attributes["sqlite_path"] = snapshot
    command.upgrade(config, RELEASE_021_REVISION)


def _current_heads(connection: Connection) -> tuple[str, ...]:
    return MigrationContext.configure(connection).get_current_heads()


async def snapshot_source(source: Path, snapshot: Path) -> None:
    """Make a consistent SQLite backup through a read-only connection; never upgrade the original."""
    if not await asyncio.to_thread(source.is_file):
        msg = "SQLite source must be an existing database file."
        raise ValueError(msg)
    resolved = await asyncio.to_thread(source.resolve)
    async with (
        aiosqlite.connect(resolved.as_uri() + "?mode=ro", uri=True) as original,
        aiosqlite.connect(snapshot) as destination,
    ):
        await original.backup(destination)
    await asyncio.to_thread(snapshot.chmod, 0o600)
    async with aiosqlite.connect(snapshot) as database:
        cursor = await database.execute("PRAGMA integrity_check")
        if await cursor.fetchall() != [("ok",)]:
            msg = "SQLite integrity validation failed."
            raise ValueError(msg)
        await cursor.close()
    engine = create_async_engine("sqlite+aiosqlite:///" + str(snapshot), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            revisions = await connection.run_sync(_current_heads)
    finally:
        await engine.dispose()
    allowed = {
        item.revision
        for item in ScriptDirectory.from_config(migration_config()).walk_revisions(
            base="base", head=RELEASE_021_REVISION
        )
    }
    if len(revisions) != 1 or revisions[0] not in allowed:
        msg = "Source revision must belong to the migration history through release 0.2.1."
        raise ValueError(msg)
    await asyncio.to_thread(_upgrade_snapshot, snapshot)


def _normalize(row: Mapping[str, object], table: Table) -> dict[str, object]:
    values = dict(row)
    for column in table.columns:
        value = values[column.name]
        if isinstance(column.type, DateTime) and isinstance(value, datetime) and value.tzinfo is None:
            values[column.name] = value.replace(tzinfo=UTC)
    return values


def _json_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    msg = "Unsupported value in database transfer verification."
    raise TypeError(msg)


async def _digest(connection: AsyncConnection, table: Table) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    async with connection.stream(select(table).order_by(*table.primary_key.columns)) as result:
        async for row in result.mappings():
            canonical = json.dumps(_normalize(row, table), sort_keys=True, default=_json_value, separators=(",", ":"))
            digest.update(canonical.encode())
            digest.update(b"\n")
            count += 1
    return count, digest.hexdigest()


async def _reset_sequences(connection: AsyncConnection, metadata: MetaData) -> None:
    for table in metadata.sorted_tables:
        for column in table.primary_key.columns:
            sequence = await connection.scalar(
                select(func.pg_get_serial_sequence(table.name, column.name)),
            )
            if sequence is not None:
                maximum = await connection.scalar(select(func.max(column)))
                # The qualified sequence identifier comes quoted from PostgreSQL's catalog function.
                await connection.execute(text(f"ALTER SEQUENCE {sequence} RESTART WITH {int(maximum or 0) + 1}"))


def _reflect(connection: Connection) -> MetaData:
    metadata = MetaData()
    metadata.reflect(bind=connection)
    return metadata


def _table_names(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


async def _validate_target_schema(target: AsyncConnection) -> None:
    """Verify target schema is locked and empty prior to import."""
    schema = await target.scalar(select(func.current_schema()))
    await target.execute(select(func.pg_advisory_xact_lock(scoped_lock_key(str(schema), IMPORT_LOCK_SCOPE))))
    if await target.run_sync(_table_names):
        msg = "PostgreSQL target schema must be empty; import is a one-time operation."
        raise ValueError(msg)


def _validate_schemas_match(metadata: MetaData, source_schema: MetaData) -> None:
    """Verify tables and columns match between SQLite source and PostgreSQL release 0.2.1."""
    source_names = set(source_schema.tables)
    if source_names != set(metadata.tables):
        msg = "SQLite source and PostgreSQL release 0.2.1 table sets disagree."
        raise ValueError(msg)
    if any(
        set(table.columns.keys()) != set(source_schema.tables[name].columns.keys())
        for name, table in metadata.tables.items()
    ):
        msg = "SQLite source and PostgreSQL release 0.2.1 columns disagree."
        raise ValueError(msg)


async def _clear_target_tables(target: AsyncConnection, metadata: MetaData) -> None:
    """Clear target tables seeded by initial migrations in reverse foreign key order."""
    for table in reversed(metadata.sorted_tables):
        if table.name != "alembic_version":
            await target.execute(table.delete())


async def _transfer_table_data(
    source: AsyncConnection,
    target: AsyncConnection,
    table: Table,
) -> int:
    """Transfer table rows in partitions and verify data digest."""
    async with source.stream(select(table).order_by(*table.primary_key.columns)) as result:
        async for rows in result.mappings().partitions(IMPORT_BATCH_SIZE):
            await target.execute(table.insert(), [_normalize(row, table) for row in rows])
    source_digest = await _digest(source, table)
    if source_digest != await _digest(target, table):
        msg = f"Transfer verification failed for table {table.name}."
        raise ValueError(msg)
    return source_digest[0]


async def _record_import_audit(target: AsyncConnection, counts: dict[str, int]) -> None:
    """Record an audit event for the completed SQLite snapshot import."""
    changes: dict[str, object] = {"tables_imported": len(counts), "rows_imported": sum(counts.values())}
    validate_audit_changes("database", "import_sqlite", changes)
    await target.execute(
        insert(AuditEventDB).values(
            created_at=datetime.now(UTC),
            actor_origin=ActorOrigin.MIGRATION,
            target_type="database",
            target_id="sqlite-0.2.1",
            action="import_sqlite",
            changed_fields=changes,
        )
    )


async def _execute_import_pipeline(
    source: AsyncConnection,
    target: AsyncConnection,
) -> dict[str, int]:
    """Orchestrate schema validation, table transfer, sequence reset and upgrade."""
    await _validate_target_schema(target)
    await target.run_sync(partial(_upgrade, revision=RELEASE_021_REVISION))
    metadata = await target.run_sync(_reflect)
    source_schema = await source.run_sync(_reflect)
    _validate_schemas_match(metadata, source_schema)

    await _clear_target_tables(target, metadata)
    counts: dict[str, int] = {}
    for table in metadata.sorted_tables:
        if table.name == "alembic_version":
            continue
        counts[table.name] = await _transfer_table_data(source, target, table)

    await _reset_sequences(target, metadata)
    await target.run_sync(partial(_upgrade, revision="head"))
    await _record_import_audit(target, counts)
    return counts


async def import_snapshot(snapshot: Path, database_url: str) -> dict[str, int]:
    """Create 0.2.1 schema, import and verify every row, then upgrade PostgreSQL in one transaction."""
    source_engine = create_async_engine("sqlite+aiosqlite:///" + str(snapshot), poolclass=NullPool)
    target_engine = create_async_engine(database_url, poolclass=NullPool, hide_parameters=True)
    try:
        async with source_engine.connect() as source, target_engine.begin() as target:
            return await _execute_import_pipeline(source, target)
    finally:
        await source_engine.dispose()
        await target_engine.dispose()


def _temporary_dump_directory() -> TemporaryDirectory[str]:
    """Create a private temporary directory in the filesystem executor thread."""
    return TemporaryDirectory(prefix="hovorun-sqlite-import-")


async def transfer(source: Path, settings: DatabaseSettings) -> dict[str, int]:
    """Keep the temporary dump private and remove it after success or failure; retain the original source."""
    temporary = await asyncio.to_thread(_temporary_dump_directory)
    try:
        snapshot = Path(temporary.name) / "release-0.2.1.sqlite3"
        await snapshot_source(source, snapshot)
        return await import_snapshot(snapshot, settings.database_url)
    finally:
        await asyncio.to_thread(temporary.cleanup)


def main() -> None:
    """Run explicitly as an operator command; output only table names and counts, never connection or row data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Existing SQLite database; stop its writers before final transfer.")
    args = parser.parse_args()
    try:
        counts = asyncio.run(transfer(args.source, DatabaseSettings()))
    except ValueError, SQLAlchemyError, aiosqlite.Error, OSError:
        sys.stderr.write("SQLite transfer failed; source preserved and PostgreSQL transaction rolled back.\n")
        raise SystemExit(1) from None
    sys.stdout.write(json.dumps({"imported": counts, "upgraded_to": "head"}, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
