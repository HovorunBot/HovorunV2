"""Exercise actual identity and role revisions against isolated legacy databases."""

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command

LEGACY_REVISION = "b1c2d3e4f5a6"
USER_REVISION = "d3e4f5a6b7c8"
ASSIGNMENT_REVISION = "a1b2c3d4e5f6"
ROLE_REVISION = "b2c3d4e5f6a7"
CONSTRAINT_REVISION = "c3d4e5f6a7b8"
TIER_REVISION = "d4e5f6a7b8c9"


def migration_config(monkeypatch: pytest.MonkeyPatch, database: Path) -> Config:
    """Point the real Alembic environment at a temporary database and synthetic settings."""
    monkeypatch.setenv("DB_PATH", str(database))
    monkeypatch.setenv("BOT_TOKEN", "123456:synthetic-migration-token")
    monkeypatch.setenv("ADMIN_IDS", "[]")
    monkeypatch.setenv("OWNERS", "[]")
    return Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))


def test_actual_upgrade_preserves_legacy_group_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real revision chain preserves grants, language, salt and selections."""
    database = tmp_path / "legacy.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, LEGACY_REVISION)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO chats (id, chat_id, platform, status, target_lang, ignored_langs, encryption_salt) "
            "VALUES (1, -100123, 'telegram', 'approved', 'uk', '[\"en\"]', 'preserved-test-salt')"
        )
        command_id = connection.execute("SELECT id FROM commands ORDER BY id LIMIT 1").fetchone()[0]
        connection.execute("INSERT INTO chat_commands (chat_id, command_id) VALUES (1, ?)", (command_id,))
        before = connection.execute(
            "SELECT id, chat_id, platform, status, target_lang, ignored_langs, encryption_salt FROM chats"
        ).fetchall()
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT id, chat_id, platform, status, target_lang, ignored_langs, encryption_salt FROM chats"
            ).fetchall()
            == before
        )
        assert connection.execute("SELECT chat_id, command_id FROM chat_commands").fetchall() == [(1, command_id)]
        assert connection.execute("SELECT generation, deleted_at FROM chats").fetchone() == (1, None)
        assert connection.execute("SELECT COUNT(*) FROM user_roles").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone() == (0,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (TIER_REVISION,)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO chats (chat_id, platform, status) VALUES (-100123, 'telegram', 'approved')")

    command.downgrade(config, LEGACY_REVISION)
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT id, chat_id, platform, status, target_lang, ignored_langs, encryption_salt FROM chats"
            ).fetchall()
            == before
        )
        assert connection.execute("SELECT chat_id, command_id FROM chat_commands").fetchall() == [(1, command_id)]
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT id, chat_id, platform, status, target_lang, ignored_langs, encryption_salt FROM chats"
            ).fetchall()
            == before
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_actual_group_upgrade_rejects_duplicate_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The revision itself rejects duplicates rather than merging conflicting access."""
    database = tmp_path / "duplicates.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, USER_REVISION)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO chats (chat_id, platform, status) VALUES (-100123, 'telegram', ?)",
            [("approved",), ("unauthorized",)],
        )
    with pytest.raises(RuntimeError, match="duplicate active chats"):
        command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT status FROM chats ORDER BY id").fetchall() == [
            ("approved",),
            ("unauthorized",),
        ]
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (USER_REVISION,)


def test_actual_role_upgrade_preserves_assignments_and_generations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actual backfill prioritizes OWNER by internal identity and retains historical rows."""
    database = tmp_path / "roles.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, ASSIGNMENT_REVISION)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO users (id, telegram_id, generation, created_at, updated_at, deleted_at) "
            "VALUES (?, ?, ?, '2026-01-01', '2026-01-01', ?)",
            [(1, 101, 1, None), (2, 102, 1, None), (3, 103, 1, "2026-01-02"), (4, 103, 2, None)],
        )
        connection.executemany(
            "INSERT INTO user_roles (user_id, telegram_id, role, created_at, updated_at) "
            "VALUES (?, ?, ?, '2026-01-01', '2026-01-01')",
            [(1, 101, "admin"), (1, 101, "owner"), (3, 103, "admin")],
        )
        assignments = connection.execute("SELECT * FROM user_roles ORDER BY id").fetchall()
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id, role FROM users ORDER BY id").fetchall() == [
            (1, 100),
            (2, 1),
            (3, 10),
            (4, 1),
        ]
        assert connection.execute("SELECT * FROM user_roles ORDER BY id").fetchall() == assignments
        assert connection.execute("SELECT deleted_at FROM users WHERE id = 3").fetchone() == ("2026-01-02",)
        connection.execute(
            "INSERT INTO users (telegram_id, created_at, updated_at) VALUES (104, '2026-01-01', '2026-01-01')"
        )
        assert connection.execute("SELECT role FROM users WHERE telegram_id = 104").fetchone() == (1,)
    command.downgrade(config, ASSIGNMENT_REVISION)
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM user_roles ORDER BY id").fetchall() == assignments


def test_actual_upgrade_enforces_authority_role_constraint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An Alembic-created database enforces the same role set as ORM-created databases."""
    database = tmp_path / "role-constraint.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "head")
    with (
        sqlite3.connect(database) as connection,
        pytest.raises(sqlite3.IntegrityError, match="ck_users_valid_role"),
    ):
        connection.execute(
            "INSERT INTO users (telegram_id, role, created_at, updated_at) "
            "VALUES (101, 'superadmin', '2026-01-01', '2026-01-01')"
        )


@pytest.mark.parametrize("table", ["users", "chats"])
def test_actual_upgrade_preserves_active_identity_uniqueness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, table: str
) -> None:
    """Migrated partial indexes allow history alongside exactly one active identity."""
    database = tmp_path / "active-identities.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        insert = {
            "users": (
                "INSERT INTO users (telegram_id, generation, deleted_at, created_at, updated_at) "
                "VALUES (101, ?, ?, '2026-01-01', '2026-01-01')"
            ),
            "chats": "INSERT INTO chats (chat_id, platform, status, generation, deleted_at) "
            "VALUES (-100101, 'telegram', 'approved', ?, ?)",
        }[table]
        connection.execute(insert, (1, "2026-01-02"))
        connection.execute(insert, (2, None))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            connection.execute(insert, (3, None))
        connection.execute(insert, (3, "2026-01-03"))


def test_actual_upgrade_enforces_association_foreign_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Migrated associations reject missing parents with enforcement enabled."""
    database = tmp_path / "foreign-keys.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        connection.execute(
            "INSERT INTO users (id, telegram_id, created_at, updated_at) VALUES (1, 101, '2026-01-01', '2026-01-01')"
        )
        connection.execute(
            "INSERT INTO chats (id, chat_id, platform, status) VALUES (1, -100101, 'telegram', 'approved')"
        )
        command_id = connection.execute("SELECT id FROM commands ORDER BY id LIMIT 1").fetchone()[0]
        connection.execute("INSERT INTO chat_commands (chat_id, command_id) VALUES (1, ?)", (command_id,))
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            connection.execute("INSERT INTO chat_commands (chat_id, command_id) VALUES (2, ?)", (command_id,))
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            connection.execute("INSERT INTO chat_commands (chat_id, command_id) VALUES (1, -1)")
        insert_role = (
            "INSERT INTO user_roles (user_id, telegram_id, role, created_at, updated_at) "
            "VALUES (?, 101, 'admin', '2026-01-01', '2026-01-01')"
        )
        connection.execute(insert_role, (1,))
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            connection.execute(insert_role, (2,))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            connection.execute(insert_role, (1,))
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_actual_upgrade_enforces_shared_handle_uniqueness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The actual shared registry enforces normalized handle and target uniqueness."""
    database = tmp_path / "handles.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        insert = (
            "INSERT INTO public_handles "
            "(handle_normalized, handle_display, target_kind, target_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '2026-01-01', '2026-01-01')"
        )
        connection.execute(insert, ("known_user", "Known_User", "user", 101))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            connection.execute(insert, ("known_user", "known_user", "group", -100101))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            connection.execute(insert, ("different_name", "Different_Name", "user", 101))
        connection.execute(insert, ("known_group", "Known_Group", "group", -100101))


def test_role_constraint_migration_preserves_current_state_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The corrective revision preserves current roles, tombstones and referenced history."""
    database = tmp_path / "constraint-preservation.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, ROLE_REVISION)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO users "
            "(id, telegram_id, role, generation, created_at, updated_at, deleted_at, deleted_reason) "
            "VALUES (?, 101, ?, ?, '2026-01-01', '2026-01-01', ?, ?)",
            [(1, "owner", 1, "2026-01-02", "retired"), (2, "user", 2, None, None)],
        )
        connection.execute(
            "INSERT INTO user_roles (user_id, telegram_id, role, created_at, updated_at) "
            "VALUES (1, 101, 'admin', '2026-01-01', '2026-01-01')"
        )
        users = connection.execute("SELECT * FROM users ORDER BY id").fetchall()
        history = connection.execute("SELECT * FROM user_roles ORDER BY id").fetchall()
    command.upgrade(config, CONSTRAINT_REVISION)
    command.downgrade(config, ROLE_REVISION)
    command.upgrade(config, CONSTRAINT_REVISION)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM users ORDER BY id").fetchall() == users
        assert connection.execute("SELECT * FROM user_roles ORDER BY id").fetchall() == history
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match="ck_users_valid_role"):
            connection.execute("UPDATE users SET role = 'superadmin' WHERE id = 2")
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
            connection.execute("UPDATE users SET role = NULL WHERE id = 2")


def test_role_constraint_migration_rejects_invalid_existing_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An invalid authority is never silently repaired or lost by a failed migration."""
    database = tmp_path / "invalid-role.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, ROLE_REVISION)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users (telegram_id, role, created_at, updated_at) "
            "VALUES (101, 'superadmin', '2026-01-01', '2026-01-01')"
        )
        before = connection.execute("SELECT * FROM users").fetchall()
    with pytest.raises(ValueError, match="invalid authority roles"):
        command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM users").fetchall() == before
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (ROLE_REVISION,)
        assert connection.execute("SELECT name FROM sqlite_master WHERE name LIKE '_alembic_tmp_%'").fetchall() == []


def test_integer_tier_migration_preserves_current_authority_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integer conversion is reversible without reapplying historical grants or changing audit."""
    database = tmp_path / "integer-tiers.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, CONSTRAINT_REVISION)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO users (id, telegram_id, role, generation, created_at, updated_at, deleted_at) "
            "VALUES (?, ?, ?, ?, '2026-01-01', '2026-01-01', ?)",
            [(1, 101, "user", 1, None), (2, 102, "admin", 1, None), (3, 103, "owner", 1, "2026-01-02")],
        )
        connection.execute(
            "INSERT INTO user_roles (user_id, telegram_id, role, created_at, updated_at) "
            "VALUES (1, 101, 'owner', '2026-01-01', '2026-01-01')"
        )
        connection.execute(
            "INSERT INTO audit_events (created_at, actor_origin, target_type, target_id, action, changed_fields) "
            "VALUES ('2026-01-01', 'cli', 'user_role', '101:user', 'set_role', '{\"role\": [\"owner\", \"user\"]}')"
        )
        history = connection.execute("SELECT * FROM user_roles").fetchall()
        audit = connection.execute("SELECT * FROM audit_events").fetchall()
        identity = connection.execute(
            "SELECT id, telegram_id, generation, deleted_at FROM users ORDER BY id"
        ).fetchall()
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT role, typeof(role) FROM users ORDER BY id").fetchall() == [
            (1, "integer"),
            (10, "integer"),
            (100, "integer"),
        ]
        for invalid in (0, 11, 1000, "admin"):
            with pytest.raises(sqlite3.IntegrityError, match="ck_users_valid_role"):
                connection.execute("UPDATE users SET role = ? WHERE id = 1", (invalid,))
        connection.execute(
            "INSERT INTO users (telegram_id, created_at, updated_at) VALUES (104, '2026-01-01', '2026-01-01')"
        )
        assert connection.execute("SELECT role, typeof(role) FROM users WHERE telegram_id = 104").fetchone() == (
            1,
            "integer",
        )
    command.downgrade(config, CONSTRAINT_REVISION)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT role FROM users ORDER BY id").fetchall() == [
            ("user",),
            ("admin",),
            ("owner",),
            ("user",),
        ]
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM user_roles").fetchall() == history
        assert connection.execute("SELECT * FROM audit_events").fetchall() == audit
        assert (
            connection.execute(
                "SELECT id, telegram_id, generation, deleted_at FROM users WHERE id <= 3 ORDER BY id"
            ).fetchall()
            == identity
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
