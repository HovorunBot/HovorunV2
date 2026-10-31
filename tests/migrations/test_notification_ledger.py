"""Notification ledger migration adds event and receipt tables without changing history."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from alembic import command
from tests.migrations.helpers import migration_config


def test_notification_ledger_migration_upgrade_and_downgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Upgrade a representative old database to the additive ledger and revert it cleanly."""
    database = tmp_path / "notification_ledger.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "notif052")
    command.upgrade(config, "notif053")
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"notification_events", "notification_receipts"} <= tables
        connection.execute(
            "INSERT INTO users (platform, telegram_id, role, is_trusted, generation, created_at, updated_at) "
            "VALUES ('telegram', 550001, 1, 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        user_id = connection.execute("SELECT id FROM users WHERE telegram_id = 550001").fetchone()[0]
        connection.execute(
            "INSERT INTO notification_events (event_key, category, introduction, release_identity, created_at) "
            "VALUES ('release:v0.3.0', 'release', 'release', 'v0.3.0', CURRENT_TIMESTAMP)"
        )
        event_id = connection.execute("SELECT id FROM notification_events").fetchone()[0]
        connection.execute(
            "INSERT INTO notification_receipts "
            "(event_id, user_id, status, attempts, created_at, updated_at) "
            "VALUES (?, ?, 'delivered', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (event_id, user_id),
        )
        assert connection.execute("SELECT status FROM notification_receipts").fetchone() == ("delivered",)
    command.downgrade(config, "notif052")
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "notification_events" not in tables
        assert "notification_receipts" not in tables


def test_body_reference_column_upgrade_and_downgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The encrypted body reference column is additive and removable without touching rows."""
    database = tmp_path / "notification_body.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "notif053")
    command.upgrade(config, "notif054")
    with closing(sqlite3.connect(database)) as connection, connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(notification_events)")}
        assert "body_ref" in columns
    command.downgrade(config, "notif053")
    with closing(sqlite3.connect(database)) as connection, connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(notification_events)")}
        assert "body_ref" not in columns
