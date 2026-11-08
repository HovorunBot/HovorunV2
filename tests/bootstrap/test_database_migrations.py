"""Verify that production migrations create the current PostgreSQL schema."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg.errors import CheckViolation, UniqueViolation
from sqlalchemy import Connection, Table, func, insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.user import UserDB

pytestmark = pytest.mark.fresh_schema


def _verify_schema(connection: Connection) -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")
    inspector = inspect(connection)
    assert {"users", "chats", "chat_commands", "notification_events", "announcement_drafts"} <= set(
        inspector.get_table_names()
    )
    assert "deleted_at" in {column["name"] for column in inspector.get_columns("chat_commands")}
    assert (
        connection.scalar(text("SELECT version_num FROM alembic_version"))
        == ScriptDirectory.from_config(config).get_current_head()
    )


@pytest.fixture
async def migrated_sessions(postgres_database_url: str) -> AsyncGenerator[async_sessionmaker]:
    """Provide sessions against an isolated schema created exclusively by production migrations."""
    engine = create_async_engine(postgres_database_url, hide_parameters=True)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_verify_schema)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("table", "identity_column", "identity_value"),
    [(UserDB.__table__, "telegram_id", 1 << 40), (ChatDB.__table__, "chat_id", -(1 << 40))],
    ids=["user", "group"],
)
async def test_migrated_identities_reject_duplicate_active_rows_and_allow_history(
    migrated_sessions: async_sessionmaker, table: Table, identity_column: str, identity_value: int
) -> None:
    """Preserve large Telegram identities and enforce active uniqueness while retaining deleted generations."""
    async with migrated_sessions.begin() as session:
        await session.execute(insert(table).values({identity_column: identity_value}))
    with pytest.raises(IntegrityError) as error:
        async with migrated_sessions.begin() as session:
            await session.execute(insert(table).values({identity_column: identity_value}))
    assert isinstance(error.value.orig, UniqueViolation)
    async with migrated_sessions.begin() as session:
        await session.execute(
            update(table).where(table.c[identity_column] == identity_value).values(deleted_at=datetime.now(UTC))
        )
        await session.execute(
            insert(table).values({identity_column: identity_value, "generation": 2, "deleted_at": datetime.now(UTC)})
        )
        await session.execute(insert(table).values({identity_column: identity_value, "generation": 3}))
    async with migrated_sessions() as session:
        generations = dict(
            (
                await session.execute(
                    select(table.c.generation, table.c.deleted_at).where(table.c[identity_column] == identity_value)
                )
            ).all()
        )
        assert set(generations) == {1, 2, 3}
        assert generations[1] is not None
        assert generations[2] is not None
        assert generations[3] is None


async def test_migrated_users_reject_invalid_authority(migrated_sessions: async_sessionmaker) -> None:
    """Reject invalid persisted authority even when a writer bypasses application validation."""
    with pytest.raises(IntegrityError) as error:
        async with migrated_sessions.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO users (platform, telegram_id, role, is_trusted, generation, created_at, updated_at) "
                    "VALUES ('telegram', :telegram_id, :role, false, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"telegram_id": 1 << 40, "role": 999},
            )
    assert isinstance(error.value.orig, CheckViolation)
    async with migrated_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(UserDB)) == 0
