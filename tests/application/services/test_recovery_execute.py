"""Explicit restore and fresh-generation recovery through audited transactions."""

import pytest
from dishka import AsyncContainer

from hovorunv2.application.access.ban_ports import BanKind, BanStore
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.lifecycle import IdentityLifecycleService
from hovorunv2.application.identity.ports import PublicHandleStore, UserStore
from hovorunv2.application.identity.recovery import RecoveryOption, RecoveryPreviewService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ChatStatus, Role, TargetKind
from tests.conftest import provision_role

USER = 650001
ADMIN = 650002
GROUP = -650003


async def _lifecycle(container: AsyncContainer) -> IdentityLifecycleService:
    return IdentityLifecycleService(
        await container.get(UserStore), await container.get(ChatStore), await container.get(PublicHandleStore)
    )


async def _admin(container: AsyncContainer) -> None:
    users = await container.get(UserStore)
    await users.ingest_user(ADMIN)
    await provision_role(container, ADMIN, Role.ADMIN)


async def _deleted_user(container: AsyncContainer) -> None:
    users = await container.get(UserStore)
    await users.ingest_user(USER)
    await users.set_trust(USER, is_trusted=True, actor=ActorContext.cli())
    await (await _lifecycle(container)).delete_user(USER, reason="accident", actor=ActorContext.cli())


async def test_restore_preserves_prior_state_and_bans(test_container: AsyncContainer) -> None:
    """Restore reactivates the tombstone with its role/trust while reporting an active ban."""
    users = await test_container.get(UserStore)
    await users.ingest_user(USER)
    await users.set_trust(USER, is_trusted=True, actor=ActorContext.cli())
    await (await test_container.get(BanStore)).set_ban(
        TargetKind.USER, USER, BanKind.PERSONAL, active=True, actor=ActorContext.cli(reason="restriction")
    )
    await (await _lifecycle(test_container)).delete_user(USER, reason="accident", actor=ActorContext.cli())
    await _admin(test_container)
    result = await (await test_container.get(RecoveryPreviewService)).execute_user(
        USER, RecoveryOption.RESTORE, 2, actor=ActorContext.telegram(ADMIN)
    )
    assert result.generation == 3
    assert result.banned is True
    user = await (await test_container.get(UserStore)).get_by_telegram_id(USER)
    assert user is not None
    assert user.is_trusted is True


async def test_admin_restore_requires_cli(test_container: AsyncContainer) -> None:
    """A Telegram-origin restore cannot reinstate ADMIN authority."""
    users = await test_container.get(UserStore)
    await users.ingest_user(ADMIN)
    await provision_role(test_container, ADMIN, Role.ADMIN)
    await (await _lifecycle(test_container)).delete_user(ADMIN, reason="cleanup", actor=ActorContext.cli())
    other = 650004
    await users.ingest_user(other)
    await provision_role(test_container, other, Role.ADMIN)
    service = await test_container.get(RecoveryPreviewService)
    with pytest.raises(PermissionError):
        await service.execute_user(ADMIN, RecoveryOption.RESTORE, 2, actor=ActorContext.telegram(other))
    result = await service.execute_user(ADMIN, RecoveryOption.RESTORE, 2, actor=ActorContext.cli())
    assert result.generation == 3


async def test_fresh_generation_retains_tombstone_without_old_authority(
    test_container: AsyncContainer,
) -> None:
    """Fresh recovery creates a default USER generation and keeps the tombstone."""
    await _deleted_user(test_container)
    await _admin(test_container)
    result = await (await test_container.get(RecoveryPreviewService)).execute_user(
        USER, RecoveryOption.FRESH, 2, actor=ActorContext.telegram(ADMIN)
    )
    assert result.generation == 3
    users = await test_container.get(UserStore)
    active = await users.get_by_telegram_id(USER)
    assert active is not None
    assert active.role == Role.USER
    assert active.is_trusted is False
    history = await users.get_historical_by_telegram_id(USER)
    assert len(history) == 2
    assert any(row.deleted_at is not None for row in history)


async def test_stale_revision_cannot_execute(test_container: AsyncContainer) -> None:
    """A changed deletion revision blocks the old confirmation."""
    await _deleted_user(test_container)
    await _admin(test_container)
    with pytest.raises(ValueError, match="revision changed"):
        await (await test_container.get(RecoveryPreviewService)).execute_user(
            USER, RecoveryOption.RESTORE, 1, actor=ActorContext.telegram(ADMIN)
        )


async def test_group_restore_and_fresh_generation(test_container: AsyncContainer) -> None:
    """Group restore keeps approval; fresh recovery starts unapproved without old settings."""
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(GROUP)
    await (await test_container.get(ChatStatusService)).set_status(GROUP, ChatStatus.APPROVED)
    lifecycle = await _lifecycle(test_container)
    await lifecycle.delete_group(str(GROUP), reason="cleanup", actor=ActorContext.cli())
    await _admin(test_container)
    service = await test_container.get(RecoveryPreviewService)
    restored = await service.execute_group(GROUP, RecoveryOption.RESTORE, 2, actor=ActorContext.telegram(ADMIN))
    assert restored.generation == 3
    active = await chats.get_chat(GROUP)
    assert active is not None
    assert active.status == ChatStatus.APPROVED
    await lifecycle.delete_group(str(GROUP), reason="cleanup", actor=ActorContext.cli())
    fresh = await service.execute_group(GROUP, RecoveryOption.FRESH, 4, actor=ActorContext.telegram(ADMIN))
    assert fresh.generation == 5
    fresh_chat = await chats.get_chat(GROUP)
    assert fresh_chat is not None
    assert fresh_chat.status == ChatStatus.UNAUTHORIZED
