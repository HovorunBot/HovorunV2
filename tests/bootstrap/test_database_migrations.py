"""Verify that production migrations create the current PostgreSQL schema."""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command


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


@pytest.mark.fresh_schema
async def test_migrations_create_current_postgres_schema(postgres_database_url: str) -> None:
    """Create the deployed schema from an empty, isolated PostgreSQL schema."""
    engine = create_async_engine(postgres_database_url, hide_parameters=True)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_verify_schema)
    finally:
        await engine.dispose()
