"""The complete 0.3.0 Alembic chain upgrades and downgrades on a representative database."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from alembic import command
from tests.migrations.helpers import migration_config


def test_full_chain_reaches_single_head_and_downgrades(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Upgrade from an empty database to head and back without a broken revision."""
    database = tmp_path / "full_chain.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "head")
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"users", "chats", "chat_commands", "notification_events", "announcement_drafts"} <= tables
        columns = {row[1] for row in connection.execute("PRAGMA table_info(chat_commands)")}
        assert "deleted_at" in columns
    command.downgrade(config, "base")
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "users" not in tables
