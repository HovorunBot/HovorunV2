"""Real predecessor upgrade, lifecycle constraints and downgrade for group beta state."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from alembic import command
from tests.migrations.helpers import migration_config


def test_group_beta_upgrade_preserves_data_and_enforces_active_identity_constraints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Add empty beta configuration tables without inferring eligibility or selected state."""
    database = tmp_path / "group-beta.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "beta028")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO chats (id, chat_id, platform, status, encryption_salt) "
            "VALUES (1, -290001, 'telegram', 'approved', 'synthetic-salt')"
        )
        connection.execute(
            "INSERT INTO users (id, telegram_id, created_at, updated_at) "
            "VALUES (1, 290001, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        before_chats = connection.execute("SELECT * FROM chats").fetchall()
        before_users = connection.execute("SELECT * FROM users").fetchall()
        before_audit = connection.execute("SELECT * FROM audit_events").fetchall()
    command.upgrade(config, "groupbeta029")
    command.upgrade(config, "groupbeta029")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("SELECT * FROM chats").fetchall() == before_chats
        assert connection.execute("SELECT * FROM users").fetchall() == before_users
        assert connection.execute("SELECT * FROM audit_events").fetchall() == before_audit
        assert connection.execute("SELECT * FROM group_beta_features").fetchall() == []
        assert connection.execute("SELECT * FROM group_beta_enablers").fetchall() == []
        connection.execute("INSERT INTO group_beta_features (id, group_id, feature) VALUES (1, 1, 'synthetic_beta')")
        assert connection.execute("SELECT selected FROM group_beta_features").fetchone() == (0,)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute("INSERT INTO group_beta_features (group_id, feature) VALUES (1, 'synthetic_beta')")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute("INSERT INTO group_beta_features (group_id, feature) VALUES (1, '')")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute("INSERT INTO group_beta_features (group_id, feature) VALUES (2, 'synthetic_beta')")
        connection.execute(
            "INSERT INTO group_beta_enablers (selection_id, user_id, created_at) VALUES (1, 1, CURRENT_TIMESTAMP)"
        )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(
                "INSERT INTO group_beta_enablers (selection_id, user_id, created_at) VALUES (1, 1, CURRENT_TIMESTAMP)"
            )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(
                "INSERT INTO group_beta_enablers (selection_id, user_id, created_at) VALUES (1, 2, CURRENT_TIMESTAMP)"
            )
        connection.execute("UPDATE group_beta_enablers SET revoked_at = CURRENT_TIMESTAMP")
        connection.execute(
            "INSERT INTO group_beta_enablers (selection_id, user_id, created_at) VALUES (1, 1, CURRENT_TIMESTAMP)"
        )
        connection.execute("UPDATE group_beta_features SET deleted_at = CURRENT_TIMESTAMP WHERE id = 1")
        connection.execute("INSERT INTO group_beta_features (group_id, feature) VALUES (1, 'synthetic_beta')")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    command.downgrade(config, "beta028")
    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute("SELECT * FROM chats").fetchall() == before_chats
        assert connection.execute("SELECT * FROM users").fetchall() == before_users
        assert connection.execute("SELECT * FROM audit_events").fetchall() == before_audit
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name IN ('group_beta_features', 'group_beta_enablers')"
            ).fetchall()
            == []
        )
    command.upgrade(config, "groupbeta029")
    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute("SELECT * FROM group_beta_features").fetchall() == []
        assert connection.execute("SELECT * FROM group_beta_enablers").fetchall() == []
