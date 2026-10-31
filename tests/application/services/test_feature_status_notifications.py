"""Outage and recovery notices target only affected scopes and respect preferences."""

import redis
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.emergency import FeatureEmergencyState
from hovorunv2.application.features.ports import CommandStore
from hovorunv2.application.features.registry import FEATURE_REGISTRY
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.feature_jobs import PayloadPort
from hovorunv2.application.operations.feature_status import FeatureAudienceStore, FeatureStatusNotifier
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
from hovorunv2.application.usage.budgets import Clock, RuntimeNamespace
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName
from hovorunv2.domain.notification_event import NotificationReceiptDB
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.feature_payloads import WorkerPayloadClient
from tests.utils.runtime import ManualClock

USER = 590001
UNRELATED = 590002
NAMESPACE = RuntimeNamespace("test:{feature-status}:")
FEATURE = str(CommandName.TWITTER_VIDEO)
OTHER_FEATURE = str(CommandName.TIKTOK_VIDEO)


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
        return 700 + len(self.calls)


async def _enable(container: AsyncContainer, telegram_id: int, command: str) -> None:
    users = await container.get(UserStore)
    commands = await container.get(CommandStore)
    await users.ingest_user(telegram_id)
    await users.set_trust(telegram_id, is_trusted=True, actor=ActorContext.cli())
    await (await container.get(ChatStore)).get_or_create_chat(telegram_id)
    assert await commands.enable_command(telegram_id, command, actor=ActorContext.telegram(telegram_id))


async def _notifier(container: AsyncContainer) -> FeatureStatusNotifier:
    return FeatureStatusNotifier(
        await container.get(FeatureAudienceStore),
        await container.get(NotificationLedgerService),
        await container.get(PayloadPort),
        FEATURE_REGISTRY,
        await container.get(Clock),
    )


def _state(
    *, disabled: bool = True, public_reason: str | None = "Provider outage", epoch: int = 1
) -> FeatureEmergencyState:
    return FeatureEmergencyState(FEATURE, disabled, "internal-only note", public_reason, epoch)


async def test_notice_targets_only_scopes_using_the_feature(test_container: AsyncContainer) -> None:
    """Affected users receive one event; unrelated feature users and internal-only stops do not."""
    await _enable(test_container, USER, FEATURE)
    await _enable(test_container, UNRELATED, OTHER_FEATURE)
    notifier = await _notifier(test_container)
    assert await notifier.publish(_state(), actor=ActorContext.cli(), transport_kind=None) == 1
    assert await notifier.publish(_state(), actor=ActorContext.cli(), transport_kind=None) == 0
    assert await notifier.publish(_state(public_reason=None), actor=ActorContext.cli(), transport_kind=None) == 0
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        receipts = (await session.scalars(select(NotificationReceiptDB))).all()
        assert len(receipts) == 1
        user = await (await test_container.get(UserStore)).get_by_telegram_id(USER)
        assert user is not None
        assert receipts[0].user_id == user.id
        assert receipts[0].status == NotificationOutcome.PENDING.value


async def test_recovery_notice_uses_public_text_only(test_container: AsyncContainer) -> None:
    """Re-enabling renders recovery text without the internal reason."""
    await _enable(test_container, USER, FEATURE)
    notifier = await _notifier(test_container)
    assert (
        await notifier.publish(
            _state(disabled=False, public_reason=None), actor=ActorContext.cli(), transport_kind=None
        )
        == 1
    )


async def test_feature_status_preference_off_skips_delivery(test_container: AsyncContainer) -> None:
    """Opting out of feature-status notices suppresses the queued delivery."""
    await _enable(test_container, USER, FEATURE)
    preferences = await test_container.get(NotificationPreferenceService)
    user = await (await test_container.get(UserStore)).get_by_telegram_id(USER)
    assert user is not None
    target = PermissionTarget("user", user.id, user.generation, USER)
    await preferences.set_preference(
        target,
        NotificationCategory.FEATURE_STATUS,
        enabled=False,
        actor=ActorContext.telegram(USER),
        transport_kind="private",
    )
    assert await preferences.resolve(target, NotificationCategory.FEATURE_STATUS) is False
    settings = await test_container.get(Settings)
    client = redis.Redis.from_url(settings.valkey_url, decode_responses=True)
    sender = RecordingSender()
    try:
        with WorkerDatabase(settings) as db:
            clock = ManualClock()
            payloads = WorkerPayloadClient(client, settings, clock, NAMESPACE, UsageSafetyCaps(), lambda: None)
            sealed = payloads.seal("feature-status.test", b"outage", clock.now() + 300)
            db.notifications.ensure_event(
                NotificationEvent(
                    "feature-status.test",
                    NotificationCategory.FEATURE_STATUS,
                    "feature_status",
                    FEATURE,
                    sealed.reference,
                ),
                actor=ActorContext.cli(),
            )
            db.notifications.record(
                "feature-status.test",
                target,
                NotificationOutcome.PENDING,
                attempts=0,
                telegram_message_id=None,
                actor=ActorContext.cli(),
            )
            delivery = NotificationDeliveryService(db.notifications, payloads, sender, clock)
            assert delivery.deliver_due() == 0
            assert sender.calls == []
    finally:
        client.close()
