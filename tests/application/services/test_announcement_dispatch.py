"""Confirmed announcement dispatch with single-use, revision-bound authorization."""

import time

import pytest
import redis
from dishka import AsyncContainer

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.operations.announcements import AnnouncementService, AnnouncementStore
from hovorunv2.application.operations.notification_delivery import NotificationDeliveryService
from hovorunv2.application.operations.notification_ledger import (
    NotificationEvent,
    NotificationLedgerService,
    NotificationOutcome,
)
from hovorunv2.application.operations.notification_preferences import (
    NotificationCategory,
    NotificationPreferenceService,
)
from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.domain.announcement import AnnouncementAudience
from hovorunv2.domain.audit import ActorContext
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.feature_payloads import WorkerPayloadClient
from tests.utils.runtime import ManualClock

AUTHOR = 610001
USER = 610002


class RecordingSender:
    """Record sends without network I/O."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
        reply_markup: str | None = None,
    ) -> int | None:
        """Record one synthetic send."""
        self.calls.append((chat_id, text))
        return 800 + len(self.calls)


async def _user(container: AsyncContainer, telegram_id: int) -> PermissionTarget:
    users = await container.get(UserStore)
    await users.ingest_user(telegram_id)
    user = await users.get_by_telegram_id(telegram_id)
    assert user is not None
    return PermissionTarget("user", user.id, user.generation, telegram_id)


async def _draft(container: AsyncContainer) -> tuple[AnnouncementService, int]:
    await _user(container, AUTHOR)
    await _user(container, USER)
    service = await container.get(AnnouncementService)
    draft = await service.create(
        author=ActorContext.telegram(AUTHOR),
        category=NotificationCategory.ANNOUNCEMENT,
        audience=AnnouncementAudience.SELECTED,
        target_ids=(USER,),
        body="Routine announcement",
    )
    return service, draft.id


async def _deliver(container: AsyncContainer, sender: RecordingSender) -> int:
    settings = await container.get(Settings)
    client = redis.Redis.from_url(settings.valkey_url, decode_responses=True)
    try:
        with WorkerDatabase(settings) as db:
            clock = ManualClock()
            payloads = WorkerPayloadClient(client, settings, clock, RuntimeNamespace(), UsageSafetyCaps(), lambda: None)
            delivery = NotificationDeliveryService(db.notifications, payloads, sender, clock)
            return delivery.deliver_due()
    finally:
        client.close()


async def test_confirm_dispatch_and_single_use(test_container: AsyncContainer) -> None:
    """Confirming binds the revision and one dispatch; reuse is rejected."""
    service, draft_id = await _draft(test_container)
    confirmation = await service.confirm(draft_id, actor=ActorContext.telegram(AUTHOR))
    assert confirmation.status == "pending"
    assert confirmation.revision == 1
    status = await service.dispatch(draft_id, confirmation.token, actor=ActorContext.telegram(AUTHOR))
    assert status.event_key == f"announcement.{draft_id}.r1"
    assert status.outcomes.get(NotificationOutcome.PENDING) == 1
    with pytest.raises(ValueError, match="no longer usable"):
        await service.dispatch(draft_id, confirmation.token, actor=ActorContext.telegram(AUTHOR))


async def test_stale_revision_cannot_dispatch(test_container: AsyncContainer) -> None:
    """Changing the draft after confirmation invalidates the old authorization."""
    service, draft_id = await _draft(test_container)
    confirmation = await service.confirm(draft_id, actor=ActorContext.telegram(AUTHOR))
    await service.update(
        draft_id,
        author=ActorContext.telegram(AUTHOR),
        category=NotificationCategory.ANNOUNCEMENT,
        audience=AnnouncementAudience.SELECTED,
        target_ids=(USER,),
        body="Changed",
    )
    with pytest.raises(ValueError, match="stale"):
        await service.dispatch(draft_id, confirmation.token, actor=ActorContext.telegram(AUTHOR))


async def test_expired_confirmation_cannot_dispatch(test_container: AsyncContainer) -> None:
    """An expired authorization is rejected before any event is created."""
    service, draft_id = await _draft(test_container)
    store = await test_container.get(AnnouncementStore)
    draft = await store.get(draft_id)
    assert draft is not None
    recipients = await store.snapshot(draft.category, draft.audience, await store.targets(draft_id))
    expired = await store.confirm(
        draft,
        actor=ActorContext.telegram(AUTHOR),
        actor_user_id=draft.author_user_id,
        recipients=recipients,
        expires_at=time.time() - 1,
    )
    with pytest.raises(ValueError, match="expired"):
        await service.dispatch(draft_id, expired.token, actor=ActorContext.telegram(AUTHOR))


async def test_opt_out_after_preview_suppresses_delivery(test_container: AsyncContainer) -> None:
    """A recipient who opts out after confirmation is skipped by the delivery worker."""
    service, draft_id = await _draft(test_container)
    user = await (await test_container.get(UserStore)).get_by_telegram_id(USER)
    assert user is not None
    confirmation = await service.confirm(draft_id, actor=ActorContext.telegram(AUTHOR))
    preferences = await test_container.get(NotificationPreferenceService)
    await preferences.set_preference(
        PermissionTarget("user", user.id, user.generation, USER),
        NotificationCategory.ANNOUNCEMENT,
        enabled=False,
        actor=ActorContext.telegram(USER),
        transport_kind="private",
    )
    await service.dispatch(draft_id, confirmation.token, actor=ActorContext.telegram(AUTHOR))
    sender = RecordingSender()
    assert await _deliver(test_container, sender) == 0
    assert sender.calls == []
    status = await service.status(draft_id)
    assert status.outcomes.get(NotificationOutcome.SKIPPED) == 1


async def test_cancel_dismisses_only_the_selected_event(test_container: AsyncContainer) -> None:
    """Cancellation dismisses this announcement's receipts and leaves other events pending."""
    service, draft_id = await _draft(test_container)
    confirmation = await service.confirm(draft_id, actor=ActorContext.telegram(AUTHOR))
    await service.dispatch(draft_id, confirmation.token, actor=ActorContext.telegram(AUTHOR))
    ledger = await test_container.get(NotificationLedgerService)
    user = await (await test_container.get(UserStore)).get_by_telegram_id(USER)
    assert user is not None
    target = PermissionTarget("user", user.id, user.generation, USER)
    await ledger.ensure_event(
        NotificationEvent("unrelated.event", NotificationCategory.ANNOUNCEMENT, "test", "unrelated"),
        actor=ActorContext.telegram(AUTHOR),
    )
    await ledger.record(
        "unrelated.event", target, NotificationOutcome.PENDING, attempts=0, actor=ActorContext.telegram(AUTHOR)
    )
    status = await service.cancel(draft_id, actor=ActorContext.telegram(AUTHOR))
    assert status.outcomes.get(NotificationOutcome.DISMISSED) == 1
    unrelated = await ledger.receipt("unrelated.event", target)
    assert unrelated is not None
    assert unrelated.outcome == NotificationOutcome.PENDING


async def test_cancel_pending_confirmation_blocks_dispatch(test_container: AsyncContainer) -> None:
    """Cancelling a pending confirmation makes its token unusable."""
    service, draft_id = await _draft(test_container)
    confirmation = await service.confirm(draft_id, actor=ActorContext.telegram(AUTHOR))
    await service.cancel(draft_id, actor=ActorContext.telegram(AUTHOR))
    with pytest.raises(ValueError, match="no longer usable"):
        await service.dispatch(draft_id, confirmation.token, actor=ActorContext.telegram(AUTHOR))
