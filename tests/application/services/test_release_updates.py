"""Release updates published as recipient events and delivered with preferences."""

import redis
from dishka import AsyncContainer

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.operations.notification_delivery import NotificationDeliveryService
from hovorunv2.application.operations.notification_preferences import (
    NotificationCategory,
    NotificationPreferenceService,
)
from hovorunv2.application.operations.release_updates import ReleaseUpdateService
from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, Role
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.feature_payloads import WorkerPayloadClient
from tests.conftest import provision_role
from tests.utils.runtime import ManualClock

GROUP = -570001
ADMIN = 570002
NAMESPACE = RuntimeNamespace("test:{release-updates}:")
START_VERSION = "0.1.0"


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
        return 600 + len(self.calls)


async def _approved_group(container: AsyncContainer) -> ChatDB:
    chats = await container.get(ChatStore)
    chat = await chats.get_or_create_chat(GROUP)
    await (await container.get(ChatStatusService)).set_status(GROUP, ChatStatus.APPROVED)
    await provision_role(container, ADMIN, Role.ADMIN)
    return chat


def _group_target(chat: ChatDB) -> PermissionTarget:
    return PermissionTarget("group", chat.id, chat.generation, chat.chat_id)


def _services(
    db: WorkerDatabase, client: redis.Redis, settings: Settings, sender: RecordingSender
) -> tuple[ReleaseUpdateService, NotificationDeliveryService]:
    clock = ManualClock()
    payloads = WorkerPayloadClient(client, settings, clock, NAMESPACE, UsageSafetyCaps(), lambda: None)
    release = ReleaseUpdateService(db.operations, db.notifications, payloads, settings, clock)
    delivery = NotificationDeliveryService(db.notifications, payloads, sender, clock)
    return release, delivery


async def test_same_version_restart_does_not_requeue_or_resend(test_container: AsyncContainer) -> None:
    """A restarted worker sees the advanced marker and sends nothing again."""
    await _approved_group(test_container)
    settings = await test_container.get(Settings)
    client = redis.Redis.from_url(settings.valkey_url, decode_responses=True)
    sender = RecordingSender()
    try:
        with WorkerDatabase(settings) as db:
            release, delivery = _services(db, client, settings, sender)
            db.operations.set_last_notified_version(START_VERSION)
            assert release.publish() >= 1
            assert delivery.deliver_due() == 1
            assert len(sender.calls) == 1
            assert release.publish() == 0
            assert delivery.deliver_due() == 0
            assert len(sender.calls) == 1
    finally:
        client.close()


async def test_release_preference_off_suppresses_only_release_delivery(
    test_container: AsyncContainer,
) -> None:
    """Disabling release notices skips delivery while other categories stay enabled."""
    chat = await _approved_group(test_container)
    settings = await test_container.get(Settings)
    preferences = await test_container.get(NotificationPreferenceService)
    group_target = _group_target(chat)
    await preferences.set_preference(
        group_target,
        NotificationCategory.RELEASE,
        enabled=False,
        actor=ActorContext.telegram(ADMIN),
        transport_kind="private",
    )
    assert await preferences.resolve(group_target, NotificationCategory.RELEASE) is False
    assert await preferences.resolve(group_target, NotificationCategory.BETA) is True
    client = redis.Redis.from_url(settings.valkey_url, decode_responses=True)
    sender = RecordingSender()
    try:
        with WorkerDatabase(settings) as db:
            release, delivery = _services(db, client, settings, sender)
            db.operations.set_last_notified_version(START_VERSION)
            assert release.publish() >= 1
            assert delivery.deliver_due() == 0
            assert sender.calls == []
    finally:
        client.close()


async def test_existing_group_access_survives_publication(test_container: AsyncContainer) -> None:
    """Publishing a release event never changes group approval or generation."""
    chat = await _approved_group(test_container)
    settings = await test_container.get(Settings)
    client = redis.Redis.from_url(settings.valkey_url, decode_responses=True)
    try:
        with WorkerDatabase(settings) as db:
            release, _delivery = _services(db, client, settings, RecordingSender())
            db.operations.set_last_notified_version(START_VERSION)
            release.publish()
        chats = await test_container.get(ChatStore)
        current = await chats.get_chat(GROUP)
        assert current is not None
        assert current.status == ChatStatus.APPROVED
        assert current.generation == chat.generation
    finally:
        client.close()
