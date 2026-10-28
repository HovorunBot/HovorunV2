"""Trust revision migrations preserve existing trust and authority."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from alembic import command
from tests.migrations.helpers import TIER_REVISION, migration_config


def test_trust_revision_upgrade_preserves_existing_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Upgrade a real pre-trust database without changing trust or authority."""
    database = tmp_path / "trust.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, TIER_REVISION)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO users (platform, telegram_id, role, is_trusted, generation, created_at, updated_at) "
            "VALUES ('telegram', 880013, 10, 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    command.upgrade(config, "trust013")
    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute("SELECT role, is_trusted, trust_revision FROM users").fetchone() == (10, 1, 0)
    command.downgrade(config, TIER_REVISION)
    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute("SELECT role, is_trusted FROM users").fetchone() == (10, 1)
