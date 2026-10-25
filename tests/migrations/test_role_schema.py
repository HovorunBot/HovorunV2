"""Historical role schema constraints, separate from real Alembic upgrade tests."""

import sqlite3
from pathlib import Path

import pytest


def test_legacy_role_assignment_schema_starts_empty(tmp_path: Path) -> None:
    """Migration a1b2c3d4e5f6 creates the user_roles table completely empty."""
    db_file = tmp_path / "test_roles_migration.db"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()

    # Pre-create users table
    cur.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform VARCHAR(32) DEFAULT 'telegram' NOT NULL,
            telegram_id BIGINT NOT NULL,
            is_trusted BOOLEAN DEFAULT 0 NOT NULL,
            generation INTEGER DEFAULT 1 NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            deleted_at DATETIME,
            deleted_by_actor_id BIGINT,
            deleted_reason VARCHAR
        )
        """
    )
    # Execute migration table creation
    cur.execute(
        """
        CREATE TABLE user_roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            telegram_id BIGINT NOT NULL,
            role VARCHAR(32) NOT NULL,
            platform VARCHAR(32) DEFAULT 'telegram' NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
        """
    )
    cur.execute("CREATE UNIQUE INDEX uq_user_roles_assignment ON user_roles (platform, user_id, role)")

    # Assert empty table initially
    count = cur.execute("SELECT COUNT(*) FROM user_roles").fetchone()[0]
    assert count == 0

    # Uniqueness constraint enforcement
    cur.execute("INSERT INTO users (id, telegram_id, created_at, updated_at) VALUES (1, 12345, '2026', '2026')")
    cur.execute(
        "INSERT INTO user_roles (user_id, telegram_id, role, created_at, updated_at) "
        "VALUES (1, 12345, 'admin', '2026', '2026')"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        cur.execute(
            "INSERT INTO user_roles (user_id, telegram_id, role, created_at, updated_at) "
            "VALUES (1, 12345, 'admin', '2026', '2026')"
        )

    conn.close()


def test_legacy_role_column_schema_defaults_and_precedence(tmp_path: Path) -> None:
    """Migration b2c3d4e5f6a7 adds role column defaulting to 'user' and honors owner precedence."""
    db_file = tmp_path / "role_column_schema.db"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()

    # Create users and user_roles as of revision a1b2c3d4e5f6
    cur.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform VARCHAR(32) DEFAULT 'telegram' NOT NULL,
            telegram_id BIGINT NOT NULL,
            is_trusted BOOLEAN DEFAULT 0 NOT NULL,
            generation INTEGER DEFAULT 1 NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            deleted_at DATETIME,
            deleted_by_actor_id BIGINT,
            deleted_reason VARCHAR
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE user_roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            telegram_id BIGINT NOT NULL,
            role VARCHAR(32) NOT NULL,
            platform VARCHAR(32) DEFAULT 'telegram' NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
        """
    )
    # 1. User with no role assignment
    cur.execute("INSERT INTO users (id, telegram_id, created_at, updated_at) VALUES (1, 1001, '2026', '2026')")
    # 2. User with admin assignment
    cur.execute("INSERT INTO users (id, telegram_id, created_at, updated_at) VALUES (2, 1002, '2026', '2026')")
    cur.execute(
        "INSERT INTO user_roles (user_id, telegram_id, role, created_at, updated_at) "
        "VALUES (2, 1002, 'admin', '2026', '2026')"
    )
    # 3. User with owner assignment
    cur.execute("INSERT INTO users (id, telegram_id, created_at, updated_at) VALUES (3, 1003, '2026', '2026')")
    cur.execute(
        "INSERT INTO user_roles (user_id, telegram_id, role, created_at, updated_at) "
        "VALUES (3, 1003, 'owner', '2026', '2026')"
    )
    # 4. User with both admin and owner assignments (precedence test)
    cur.execute("INSERT INTO users (id, telegram_id, created_at, updated_at) VALUES (4, 1004, '2026', '2026')")
    cur.execute(
        "INSERT INTO user_roles (user_id, telegram_id, role, created_at, updated_at) "
        "VALUES (4, 1004, 'admin', '2026', '2026')"
    )
    cur.execute(
        "INSERT INTO user_roles (user_id, telegram_id, role, created_at, updated_at) "
        "VALUES (4, 1004, 'owner', '2026', '2026')"
    )
    # 5. Soft-deleted user generation with admin assignment
    cur.execute(
        "INSERT INTO users (id, telegram_id, created_at, updated_at, deleted_at) "
        "VALUES (5, 1005, '2026', '2026', '2026-01-01')"
    )
    cur.execute(
        "INSERT INTO user_roles (user_id, telegram_id, role, created_at, updated_at) "
        "VALUES (5, 1005, 'admin', '2026', '2026')"
    )
    conn.commit()

    # Apply migration logic:
    cur.execute("ALTER TABLE users ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'user'")
    cur.execute("UPDATE users SET role = 'owner' WHERE id IN (SELECT user_id FROM user_roles WHERE role = 'owner')")
    cur.execute(
        "UPDATE users SET role = 'admin' "
        "WHERE id IN (SELECT user_id FROM user_roles WHERE role = 'admin') AND role != 'owner'"
    )
    conn.commit()

    rows = {r[0]: (r[1], r[2]) for r in cur.execute("SELECT id, role, deleted_at FROM users").fetchall()}
    conn.close()

    assert rows[1] == ("user", None)
    assert rows[2] == ("admin", None)
    assert rows[3] == ("owner", None)
    assert rows[4] == ("owner", None)  # Owner precedence over admin!
    assert rows[5] == ("admin", "2026-01-01")  # Preserved deleted timestamp!
