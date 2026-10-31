"""Urgent announcement migration adds confirmation and event bypass markers."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from alembic import command
from tests.migrations.helpers import migration_config


def test_urgent_migration_upgrade_and_downgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Upgrade a representative old database and remove only the additive markers."""
    database = tmp_path / "announcement_urgent.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "announce059")
    command.upgrade(config, "announce060")
    with closing(sqlite3.connect(database)) as connection, connection:
        confirmation_columns = {row[1] for row in connection.execute("PRAGMA table_info(announcement_confirmations)")}
        assert {"urgent", "urgent_reason"} <= confirmation_columns
        event_columns = {row[1] for row in connection.execute("PRAGMA table_info(notification_events)")}
        assert "bypass_preferences" in event_columns
    command.downgrade(config, "announce059")
    with closing(sqlite3.connect(database)) as connection, connection:
        confirmation_columns = {row[1] for row in connection.execute("PRAGMA table_info(announcement_confirmations)")}
        assert "urgent" not in confirmation_columns
        event_columns = {row[1] for row in connection.execute("PRAGMA table_info(notification_events)")}
        assert "bypass_preferences" not in event_columns
