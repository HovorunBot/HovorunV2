"""Real SQLite trust authorization, revision and audit coverage."""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from dishka import AsyncContainer
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from alembic import command
from hovorunv2.application.ports import PublicHandleStore, UserStore
from hovorunv2.application.services.trust_service import TrustService
from hovorunv2.bootstrap.admin import run_admin
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.enums import Role, TargetKind
from hovorunv2.interface.cli.roles import RoleArguments, build_parser
from tests.conftest import provision_role
from tests.test_identity_migrations import TIER_REVISION, migration_config


@pytest.mark.asyncio
async def test_trust_revisions_authority_and_idempotence(test_container: AsyncContainer) -> None:
    """Trust alone cannot mutate trust; current admin roles can, with atomic revisions."""
    users = await test_container.get(UserStore)
    trust = await test_container.get(TrustService)
    target_id = 880013
    await users.ingest_user(target_id)
    assert await trust.set_trust(str(target_id), is_trusted=True, actor=ActorContext.cli())
    target = await users.get_by_telegram_id(target_id)
    assert target is not None
    assert target.is_trusted
    assert target.trust_revision == 1
    assert await trust.set_trust(str(target_id), is_trusted=True, actor=ActorContext.cli())
    with pytest.raises(PermissionError):
        await trust.set_trust(
            str(target_id), is_trusted=False, actor=ActorContext.telegram(target_id), transport_kind="private"
        )
    admin_id = await provision_role(test_container, user_id=880014, role=Role.ADMIN)
    with pytest.raises(PermissionError):
        await trust.set_trust(
            str(target_id), is_trusted=False, actor=ActorContext.telegram(admin_id), transport_kind="group"
        )
    assert await trust.set_trust(
        str(target_id), is_trusted=False, actor=ActorContext.telegram(admin_id), transport_kind="private"
    )
    target = await users.get_by_telegram_id(target_id)
    assert target is not None
    assert not target.is_trusted
    assert target.trust_revision == 2
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        events = list(
            (
                await session.scalars(
                    select(AuditEventDB)
                    .where(AuditEventDB.action == "set_trust", AuditEventDB.target_id == str(target_id))
                    .order_by(AuditEventDB.id)
                )
            ).all()
        )
    assert len(events) == 2
    assert events[0].changed_fields == {"is_trusted": [False, True], "trust_revision": [0, 1]}
    assert events[1].changed_fields == {"is_trusted": [True, False], "trust_revision": [1, 2]}
    assert events[1].actor_id == admin_id


@pytest.mark.asyncio
async def test_trust_resolves_active_handles_and_rejects_deleted_users(test_container: AsyncContainer) -> None:
    """Handle reassignment cannot transfer trust; deleted or missing targets remain unavailable."""
    users = await test_container.get(UserStore)
    handles = await test_container.get(PublicHandleStore)
    trust = await test_container.get(TrustService)
    first, second = 880015, 880016
    await users.ingest_user(first)
    await users.ingest_user(second)
    await handles.sync_handle(TargetKind.USER, first, "trust_person", actor=ActorContext.system())
    assert await trust.set_trust("@trust_person", is_trusted=True, actor=ActorContext.cli())
    await handles.release_handle(TargetKind.USER, first, actor=ActorContext.system())
    await handles.sync_handle(TargetKind.USER, second, "trust_person", actor=ActorContext.system())
    original = await users.get_by_telegram_id(first)
    replacement = await users.get_by_telegram_id(second)
    assert original is not None
    assert original.is_trusted
    assert replacement is not None
    assert not replacement.is_trusted
    await users.soft_delete_user(second, actor=ActorContext.cli(), reason="synthetic cleanup")
    assert not await trust.set_trust(str(second), is_trusted=True, actor=ActorContext.cli())
    assert not await trust.set_trust("880099", is_trusted=True, actor=ActorContext.cli())


def test_trust_revision_upgrade_preserves_existing_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Upgrade a real pre-trust database without changing trust or authority."""
    database = tmp_path / "trust.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, TIER_REVISION)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users (platform, telegram_id, role, is_trusted, generation, created_at, updated_at) "
            "VALUES ('telegram', 880013, 10, 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    command.upgrade(config, "trust013")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT role, is_trusted, trust_revision FROM users").fetchone() == (10, 1, 0)
    command.downgrade(config, TIER_REVISION)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT role, is_trusted FROM users").fetchone() == (10, 1)


@pytest.mark.asyncio
async def test_trust_audit_failure_rolls_back_revision(test_container: AsyncContainer) -> None:
    """A rejected audit insert rolls back both trust and its revision."""
    users = await test_container.get(UserStore)
    trust = await test_container.get(TrustService)
    await users.ingest_user(880017)
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        await session.execute(
            text(
                "CREATE TRIGGER reject_trust_audit BEFORE INSERT ON audit_events "
                "WHEN NEW.action = 'set_trust' BEGIN SELECT RAISE(ABORT, 'synthetic audit failure'); END"
            )
        )
        await session.commit()
    with pytest.raises(IntegrityError):
        await trust.set_trust("880017", is_trusted=True, actor=ActorContext.cli())
    user = await users.get_by_telegram_id(880017)
    assert user is not None
    assert not user.is_trusted
    assert user.trust_revision == 0


def test_trust_cli_uses_offline_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Trust CLI runs without bot credentials and preserves idempotent mutation semantics."""
    database = tmp_path / "trust-cli.db"
    config = migration_config(monkeypatch, database)
    command.upgrade(config, "trust013")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users (platform, telegram_id, role, is_trusted, generation, created_at, updated_at) "
            "VALUES ('telegram', 880018, 1, 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    parser = build_parser()
    arguments = parser.parse_args(
        ["trust", "grant", "880018", "--reason", "synthetic approval"], namespace=RoleArguments()
    )
    assert asyncio.run(run_admin(arguments)) == "880018\ttrusted=true"
    assert asyncio.run(run_admin(arguments)) == "880018\ttrusted=true"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT is_trusted, trust_revision FROM users").fetchone() == (1, 1)
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action = 'set_trust'").fetchone() == (1,)
