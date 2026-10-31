"""Real SQLite trust authorization, revision and audit coverage."""

import pytest
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.access.trust_service import TrustService
from hovorunv2.application.ports import PublicHandleStore, UserStore
from hovorunv2.bootstrap.admin import run_admin
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.enums import Role, TargetKind
from hovorunv2.interface.cli.roles import RoleArguments, build_parser
from tests.conftest import provision_role
from tests.utils.postgres import reject_audit


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


@pytest.mark.fresh_schema
async def test_trust_audit_failure_rolls_back_revision(test_container: AsyncContainer) -> None:
    """A rejected audit insert rolls back both trust and its revision."""
    users = await test_container.get(UserStore)
    trust = await test_container.get(TrustService)
    await users.ingest_user(880017)
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        await reject_audit(session, "reject_trust_audit", "NEW.action = 'set_trust'", "synthetic audit failure")
        await session.commit()
    with pytest.raises(IntegrityError):
        await trust.set_trust("880017", is_trusted=True, actor=ActorContext.cli())
    user = await users.get_by_telegram_id(880017)
    assert user is not None
    assert not user.is_trusted
    assert user.trust_revision == 0


async def test_trust_cli_uses_offline_database(test_container: AsyncContainer, monkeypatch: pytest.MonkeyPatch) -> None:
    """Trust CLI uses PostgreSQL without bot credentials and preserves idempotent mutations."""
    users = await test_container.get(UserStore)
    await users.ingest_user(880018)
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("CACHE_ENCRYPTION_KEY", "")
    arguments = build_parser().parse_args(
        ["trust", "grant", "880018", "--reason", "synthetic approval"], namespace=RoleArguments()
    )
    assert await run_admin(arguments) == "880018\ttrusted=true"
    assert await run_admin(arguments) == "880018\ttrusted=true"
    user = await users.get_by_telegram_id(880018)
    assert user.is_trusted
    assert user.trust_revision == 1
    async with (await test_container.get(async_sessionmaker))() as session:
        events = list((await session.scalars(select(AuditEventDB).where(AuditEventDB.action == "set_trust"))).all())
        assert len(events) == 1
