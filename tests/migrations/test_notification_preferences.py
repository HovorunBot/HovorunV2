"""Notification preference migration adds a lifecycle-bound table without changing history."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from alembic import command
from tests.migrations.helpers import migration_config


def test_notification_preference_migration_upgrade_and_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrade a representative old database to the additive table and revert it cleanly."""
    database = tmp_path / "notifications.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "emergency051")
    command.upgrade(config, "notif052")
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "notification_preferences" in tables
        connection.execute(
            "INSERT INTO users (platform, telegram_id, role, is_trusted, generation, created_at, updated_at) "
            "VALUES ('telegram', 530001, 1, 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        user_id = connection.execute("SELECT id FROM users WHERE telegram_id = 530001").fetchone()[0]
        connection.execute(
            "INSERT INTO notification_preferences (user_id, category, enabled, created_at, updated_at) "
            "VALUES (?, 'release', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (user_id,),
        )
        assert connection.execute("SELECT category, enabled FROM notification_preferences").fetchone() == (
            "release",
            0,
        )
    command.downgrade(config, "emergency051")
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "notification_preferences" not in tables
