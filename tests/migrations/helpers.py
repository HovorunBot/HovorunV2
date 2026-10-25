"""Shared configuration and stable historical revision identifiers."""

from pathlib import Path

import pytest
from alembic.config import Config

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
    return Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
