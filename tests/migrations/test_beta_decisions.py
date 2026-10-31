"""Beta decision migration adds the durable decision table and inline markup."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from alembic import command
from tests.migrations.helpers import migration_config


def test_beta_decision_migration_upgrade_and_downgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Upgrade a representative old database to the additive table and markup column."""
    database = tmp_path / "beta_decisions.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "notif054")
    command.upgrade(config, "beta056")
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "beta_decisions" in tables
        columns = {row[1] for row in connection.execute("PRAGMA table_info(notification_events)")}
        assert "body_markup" in columns
    command.downgrade(config, "notif054")
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "beta_decisions" not in tables
        columns = {row[1] for row in connection.execute("PRAGMA table_info(notification_events)")}
        assert "body_markup" not in columns
