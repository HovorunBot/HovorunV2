"""Verify default-disabled speech migration and its three-state PostgreSQL constraint."""

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Connection, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command


def _verify_speech_upgrade(connection: Connection) -> None:
    """Exercise real upgrades from legacy configurations and preserve media intent."""
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["connection"] = connection
    command.upgrade(config, "chatcmd062")
    connection.execute(
        text(
            "INSERT INTO chats (chat_id, status, platform, chat_type, generation, "
            "legacy_empty_capture, features_configured) "
            "VALUES (-630001, 'approved', 'telegram', 'group', 1, true, false), "
            "(-630002, 'approved', 'telegram', 'group', 1, false, true)"
        )
    )
    command.upgrade(config, "speech063")
    rows = connection.execute(
        text("SELECT speech_mode, legacy_empty_capture, features_configured FROM chats ORDER BY chat_id DESC")
    ).all()
    assert rows == [("off", True, False), ("off", False, True)]
    checks = {check["name"] for check in inspect(connection).get_check_constraints("chats")}
    assert "speech_mode_values" in checks
    for value in ("automatic_only", "unknown"):
        with pytest.raises(IntegrityError, match="speech_mode_values"), connection.begin_nested():
            connection.execute(text("UPDATE chats SET speech_mode = :value"), {"value": value})
    with pytest.raises(IntegrityError, match="null"), connection.begin_nested():
        connection.execute(text("UPDATE chats SET speech_mode = NULL"))
    command.downgrade(config, "chatcmd062")
    assert "speech_mode" not in {column["name"] for column in inspect(connection).get_columns("chats")}
    command.upgrade(config, "head")
    assert connection.scalar(text("SELECT count(*) FROM chats WHERE speech_mode = 'off'")) == 2


@pytest.mark.fresh_schema
async def test_speech_upgrade_preserves_existing_selection_and_rejects_invalid_modes(
    postgres_database_url: str,
) -> None:
    """Run the actual revision chain in an isolated PostgreSQL schema."""
    engine = create_async_engine(postgres_database_url, hide_parameters=True)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_verify_speech_upgrade)
    finally:
        await engine.dispose()
