"""Announcement confirmation migration adds the single-use authorization table."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from alembic import command
from tests.migrations.helpers import migration_config


def test_announcement_confirmation_migration_upgrade_and_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrade a representative old database and remove only the additive table."""
    database = tmp_path / "announcement_confirmations.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "announce058")
    command.upgrade(config, "announce059")
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "announcement_confirmations" in tables
    command.downgrade(config, "announce058")
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "announcement_confirmations" not in tables
