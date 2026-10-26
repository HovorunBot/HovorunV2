"""Real Alembic upgrade preserves existing groups and constrains route associations."""

import sqlite3
from pathlib import Path

import pytest

from alembic import command
from tests.migrations.helpers import migration_config


def test_group_route_upgrade_preserves_old_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Upgrade old data and enforce route uniqueness and referential integrity."""
    database = tmp_path / "routes.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "a8c7f6d5e4b3")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO chats (id, chat_id, platform, status, encryption_salt) "
            "VALUES (1, -250001, 'telegram', 'banned', 'synthetic-salt')"
        )
        before = connection.execute("SELECT * FROM chats").fetchall()
    command.upgrade(config, "group025_routing_aliases")
    command.upgrade(config, "group025_routing_aliases")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("SELECT * FROM chats").fetchall() == before
        connection.execute("INSERT INTO group_routes (telegram_id, group_id) VALUES (-250001, 1)")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute("INSERT INTO group_routes (telegram_id, group_id) VALUES (-250001, 1)")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute("INSERT INTO group_routes (telegram_id, group_id) VALUES (-250002, 2)")
    command.downgrade(config, "a8c7f6d5e4b3")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM chats").fetchall() == before
