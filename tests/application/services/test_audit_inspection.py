"""Bounded, redacted audit inspection for authorized administrators."""

import pytest
from dishka import AsyncContainer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.lifecycle import IdentityLifecycleService
from hovorunv2.application.identity.ports import PublicHandleStore, UserStore
from hovorunv2.application.operations.audit_inspection import MAX_AUDIT_PAGE, AuditInspectionService
from hovorunv2.domain.audit import ActorContext, ActorOrigin, AuditEventDB
from hovorunv2.domain.enums import Role
from tests.conftest import provision_role

USER = 660001
ADMIN = 660002


async def _admin(container: AsyncContainer) -> None:
    users = await container.get(UserStore)
    await users.ingest_user(ADMIN)
    await provision_role(container, ADMIN, Role.ADMIN)


async def test_pagination_is_stable_and_fields_are_permitted(test_container: AsyncContainer) -> None:
    """Two trust changes page by keyset and expose only schema-permitted fields."""
    users = await test_container.get(UserStore)
    await users.ingest_user(USER)
    await _admin(test_container)
    await users.set_trust(USER, is_trusted=True, actor=ActorContext.telegram(ADMIN))
    await users.set_trust(USER, is_trusted=False, actor=ActorContext.telegram(ADMIN))
    service = await test_container.get(AuditInspectionService)
    first = await service.query(actor=ActorContext.telegram(ADMIN), target_type="user", action="set_trust", limit=1)
    assert len(first.entries) == 1
    assert first.next_cursor is not None
    assert set(first.entries[0].fields) <= {"is_trusted", "trust_revision"}
    second = await service.query(
        actor=ActorContext.telegram(ADMIN),
        target_type="user",
        action="set_trust",
        limit=1,
        before=first.next_cursor,
    )
    assert len(second.entries) == 1
    assert second.entries[0].id != first.entries[0].id
    bounded = await service.query(actor=ActorContext.telegram(ADMIN), limit=1000)
    assert len(bounded.entries) <= MAX_AUDIT_PAGE


async def test_ordinary_user_cannot_inspect(test_container: AsyncContainer) -> None:
    """A non-administrator is denied audit inspection."""
    users = await test_container.get(UserStore)
    await users.ingest_user(USER)
    await users.ingest_user(ADMIN)
    service = await test_container.get(AuditInspectionService)
    with pytest.raises(PermissionError):
        await service.query(actor=ActorContext.telegram(ADMIN), target_type="user")


async def test_deleted_entity_history_remains_and_secrets_are_filtered(
    test_container: AsyncContainer,
) -> None:
    """Deletion history stays inspectable, and unrelated keys in stored fields are hidden."""
    users = await test_container.get(UserStore)
    await users.ingest_user(USER)
    await _admin(test_container)
    lifecycle = IdentityLifecycleService(
        users,
        await test_container.get(ChatStore),
        await test_container.get(PublicHandleStore),
    )
    await lifecycle.delete_user(USER, reason="cleanup", actor=ActorContext.cli())
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        session.add(
            AuditEventDB(
                actor_origin=ActorOrigin.CLI,
                target_type="user",
                target_id=str(USER),
                action="set_trust",
                changed_fields={"is_trusted": [False, True], "secret": "must-not-leak"},
            )
        )
        await session.commit()
        before = await session.scalar(select(func.count()).select_from(AuditEventDB))
    service = await test_container.get(AuditInspectionService)
    page = await service.query(actor=ActorContext.telegram(ADMIN), target_type="user", action="delete")
    assert page.entries
    assert any(entry.target_id == str(USER) for entry in page.entries)
    trust = await service.query(actor=ActorContext.telegram(ADMIN), target_type="user", action="set_trust")
    assert all("secret" not in entry.fields for entry in trust.entries)
    async with session_maker() as session:
        after = await session.scalar(select(func.count()).select_from(AuditEventDB))
    assert after == before
