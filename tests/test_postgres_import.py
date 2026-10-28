"""One-time transfer from the real 0.2.1 SQLite schema through PostgreSQL migration history."""

import asyncio
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from hovorunv2.bootstrap.sqlite_to_postgres import RELEASE_021_REVISION, migration_config, transfer
from hovorunv2.domain.audit import AuditEventDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.group_grant import GroupApprovalDB
from hovorunv2.infrastructure.config import DatabaseSettings


def make_source(path: Path, revision: str = RELEASE_021_REVISION) -> str:
    """Create synthetic legacy data; a large negative Telegram ID must survive PostgreSQL's stricter types."""
    config = migration_config()
    config.attributes["sqlite_path"] = path
    command.upgrade(config, revision)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "INSERT INTO chats (id, chat_id, platform, status, target_lang, ignored_langs, encryption_salt) "
            "VALUES (123, -1001234567890, 'telegram', 'approved', 'uk', '[\"en\",\"pl\"]', 'synthetic-salt')"
        )
        connection.execute(
            "INSERT INTO chat_commands (chat_id, command_id) SELECT 123, id FROM commands WHERE name = 'twitter_video'"
        )
        connection.execute("INSERT INTO system_state (id, key, value) VALUES (44, 'synthetic', 'preserved')")
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def test_transfer_preserves_source_and_data_then_applies_new_migrations(
    tmp_path: Path, postgres_database_url: str
) -> None:
    """Preserve original bytes/IDs/settings, apply legacy approval backfill on PostgreSQL, and reject replay."""
    source = tmp_path / "source.db"
    original = await asyncio.to_thread(make_source, source)
    settings = DatabaseSettings(_env_file=None, database_url=postgres_database_url)
    counts = await transfer(source, settings)
    assert counts["chats"] == 1
    assert counts["system_state"] == 1
    assert counts["chat_commands"] == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    engine = create_async_engine(postgres_database_url)
    try:
        async with engine.begin() as connection:
            chat = (await connection.execute(select(ChatDB.__table__).where(ChatDB.id == 123))).mappings().one()
            assert chat["chat_id"] == -1001234567890
            assert chat["target_lang"] == "uk"
            assert chat["ignored_langs"] == ["en", "pl"]
            assert chat["encryption_salt"] == "synthetic-salt"
            assert await connection.scalar(select(func.count()).select_from(GroupApprovalDB)) == 1
            assert (
                await connection.scalar(
                    select(AuditEventDB.actor_origin).where(
                        AuditEventDB.target_type == "database", AuditEventDB.action == "import_sqlite"
                    )
                )
                == "migration"
            )
            generated_id = await connection.scalar(
                ChatDB.__table__.insert()
                .values(chat_id=-1001234567891, platform="telegram", status="approved")
                .returning(ChatDB.id)
            )
            assert generated_id > 123
        with pytest.raises(ValueError, match="one-time"):
            await transfer(source, settings)
    finally:
        await engine.dispose()


async def test_new_postgres_migration_failure_rolls_back_entire_transfer(
    tmp_path: Path, postgres_database_url: str
) -> None:
    """Duplicate legacy identities fail the actual PostgreSQL upgrade after copy without leaving imported tables."""
    source = tmp_path / "duplicate.db"
    await asyncio.to_thread(make_source, source)
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute(
            "INSERT INTO chats (chat_id, platform, status) VALUES (-1001234567890, 'telegram', 'approved')"
        )
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises((ValueError, IntegrityError), match=r"duplicate|unique"):
        await transfer(source, DatabaseSettings(_env_file=None, database_url=postgres_database_url))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    engine = create_async_engine(postgres_database_url)
    try:
        async with engine.connect() as connection:
            assert await connection.run_sync(_tables) == []
    finally:
        await engine.dispose()


def _tables(connection: object) -> list[str]:
    """Inspect only schema names; no source data enters test output."""
    return inspect(connection).get_table_names()


async def test_source_before_021_is_upgraded_only_in_the_dump(tmp_path: Path, postgres_database_url: str) -> None:
    """Earlier deployments gain 0.2.1 command migration in the snapshot while the source stays at its revision."""
    source = tmp_path / "older.db"
    # This earlier version already has status, salts, system state and the provider command schema.
    original = await asyncio.to_thread(make_source, source, "2c01ece4472f")
    counts = await transfer(source, DatabaseSettings(_env_file=None, database_url=postgres_database_url))
    assert counts["chats"] == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    with closing(sqlite3.connect(source)) as connection, connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("2c01ece4472f",)


@pytest.mark.parametrize("extra", ["table", "column"])
async def test_unmapped_source_data_is_rejected_without_partial_import(
    tmp_path: Path, postgres_database_url: str, extra: str
) -> None:
    """Unknown source storage must never be silently skipped during the one-time transfer."""
    source = tmp_path / "extra.db"
    await asyncio.to_thread(make_source, source)
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute(
            "CREATE TABLE extra_data (id INTEGER PRIMARY KEY)"
            if extra == "table"
            else "ALTER TABLE chats ADD COLUMN extra_value TEXT"
        )
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="disagree"):
        await transfer(source, DatabaseSettings(_env_file=None, database_url=postgres_database_url))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    engine = create_async_engine(postgres_database_url)
    try:
        async with engine.connect() as connection:
            assert await connection.run_sync(_tables) == []
    finally:
        await engine.dispose()


async def test_source_newer_than_021_is_never_downgraded(tmp_path: Path, postgres_database_url: str) -> None:
    """The requested release boundary is explicit; newer branch databases require their own reviewed transfer path."""
    source = tmp_path / "newer.db"
    config = migration_config()
    config.attributes["sqlite_path"] = source
    await asyncio.to_thread(command.upgrade, config, "head")
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match=r"0\.2\.1"):
        await transfer(source, DatabaseSettings(_env_file=None, database_url=postgres_database_url))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
