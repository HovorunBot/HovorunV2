"""Command association migration adds a soft-deletion marker."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from alembic import command
from tests.migrations.helpers import migration_config


def test_chat_commands_soft_delete_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Upgrade a representative old database and remove only the additive marker."""
    database = tmp_path / "chat_commands.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "announce060")
    command.upgrade(config, "chatcmd062")
    with closing(sqlite3.connect(database)) as connection, connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(chat_commands)")}
        assert "deleted_at" in columns
    command.downgrade(config, "announce060")
    with closing(sqlite3.connect(database)) as connection, connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(chat_commands)")}
        assert "deleted_at" not in columns
