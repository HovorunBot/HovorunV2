"""Encrypted announcement drafts with revisioned, preference-aware previews."""

import pytest
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.operations.announcements import AnnouncementService
from hovorunv2.application.operations.notification_preferences import (
    NotificationCategory,
    NotificationPreferenceService,
)
from hovorunv2.domain.announcement import AnnouncementAudience, AnnouncementDraftDB
from hovorunv2.domain.audit import ActorContext, AuditEventDB

AUTHOR = 600001
USER = 600002
OTHER = 600003


async def _user(container: AsyncContainer, telegram_id: int) -> PermissionTarget:
    users = await container.get(UserStore)
    await users.ingest_user(telegram_id)
    user = await users.get_by_telegram_id(telegram_id)
    assert user is not None
    return PermissionTarget("user", user.id, user.generation, telegram_id)


async def test_draft_body_is_encrypted_and_preview_follows_preferences(
    test_container: AsyncContainer,
) -> None:
    """Plaintext stays out of the column and audit; opting out reduces the preview."""
    await _user(test_container, AUTHOR)
    user = await _user(test_container, USER)
    service = await test_container.get(AnnouncementService)
    draft = await service.create(
        author=ActorContext.telegram(AUTHOR),
        category=NotificationCategory.ANNOUNCEMENT,
        audience=AnnouncementAudience.ALL,
        target_ids=(),
        body="Hello secret body",
    )
    assert draft.revision == 1
    assert draft.preview_count >= 2
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        row = await session.get(AnnouncementDraftDB, draft.id)
        assert row is not None
        assert "Hello secret body" not in row.body_ciphertext
        audits = (await session.scalars(select(AuditEventDB))).all()
        assert audits
        assert all("Hello secret body" not in str(audit.changed_fields) for audit in audits)
    assert (await service.show(draft.id))[1] == "Hello secret body"
    preferences = await test_container.get(NotificationPreferenceService)
    await preferences.set_preference(
        user,
        NotificationCategory.ANNOUNCEMENT,
        enabled=False,
        actor=ActorContext.telegram(USER),
        transport_kind="private",
    )
    preview = await service.preview(draft.id)
    assert preview.revision == 1
    assert preview.users == draft.preview_count - 1


async def test_update_replaces_selected_targets_and_increments_revision(
    test_container: AsyncContainer,
) -> None:
    """Changing content and audience advances the revision and invalidates the old preview."""
    await _user(test_container, AUTHOR)
    user = await _user(test_container, USER)
    await _user(test_container, OTHER)
    service = await test_container.get(AnnouncementService)
    draft = await service.create(
        author=ActorContext.telegram(AUTHOR),
        category=NotificationCategory.ANNOUNCEMENT,
        audience=AnnouncementAudience.SELECTED,
        target_ids=(USER,),
        body="first body",
    )
    assert draft.preview_count == 1
    updated = await service.update(
        draft.id,
        author=ActorContext.telegram(AUTHOR),
        category=NotificationCategory.ANNOUNCEMENT,
        audience=AnnouncementAudience.SELECTED,
        target_ids=(OTHER,),
        body="second body",
    )
    assert updated.revision == 2
    assert updated.preview_count == 1
    assert (await service.show(draft.id))[1] == "second body"
    preview = await service.preview(draft.id)
    assert preview.revision == 2
    assert user.entity_id is not None


async def test_unknown_selected_target_is_rejected(test_container: AsyncContainer) -> None:
    """Selected audiences fail closed when an internal identity cannot be resolved."""
    await _user(test_container, AUTHOR)
    service = await test_container.get(AnnouncementService)
    with pytest.raises(ValueError, match="does not exist"):
        await service.create(
            author=ActorContext.telegram(AUTHOR),
            category=NotificationCategory.ANNOUNCEMENT,
            audience=AnnouncementAudience.SELECTED,
            target_ids=(999999,),
            body="body",
        )
