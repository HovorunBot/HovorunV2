"""Restriction migrations preserve existing group records."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command


def test_ban_migration_preserves_existing_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Actual Alembic upgrade adds empty bans without modifying existing approved groups."""
    database = tmp_path / "bans.db"
    monkeypatch.setenv("DB_PATH", str(database))
    config = Config("alembic.ini")
    config.attributes["sqlite_path"] = database
    command.upgrade(config, "trust013")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("INSERT INTO chats (chat_id, platform, status) VALUES (-101, 'telegram', 'approved')")
        before = connection.execute("SELECT * FROM chats").fetchall()
    command.upgrade(config, "ban014")
    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute("SELECT * FROM chats").fetchall() == before
        assert connection.execute("SELECT COUNT(*) FROM bans").fetchone() == (0,)
    command.downgrade(config, "trust013")
    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute("SELECT * FROM chats").fetchall() == before
