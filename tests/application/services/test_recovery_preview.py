"""Recovery previews expose facts and both options without selecting a mode."""

from datetime import UTC, datetime, timedelta

import pytest
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.lifecycle import IdentityLifecycleService
from hovorunv2.application.identity.ports import PublicHandleStore, UserStore
from hovorunv2.application.identity.recovery import RecoveryOption, RecoveryPreviewService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ChatStatus, Platform, Role
from hovorunv2.domain.user import UserDB
from tests.conftest import provision_role

USER = 640001
ADMIN = 640002
GROUP = -640003


async def _service(container: AsyncContainer) -> IdentityLifecycleService:
    return IdentityLifecycleService(
        await container.get(UserStore), await container.get(ChatStore), await container.get(PublicHandleStore)
    )


async def _admin(container: AsyncContainer) -> None:
    users = await container.get(UserStore)
    await users.ingest_user(ADMIN)
    await provision_role(container, ADMIN, Role.ADMIN)


async def test_user_preview_recommends_but_never_selects(test_container: AsyncContainer) -> None:
    """A recent deletion shows both options and a restore recommendation only."""
    users = await test_container.get(UserStore)
    await users.ingest_user(USER)
    await (await _service(test_container)).delete_user(str(USER), reason="accident", actor=ActorContext.cli())
    await _admin(test_container)
    preview = await (await test_container.get(RecoveryPreviewService)).preview_user(
        USER, actor=ActorContext.telegram(ADMIN)
    )
    assert preview.generation == 2
    assert preview.options == (RecoveryOption.RESTORE, RecoveryOption.FRESH)
    assert preview.recommended == RecoveryOption.RESTORE
    assert preview.collision is False
    assert preview.role_restore_requires_cli is False
    assert preview.dependants


async def test_preview_requires_current_bot_admin(test_container: AsyncContainer) -> None:
    """A non-administrator cannot inspect a deleted private record."""
    users = await test_container.get(UserStore)
    await users.ingest_user(USER)
    await users.ingest_user(ADMIN)
    await (await _service(test_container)).delete_user(str(USER), reason="accident", actor=ActorContext.cli())
    service = await test_container.get(RecoveryPreviewService)
    with pytest.raises(PermissionError):
        await service.preview_user(USER, actor=ActorContext.telegram(ADMIN))


async def test_old_deletion_and_protected_role_warnings(test_container: AsyncContainer) -> None:
    """Age changes only the recommendation; a deleted admin warns that role restoration needs CLI."""
    users = await test_container.get(UserStore)
    await users.ingest_user(USER)
    await (await _service(test_container)).delete_user(str(USER), reason="old accident", actor=ActorContext.cli())
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        row = await session.scalar(select(UserDB).where(UserDB.telegram_id == USER))
        assert row is not None
        row.deleted_at = datetime.now(UTC) - timedelta(days=2)
        await session.commit()
    await _admin(test_container)
    service = await test_container.get(RecoveryPreviewService)
    preview = await service.preview_user(USER, actor=ActorContext.telegram(ADMIN))
    assert preview.recommended == RecoveryOption.FRESH
    assert preview.options == (RecoveryOption.RESTORE, RecoveryOption.FRESH)
    await users.ingest_user(ADMIN)
    await provision_role(test_container, ADMIN, Role.ADMIN)
    await (await _service(test_container)).delete_user(str(ADMIN), reason="admin cleanup", actor=ActorContext.cli())
    admin_preview = await service.preview_user(ADMIN, actor=ActorContext.cli())
    assert admin_preview.role_restore_requires_cli is True


async def test_collision_reflects_an_active_fresh_identity(test_container: AsyncContainer) -> None:
    """A fresh active identity at the same ID is reported as a collision."""
    users = await test_container.get(UserStore)
    await users.ingest_user(USER)
    await (await _service(test_container)).delete_user(str(USER), reason="accident", actor=ActorContext.cli())
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        session.add(
            UserDB(
                telegram_id=USER,
                platform=Platform.TELEGRAM,
                role=Role.USER,
                generation=3,
            )
        )
        await session.commit()
    await _admin(test_container)
    preview = await (await test_container.get(RecoveryPreviewService)).preview_user(
        USER, actor=ActorContext.telegram(ADMIN)
    )
    assert preview.collision is True


async def test_group_preview_reports_prior_approval(test_container: AsyncContainer) -> None:
    """A deleted approved group keeps its prior status and shows both options."""
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(GROUP)
    await (await test_container.get(ChatStatusService)).set_status(GROUP, ChatStatus.APPROVED)
    await (await _service(test_container)).delete_group(str(GROUP), reason="group cleanup", actor=ActorContext.cli())
    await _admin(test_container)
    preview = await (await test_container.get(RecoveryPreviewService)).preview_group(
        GROUP, actor=ActorContext.telegram(ADMIN)
    )
    assert preview.status == ChatStatus.APPROVED
    assert preview.options == (RecoveryOption.RESTORE, RecoveryOption.FRESH)
    assert preview.collision is False
    assert preview.dependants
