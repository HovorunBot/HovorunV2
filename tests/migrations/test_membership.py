"""Actual additive membership migration preserves access and enforces evidence integrity."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command


def test_membership_upgrade_is_additive_and_enforces_active_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upgrade old data, reject bad evidence, retain soft-deleted history, and reverse schema only."""
    database = tmp_path / "membership.db"
    monkeypatch.setenv("DB_PATH", str(database))
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["sqlite_path"] = database
    command.upgrade(config, "legacy016")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO chats (id, chat_id, platform, status) VALUES (1, -100101, 'telegram', 'banned')"
        )
        connection.execute(
            "INSERT INTO users (id, telegram_id, created_at, updated_at) VALUES (1, 101, '2026-01-01', '2026-01-01')"
        )
        before = connection.execute("SELECT * FROM chats").fetchall()
    command.upgrade(config, "member019")
    command.upgrade(config, "member019")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("SELECT * FROM chats").fetchall() == before
        assert connection.execute("SELECT COUNT(*) FROM member_evidence").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM group_membership_states").fetchone() == (0,)
        connection.execute("INSERT INTO group_membership_states (group_id) VALUES (1)")
        assert connection.execute(
            "SELECT policy_revision, admins_known, bot_status FROM group_membership_states"
        ).fetchone() == (0, 0, "unknown")
        insert = (
            "INSERT INTO member_evidence (group_id, user_id, status, verified_at, expires_at) "
            "VALUES (?, ?, ?, '2026-01-01', '2026-01-02')"
        )
        connection.execute(insert, (1, 1, "admin"))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(insert, (1, 1, "left"))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute(insert, (1, 1, "fabricated"))
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(insert, (1, 2, "admin"))
        connection.execute("UPDATE member_evidence SET deleted_at = '2026-01-02'")
        connection.execute(insert, (1, 1, "left"))
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    command.downgrade(config, "legacy016")
    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute("SELECT * FROM chats").fetchall() == before
    command.upgrade(config, "member019")
