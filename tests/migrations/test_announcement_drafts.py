"""Announcement draft migration adds encrypted drafts and selected targets."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from alembic import command
from tests.migrations.helpers import migration_config


def test_announcement_draft_migration_upgrade_and_downgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Upgrade a representative old database and remove only the additive tables."""
    database = tmp_path / "announcement_drafts.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "beta056")
    command.upgrade(config, "announce058")
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"announcement_drafts", "announcement_targets"} <= tables
        columns = {row[1] for row in connection.execute("PRAGMA table_info(announcement_drafts)")}
        assert {"body_ciphertext", "body_salt", "revision", "preview_count"} <= columns
    command.downgrade(config, "beta056")
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "announcement_drafts" not in tables
        assert "announcement_targets" not in tables
