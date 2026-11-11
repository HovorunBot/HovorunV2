"""Lifecycle deletion removes current authority while retaining tombstones and history."""

import pytest
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.lifecycle import IdentityLifecycleService
from hovorunv2.application.identity.ports import PublicHandleStore, UserStore
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.enums import ChatStatus, Platform, Role, TargetKind
from hovorunv2.domain.user import UserDB
from tests.conftest import provision_role

USER = 620001
ADMIN = 620002
GROUP = -620003


async def _service(container: AsyncContainer) -> IdentityLifecycleService:
    return IdentityLifecycleService(
        await container.get(UserStore),
        await container.get(ChatStore),
        await container.get(PublicHandleStore),
    )


async def test_delete_user_tombstones_and_invalidates_generation(
    test_container: AsyncContainer,
) -> None:
    """The user disappears from active lookups, ingestion cannot resurrect it, and generation advances."""
    users = await test_container.get(UserStore)
    await users.ingest_user(USER)
    user = await users.get_by_telegram_id(USER)
    assert user is not None
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(GROUP)
    await (await test_container.get(ChatStatusService)).set_status(GROUP, ChatStatus.APPROVED)
    service = await _service(test_container)
    assert "deleted" in await service.delete_user(str(USER), reason="test cleanup", actor=ActorContext.cli())
    assert await users.get_by_telegram_id(USER) is None
    assert await users.ingest_user(USER) is None
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        row = await session.scalar(select(UserDB).where(UserDB.telegram_id == USER))
        assert row is not None
        assert row.deleted_at is not None
        assert row.generation == 2
        audits = (await session.scalars(select(AuditEventDB))).all()
        assert any(audit.target_type == "user" and audit.action == "delete" for audit in audits)
    group = await chats.get_chat(GROUP)
    assert group is not None
    assert group.status == ChatStatus.APPROVED


async def test_protected_admin_requires_cli(test_container: AsyncContainer) -> None:
    """A Telegram-origin deletion cannot remove ADMIN authority; the CLI can."""
    users = await test_container.get(UserStore)
    await users.ingest_user(ADMIN)
    await provision_role(test_container, ADMIN, Role.ADMIN)
    service = await _service(test_container)
    with pytest.raises(PermissionError):
        await service.delete_user(str(ADMIN), reason="telegram attempt", actor=ActorContext.telegram(ADMIN))
    assert "deleted" in await service.delete_user(str(ADMIN), reason="cli cleanup", actor=ActorContext.cli())


async def test_handle_mapping_cannot_alias_deleted_target(test_container: AsyncContainer) -> None:
    """A retained handle mapping is not an authorization alias after deletion."""
    users = await test_container.get(UserStore)
    await users.ingest_user(USER)
    user = await users.get_by_telegram_id(USER)
    assert user is not None
    handles = await test_container.get(PublicHandleStore)
    resolved = await handles.sync_handle(TargetKind.USER, USER, "lifecycle_alias", ActorContext.cli())
    assert resolved is not None
    assert await handles.get_handle_by_target(TargetKind.USER, USER) is not None
    service = await _service(test_container)
    await service.delete_user(str(USER), reason="cleanup", actor=ActorContext.cli())
    assert await handles.get_handle_by_target(TargetKind.USER, USER) is None
    assert await handles.resolve_handle("lifecycle_alias") is None


async def test_delete_group_advances_generation(test_container: AsyncContainer) -> None:
    """Group deletion tombstones the route while leaving prior approval history untouched."""
    chats = await test_container.get(ChatStore)
    chat = await chats.get_or_create_chat(GROUP)
    await (await test_container.get(ChatStatusService)).set_status(GROUP, ChatStatus.APPROVED)
    service = await _service(test_container)
    assert "deleted" in await service.delete_group(str(GROUP), reason="group cleanup", actor=ActorContext.cli())
    assert await chats.get_chat(GROUP) is None
    history = await chats.get_historical_by_chat_id(GROUP, Platform.TELEGRAM.value)
    assert history
    assert history[-1].generation == chat.generation + 1
    assert history[-1].status == ChatStatus.APPROVED
