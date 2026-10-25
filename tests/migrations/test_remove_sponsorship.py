"""Actual sponsorship removal preserves whitelist grants, requests, configuration and audit."""

import json
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command


@pytest.mark.parametrize("downgrade_again", [False, True])
def test_remove_sponsorship_preserves_independent_access_and_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    downgrade_again: bool,
) -> None:
    """Withdraw associations without widening whitelist access or rewriting original records."""
    database = tmp_path / "remove_sponsorship.db"
    monkeypatch.setenv("DB_PATH", str(database))
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(config, "member019")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO chats (id, chat_id, platform, status, chat_type, target_lang, ignored_langs, encryption_salt) "
            "VALUES (1, -100101, 'telegram', 'approved', 'supergroup', 'uk', '[\"en\"]', 'synthetic-salt')"
        )
        connection.execute(
            "INSERT INTO chats (id, chat_id, platform, status, chat_type) "
            "VALUES (2, -100102, 'telegram', 'unauthorized', 'group')"
        )
        connection.executemany(
            "INSERT INTO users (id, telegram_id, role, is_trusted, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '2026-01-01', '2026-01-01')",
            [(1, 101, 1, 1), (2, 102, 10, 0)],
        )
        connection.executemany(
            "INSERT INTO group_approvals (group_id, basis, created_at) VALUES (1, ?, '2026-01-01')",
            [("explicit",), ("legacy",)],
        )
        connection.execute(
            "INSERT INTO group_access_requests (group_id, requested_by_user_id, created_at) VALUES (2, 1, '2026-01-01')"
        )
        connection.executemany(
            "INSERT INTO group_sponsors (group_id, user_id, created_at) VALUES (?, 1, '2026-01-01')",
            [(1,), (2,)],
        )
        connection.executemany(
            "INSERT INTO member_evidence "
            "(group_id, user_id, status, verified_at, expires_at, admin_candidate, needs_verification) "
            "VALUES (1, ?, ?, '2026-01-01', '2026-01-02', ?, ?)",
            [(1, "member", 0, 0), (2, "admin", 1, 0)],
        )
        connection.execute("INSERT INTO group_membership_states (group_id, policy_revision) VALUES (1, 3)")
        connection.execute(
            "INSERT INTO audit_events (created_at, actor_origin, target_type, target_id, action, changed_fields) "
            "VALUES ('2026-01-01', 'cli', 'group_grant', '1', 'sponsor', '{\"user_id\":1}')"
        )
        command_id = connection.execute("SELECT id FROM commands ORDER BY id LIMIT 1").fetchone()[0]
        connection.execute("INSERT INTO chat_commands (chat_id, command_id) VALUES (1, ?)", (command_id,))
        preserved_queries = (
            "SELECT * FROM chats",
            "SELECT * FROM users",
            "SELECT * FROM group_approvals",
            "SELECT * FROM group_access_requests",
            "SELECT * FROM chat_commands",
            "SELECT * FROM group_membership_states",
        )
        before = {query: connection.execute(query).fetchall() for query in preserved_queries}
        audit_before = connection.execute("SELECT * FROM audit_events ORDER BY id").fetchall()
    command.upgrade(config, "a8c7f6d5e4b3")
    command.upgrade(config, "a8c7f6d5e4b3")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'group_sponsors'").fetchall() == []
        assert {query: connection.execute(query).fetchall() for query in preserved_queries} == before
        audit_after = connection.execute("SELECT * FROM audit_events ORDER BY id").fetchall()
        assert audit_after[: len(audit_before)] == audit_before
        migration_event = connection.execute(
            "SELECT actor_origin, changed_fields FROM audit_events WHERE action = 'remove_sponsorship'"
        ).fetchone()
        assert migration_event[0] == "migration"
        assert json.loads(migration_event[1]) == {"removed_relationships": 2, "removed_irrelevant_evidence": 1}
        assert connection.execute("SELECT user_id, status FROM member_evidence").fetchall() == [(2, "admin")]
        assert connection.execute("SELECT group_id, basis FROM group_approvals WHERE group_id = 2").fetchall() == []
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    if downgrade_again:
        command.downgrade(config, "member019")
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT COUNT(*) FROM group_sponsors").fetchone() == (0,)
            assert {query: connection.execute(query).fetchall() for query in preserved_queries} == before
            assert connection.execute("SELECT * FROM audit_events ORDER BY id").fetchall() == audit_after
        command.upgrade(config, "a8c7f6d5e4b3")
