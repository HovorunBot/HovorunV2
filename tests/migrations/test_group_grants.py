"""Upgrade representative old status/selection combinations through real Alembic revisions."""

import json
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command


@pytest.mark.parametrize("selected", [False, True])
def test_legacy_grants_preserve_all_statuses_and_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    selected: bool,
) -> None:
    """Only approved active groups acquire a legacy basis; configuration stays byte-for-byte."""
    database = tmp_path / "legacy_grants.db"
    monkeypatch.setenv("DB_PATH", str(database))
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["sqlite_path"] = database
    command.upgrade(config, "grant015")
    statuses = ["unauthorized", "pending", "approved", "rejected", "banned", "inactive"]
    with sqlite3.connect(database) as connection:
        for index, status in enumerate(statuses, start=1):
            connection.execute(
                "INSERT INTO chats (id, chat_id, platform, status, target_lang, ignored_langs, "
                "encryption_salt, chat_type) "
                "VALUES (?, ?, 'telegram', ?, 'uk', '[\"en\",\"pl\"]', 'synthetic-preserved-salt', 'supergroup')",
                (index, -100000 - index, status),
            )
        connection.execute(
            "INSERT INTO chats (id, chat_id, platform, status, chat_type) "
            "VALUES (100, 100, 'telegram', 'approved', 'private')"
        )
        connection.execute(
            "INSERT INTO chats (id, chat_id, platform, status, chat_type, deleted_at) "
            "VALUES (101, -100101, 'telegram', 'approved', 'group', '2026-01-01')"
        )
        if selected:
            command_id = connection.execute("SELECT id FROM commands ORDER BY id LIMIT 1").fetchone()[0]
            connection.executemany(
                "INSERT INTO chat_commands (chat_id, command_id) VALUES (?, ?)",
                [(index, command_id) for index in (*range(1, 7), 100)],
            )
        chats_before = connection.execute("SELECT * FROM chats ORDER BY id").fetchall()
        commands_before = connection.execute("SELECT * FROM chat_commands ORDER BY chat_id").fetchall()
    command.upgrade(config, "legacy016")
    command.upgrade(config, "legacy016")
    with sqlite3.connect(database) as connection:
        chats_after = connection.execute("SELECT * FROM chats ORDER BY id").fetchall()
        assert [row[:-2] for row in chats_after] == chats_before
        assert [row[-2] for row in chats_after] == [int(not selected)] * len(statuses) + [0, 0]
        assert [row[-1] for row in chats_after] == [1] * len(statuses) + [int(selected), 0]
        assert connection.execute("SELECT * FROM chat_commands ORDER BY chat_id").fetchall() == commands_before
        assert connection.execute("SELECT group_id, basis FROM group_approvals").fetchall() == [(3, "legacy")]
        assert connection.execute("SELECT COUNT(*) FROM group_sponsors").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone() == (0,)
        events = connection.execute(
            "SELECT actor_origin, target_id, changed_fields FROM audit_events WHERE action = 'preserve_legacy'"
        ).fetchall()
        assert len(events) == (1 if selected else len(statuses))
        assert all(origin == "migration" for origin, _, _ in events)
        approved_event = next(json.loads(changes) for _, target, changes in events if target == "3")
        assert approved_event["approval_basis"] == "legacy"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    command.downgrade(config, "grant015")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM chats ORDER BY id").fetchall() == chats_before
        assert connection.execute("SELECT COUNT(*) FROM group_approvals").fetchone() == (0,)
    command.upgrade(config, "legacy016")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT group_id, basis FROM group_approvals").fetchall() == [(3, "legacy")]
        connection.execute(
            "INSERT INTO chats (id, chat_id, platform, status) VALUES (102, -100102, 'telegram', 'unauthorized')"
        )
        assert connection.execute(
            "SELECT features_configured, legacy_empty_capture FROM chats WHERE id = 102"
        ).fetchone() == (0, 0)
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            connection.execute("UPDATE chats SET features_configured = NULL WHERE id = 102")


def test_migrated_grants_enforce_constraints_and_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Actual Alembic schema protects parent integrity and active grant/request uniqueness."""
    database = tmp_path / "grant_constraints.db"
    monkeypatch.setenv("DB_PATH", str(database))
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["sqlite_path"] = database
    command.upgrade(config, "legacy016")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO chats (id, chat_id, platform, status) VALUES (1, -100101, 'telegram', 'unauthorized')"
        )
        connection.execute(
            "INSERT INTO users (id, telegram_id, created_at, updated_at) VALUES (1, 101, '2026-01-01', '2026-01-01')"
        )
        insert_approval = "INSERT INTO group_approvals (group_id, basis, created_at) VALUES (?, ?, '2026-01-01')"
        connection.execute(insert_approval, (1, "explicit"))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(insert_approval, (1, "explicit"))
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(insert_approval, (2, "explicit"))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute(insert_approval, (1, "invented"))
        connection.execute("UPDATE group_approvals SET revoked_at = '2026-01-02'")
        connection.execute(insert_approval, (1, "explicit"))
        sponsor = "INSERT INTO group_sponsors (group_id, user_id, created_at) VALUES (1, 1, '2026-01-01')"
        connection.execute(sponsor)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(sponsor)
        connection.execute("UPDATE group_sponsors SET deleted_at = '2026-01-02'")
        connection.execute(sponsor)
        request = (
            "INSERT INTO group_access_requests (group_id, requested_by_user_id, created_at) VALUES (1, 1, '2026-01-01')"
        )
        connection.execute(request)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(request)
        connection.execute("UPDATE group_access_requests SET status = 'rejected'")
        connection.execute(request)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
