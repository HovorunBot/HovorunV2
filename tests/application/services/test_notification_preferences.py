"""Category-specific notification preferences with enabled defaults against real PostgreSQL."""

import pytest
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.operations.notification_preferences import (
    NotificationCategory,
    NotificationPreferenceService,
)
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from tests.conftest import provision_role
from tests.utils.usage import USER, usage_subjects

ADMIN = 530001


async def _service(container: AsyncContainer) -> NotificationPreferenceService:
    await usage_subjects(container)
    await provision_role(container, ADMIN)
    return await container.get(NotificationPreferenceService)


async def test_defaults_are_enabled_and_one_toggle_leaves_others_unchanged(
    test_container: AsyncContainer,
) -> None:
    """Absence means enabled; toggling one category never changes another."""
    service = await _service(test_container)
    own = await service.own_target(USER)
    defaults = await service.preferences(own)
    assert [preference.enabled for preference in defaults] == [True, True, True, True]
    assert await service.set_preference(
        own, NotificationCategory.RELEASE, enabled=False, actor=ActorContext.telegram(USER), transport_kind="private"
    )
    resolved = {preference.category: preference.enabled for preference in await service.preferences(own)}
    assert resolved[NotificationCategory.RELEASE] is False
    assert all(resolved[category] for category in NotificationCategory if category != NotificationCategory.RELEASE)


async def test_group_mutation_requires_current_group_or_bot_admin_authority(
    test_container: AsyncContainer,
) -> None:
    """Ordinary members cannot change group preferences; a current bot administrator can."""
    service = await _service(test_container)
    _user, group = await usage_subjects(test_container)
    with pytest.raises(PermissionError):
        await service.set_preference(
            group.target,
            NotificationCategory.BETA,
            enabled=False,
            actor=ActorContext.telegram(USER),
            transport_kind="group",
        )
    assert await service.set_preference(
        group.target,
        NotificationCategory.BETA,
        enabled=False,
        actor=ActorContext.telegram(ADMIN),
        transport_kind="group",
    )
    assert await service.resolve(group.target, NotificationCategory.BETA) is False
    assert await service.resolve(group.target, NotificationCategory.RELEASE) is True


async def test_toggle_is_audited_and_disappears_after_subject_deletion(
    test_container: AsyncContainer,
) -> None:
    """The mutation carries one audit event; a deleted generation falls back to defaults."""
    service = await _service(test_container)
    own = await service.own_target(USER)
    await service.set_preference(
        own,
        NotificationCategory.ANNOUNCEMENT,
        enabled=False,
        actor=ActorContext.telegram(USER),
        transport_kind="private",
    )
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        audit = await session.scalar(select(AuditEventDB).where(AuditEventDB.target_type == "notification_preference"))
        assert audit is not None
        assert audit.changed_fields["enabled"] == [True, False]
        assert audit.changed_fields["category"] == NotificationCategory.ANNOUNCEMENT.value
    await (await test_container.get(UserStore)).soft_delete_user(
        USER, actor=ActorContext.cli(), reason="synthetic lifecycle change"
    )
    assert [preference.enabled for preference in await service.preferences(own)] == [True, True, True, True]
