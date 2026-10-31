"""Feature emergency state migration adds the authoritative table without changing history."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from alembic import command
from tests.migrations.helpers import migration_config


def test_feature_emergency_migration_upgrade_and_downgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Upgrade a representative old database to the additive table and revert it cleanly."""
    database = tmp_path / "emergency.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "postgres041")
    command.upgrade(config, "emergency049")
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "feature_emergency_states" in tables
        connection.execute(
            "INSERT INTO feature_emergency_states (feature_key, disabled, reason, epoch, updated_at) "
            "VALUES ('twitter', 1, 'synthetic outage', 1, CURRENT_TIMESTAMP)"
        )
        assert connection.execute("SELECT feature_key, disabled, epoch FROM feature_emergency_states").fetchone() == (
            "twitter",
            1,
            1,
        )
    command.downgrade(config, "postgres041")
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "feature_emergency_states" not in tables


def test_public_reason_column_upgrade_and_downgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The public-safe reason column is additive and removable without touching other data."""
    database = tmp_path / "emergency051.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "emergency049")
    command.upgrade(config, "emergency051")
    with closing(sqlite3.connect(database)) as connection, connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(feature_emergency_states)")}
        assert "public_reason" in columns
        connection.execute(
            "INSERT INTO feature_emergency_states "
            "(feature_key, disabled, reason, public_reason, epoch, updated_at) "
            "VALUES ('twitter', 1, 'internal note', 'Temporary outage', 1, CURRENT_TIMESTAMP)"
        )
        assert connection.execute("SELECT public_reason FROM feature_emergency_states").fetchone() == (
            "Temporary outage",
        )
    command.downgrade(config, "emergency049")
    with closing(sqlite3.connect(database)) as connection, connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(feature_emergency_states)")}
        assert "public_reason" not in columns
        assert connection.execute("SELECT reason, epoch FROM feature_emergency_states").fetchone() == (
            "internal note",
            1,
        )
