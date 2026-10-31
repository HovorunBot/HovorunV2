"""Native notification delivery: preferences, access, body loading, replay and retry."""

import time

import redis
from dishka import AsyncContainer
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from hovorunv2.application.access.permissions import PermissionTarget
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
from hovorunv2.domain.audit import ActorContext
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.persistence.stores.worker_notification_store import WorkerNotificationStore
from hovorunv2.infrastructure.runtime.cache import CacheService
from hovorunv2.infrastructure.runtime.feature_payloads import ValkeyPayloadStore, WorkerPayloadClient
from tests.utils.runtime import ManualClock, runtime_cache
from tests.utils.usage import usage_subjects

USER = 560001
EVENT_KEY = "release.v0.3.0"


class RecordingSender:
    """Record external Telegram sends only; no network I/O."""

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
        return 900 + len(self.calls)


class FailingSender:
    """Fail every send to exercise bounded retry."""

    def __init__(self) -> None:
        self.calls = 0

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
        reply_markup: str | None = None,
    ) -> int | None:
        """Fail one synthetic send."""
        self.calls += 1
        msg = "synthetic telegram failure"
        raise OSError(msg)


def _sync_store(database_url: str) -> tuple[WorkerNotificationStore, object]:
    engine = create_engine(database_url, hide_parameters=True)
    sessions = sessionmaker(engine, expire_on_commit=False)
    return WorkerNotificationStore(sessions, lambda: None), engine


async def _seed(
    test_container: AsyncContainer,
    settings: Settings,
    cache: CacheService,
    namespace: RuntimeNamespace,
    clock: ManualClock,
) -> PermissionTarget:
    user, _group = await usage_subjects(test_container)
    payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
    sealed = await payloads.seal(EVENT_KEY, b"<b>Release v0.3.0</b>", clock.now() + 300)
    await payloads.put(sealed)
    ledger = await test_container.get(NotificationLedgerService)
    await ledger.ensure_event(
        NotificationEvent(
            EVENT_KEY,
            NotificationCategory.RELEASE,
            "release",
            "v0.3.0",
            sealed.reference,
        ),
        actor=ActorContext.cli(),
    )
    await ledger.record(
        EVENT_KEY,
        user.target,
        NotificationOutcome.PENDING,
        attempts=0,
        actor=ActorContext.cli(),
    )
    return user.target


async def test_due_receipt_is_delivered_once(test_container: AsyncContainer, postgres_database_url: str) -> None:
    """A due receipt with a preference-enabled recipient is delivered once and never replayed."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        target = await _seed(test_container, settings, cache, namespace, clock)
        store, engine = _sync_store(postgres_database_url)
        sender = RecordingSender()
        payloads = WorkerPayloadClient(
            redis.Redis.from_url(settings.valkey_url, decode_responses=True),
            settings,
            clock,
            namespace,
            UsageSafetyCaps(),
            lambda: None,
        )
        service = NotificationDeliveryService(store, payloads, sender, clock)
        try:
            assert service.deliver_due() == 1
            assert sender.calls == [(target.telegram_id, "<b>Release v0.3.0</b>")]
            assert service.deliver_due() == 0
            receipt = store.pending()
            assert receipt == ()
            ledger = await test_container.get(NotificationLedgerService)
            stored = await ledger.receipt(EVENT_KEY, target)
            assert stored is not None
            assert stored.outcome == NotificationOutcome.DELIVERED
        finally:
            engine.dispose()


async def test_preference_off_skips_without_sending(test_container: AsyncContainer, postgres_database_url: str) -> None:
    """A category switch suppresses delivery and records a terminal skip."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        target = await _seed(test_container, settings, cache, namespace, clock)
        preferences = await test_container.get(NotificationPreferenceService)
        await preferences.set_preference(
            target,
            NotificationCategory.RELEASE,
            enabled=False,
            actor=ActorContext.telegram(target.telegram_id or 0),
            transport_kind="private",
        )
        store, engine = _sync_store(postgres_database_url)
        sender = RecordingSender()
        payloads = WorkerPayloadClient(
            redis.Redis.from_url(settings.valkey_url, decode_responses=True),
            settings,
            clock,
            namespace,
            UsageSafetyCaps(),
            lambda: None,
        )
        service = NotificationDeliveryService(store, payloads, sender, clock)
        try:
            assert service.deliver_due() == 0
            assert sender.calls == []
            ledger = await test_container.get(NotificationLedgerService)
            stored = await ledger.receipt(EVENT_KEY, target)
            assert stored is not None
            assert stored.outcome == NotificationOutcome.SKIPPED
        finally:
            engine.dispose()


async def test_transient_failure_retries_with_bounded_backoff(
    test_container: AsyncContainer, postgres_database_url: str
) -> None:
    """A transient send failure records retry and waits for the bounded backoff window."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        target = await _seed(test_container, settings, cache, namespace, clock)
        store, engine = _sync_store(postgres_database_url)
        sender = FailingSender()
        payloads = WorkerPayloadClient(
            redis.Redis.from_url(settings.valkey_url, decode_responses=True),
            settings,
            clock,
            namespace,
            UsageSafetyCaps(),
            lambda: None,
        )
        service = NotificationDeliveryService(store, payloads, sender, clock)
        try:
            assert service.deliver_due() == 0
            ledger = await test_container.get(NotificationLedgerService)
            stored = await ledger.receipt(EVENT_KEY, target)
            assert stored is not None
            assert stored.outcome == NotificationOutcome.RETRY
            assert stored.attempts == 1
            assert service.deliver_due() == 0
            assert sender.calls == 1
            clock.value += 60
            assert service.deliver_due() == 0
            assert sender.calls == 2
        finally:
            engine.dispose()
