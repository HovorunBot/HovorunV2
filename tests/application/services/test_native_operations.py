"""Tests for synchronous Celery operations worker maintenance tasks."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest
import redis
from dishka import AsyncContainer, make_async_container
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.__main__ import _build_bot_router, _configure_dispatcher, _teardown_services
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.membership import MembershipEvidence, MembershipStatus
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.http_ports import HTTPFiles, HTTPForm, HTTPHeaders, HTTPQuery, SyncHTTPPort
from hovorunv2.application.operations.changelog import get_current_version
from hovorunv2.application.operations.notification_delivery import NotificationDeliveryService
from hovorunv2.application.operations.operations_runner import WorkerOperationsRunner
from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.container import AppProvider
from hovorunv2.bootstrap.providers.infrastructure import InfrastructureProvider
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.domain.enums import ChatStatus, Role
from hovorunv2.domain.system import SystemStateDB
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.queues.feature_queue import (
    CHAT_MAINTENANCE_TASK,
    CLEANUP_SWEEP_TASK,
    MEMBERSHIP_RECONCILIATION_TASK,
    RELEASE_NOTIFICATIONS_TASK,
    broadcast_release_notifications_task,
    build_beat_schedule,
    reconcile_memberships_task,
    run_chat_maintenance_task,
    send_unauthorized_reminder_task,
    set_operations_runner,
    sweep_expired_cleanups_task,
)
from hovorunv2.infrastructure.runtime.feature_payloads import WorkerPayloadClient
from hovorunv2.interface.telegram.settings.prompts import SyncTelegramUnauthorizedPromptSender
from hovorunv2.interface.telegram.transport.sync_delivery import NativeTelegramAPI
from tests.conftest import provision_role
from tests.utils.runtime import ManualClock


def _delivery(
    db: WorkerDatabase,
    client: redis.Redis,
    settings: Settings,
    namespace: RuntimeNamespace,
    api: NativeTelegramAPI,
) -> NotificationDeliveryService:
    """Compose the real delivery service with the child-owned database and payload client."""
    clock = ManualClock()
    return NotificationDeliveryService(
        ledger=db.notifications,
        payloads=WorkerPayloadClient(client, settings, clock, namespace, UsageSafetyCaps(), lambda: None),
        sender=api,
        clock=clock,
    )


class RecordingSyncHTTP(SyncHTTPPort):
    """Stub synchronous HTTP adapter capturing API requests and delivering canned responses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, HTTPForm | None]] = []
        self.bot_id = 999
        self.member_status = "administrator"
        self.admin_ids: list[int] = [123]

    def read_json(self, url: str, params: HTTPQuery | None = None, headers: HTTPHeaders | None = None) -> object:
        """Read JSON response stub."""
        return {}

    def read_text(self, url: str, params: HTTPQuery | None = None, headers: HTTPHeaders | None = None) -> str:
        """Read text response stub."""
        return ""

    def resolve(self, url: str, headers: HTTPHeaders | None = None) -> str:
        """Resolve redirect destination stub."""
        return url

    def download_bytes(
        self, url: str, headers: HTTPHeaders | None = None, *, cookies: Mapping[str, str] | None = None
    ) -> bytes:
        """Download binary payload stub."""
        return b""

    def request_json(
        self,
        method: str,
        url: str,
        *,
        data: HTTPForm | None = None,
        files: HTTPFiles | None = None,
    ) -> object:
        """Simulate Telegram Bot API responses for test expectations."""
        self.calls.append((method, url, data))
        if "getChatMember" in url:
            user_id = int(data.get("user_id", 0)) if data else 0
            is_bot = user_id == self.bot_id
            return {
                "ok": True,
                "result": {
                    "status": "administrator",
                    "user": {"id": user_id, "is_bot": is_bot},
                    "can_delete_messages": True,
                    "can_restrict_members": True,
                    "can_invite_users": True,
                    "can_pin_messages": True,
                    "can_manage_chat": True,
                },
            }
        if "getChatAdministrators" in url:
            return {
                "ok": True,
                "result": [{"status": "creator", "user": {"id": uid, "is_bot": False}} for uid in self.admin_ids],
            }
        if "sendMessage" in url:
            return {"ok": True, "result": {"message_id": 42}}
        if "deleteMessage" in url:
            return {"ok": True, "result": True}
        if "editMessageText" in url:
            return {"ok": True, "result": {"message_id": 42}}
        return {"ok": True, "result": {}}


@pytest.fixture
def sync_http() -> RecordingSyncHTTP:
    """Provide a recording synchronous HTTP transport."""
    return RecordingSyncHTTP()


@pytest.fixture
def sync_telegram_api(sync_http: RecordingSyncHTTP) -> NativeTelegramAPI:
    """Provide a synchronous Telegram API using the recording transport."""
    return NativeTelegramAPI(sync_http, "999:TEST_BOT_TOKEN")


@pytest.fixture
def sync_prompt_sender(sync_telegram_api: NativeTelegramAPI) -> SyncTelegramUnauthorizedPromptSender:
    """Provide a synchronous prompt sender using the test telegram API."""
    return SyncTelegramUnauthorizedPromptSender(sync_telegram_api)


async def test_celery_beat_schedule_structure() -> None:
    """Verify beat schedule routes all operations to the operations queue."""
    schedule = build_beat_schedule("test:")
    assert MEMBERSHIP_RECONCILIATION_TASK in [s["task"] for s in schedule.values()]
    assert CHAT_MAINTENANCE_TASK in [s["task"] for s in schedule.values()]
    assert CLEANUP_SWEEP_TASK in [s["task"] for s in schedule.values()]
    assert RELEASE_NOTIFICATIONS_TASK in [s["task"] for s in schedule.values()]
    for entry in schedule.values():
        options = entry.get("options")
        assert isinstance(options, dict)
        assert options["queue"] == "test:operations"


async def test_membership_reconciliation_in_operations_worker(
    test_container: AsyncContainer,
    sync_telegram_api: NativeTelegramAPI,
    sync_prompt_sender: SyncTelegramUnauthorizedPromptSender,
) -> None:
    """Run membership reconciliation in operations worker and verify durable PostgreSQL state."""
    chats = await test_container.get(ChatStore)
    users = await test_container.get(UserStore)
    memberships = await test_container.get(MembershipStore)
    settings = await test_container.get(Settings)

    group_chat_id = -100555111
    user_telegram_id = 123
    group = await chats.get_or_create_chat(group_chat_id)
    user = await users.ingest_user(user_telegram_id)
    assert user is not None

    stale_time = datetime.now(UTC) - timedelta(minutes=10)
    await memberships.save_member(
        group.id,
        MembershipEvidence(
            group_chat_id,
            user_telegram_id,
            MembershipStatus.ADMIN,
            stale_time,
            stale_time + timedelta(minutes=5),
        ),
    )

    namespace = RuntimeNamespace("test:{ops}:")
    client = redis.Redis.from_url(settings.valkey_url, decode_responses=True)
    with WorkerDatabase(settings) as db:
        runner = WorkerOperationsRunner(
            ops=db.operations,
            client=client,
            namespace=namespace,
            api=sync_telegram_api,
            prompt_sender=sync_prompt_sender,
            settings=settings,
            clock=ManualClock(),
            delivery=_delivery(db, client, settings, namespace, sync_telegram_api),
        )
        set_operations_runner(runner)
        try:
            refreshed = reconcile_memberships_task()
            assert refreshed == 1

            state = await memberships.get_state(group.id)
            assert state is not None
            assert state.bot_status == "admin"
            assert state.admins_known is True
            assert state.can_delete_messages is True

            member_ev = await memberships.get_member(group.id, user.id)
            assert member_ev is not None
            assert member_ev.status == MembershipStatus.ADMIN
        finally:
            set_operations_runner(None)
            client.close()


async def test_chat_reminders_and_unauthorized_prompts_in_operations_worker(
    test_container: AsyncContainer,
    sync_telegram_api: NativeTelegramAPI,
    sync_prompt_sender: SyncTelegramUnauthorizedPromptSender,
    sync_http: RecordingSyncHTTP,
) -> None:
    """Verify unauthorized group prompts and pending owner pings with cooldown tracking."""
    chats = await test_container.get(ChatStore)
    statuses = await test_container.get(ChatStatusService)
    settings = await test_container.get(Settings)
    await provision_role(test_container, 777111, Role.OWNER)

    unauthorized_chat_id = -100777222
    pending_chat_id = -100777333
    await chats.get_or_create_chat(unauthorized_chat_id)
    await statuses.set_status(unauthorized_chat_id, ChatStatus.UNAUTHORIZED)
    await chats.get_or_create_chat(pending_chat_id)
    await statuses.set_status(pending_chat_id, ChatStatus.PENDING)

    namespace = RuntimeNamespace("test:{reminders}:")
    client = redis.Redis.from_url(settings.valkey_url, decode_responses=True)
    client.delete(f"periodic:unauthorized_prompt:{unauthorized_chat_id}")
    client.delete(f"periodic:pending_ping:{pending_chat_id}")

    with WorkerDatabase(settings) as db:
        runner = WorkerOperationsRunner(
            ops=db.operations,
            client=client,
            namespace=namespace,
            api=sync_telegram_api,
            prompt_sender=sync_prompt_sender,
            settings=settings,
            clock=ManualClock(),
            delivery=_delivery(db, client, settings, namespace, sync_telegram_api),
        )
        set_operations_runner(runner)
        try:
            count = run_chat_maintenance_task()
            assert count >= 1

            assert client.get(f"periodic:unauthorized_prompt:{unauthorized_chat_id}") == "1"
            assert client.get(f"periodic:pending_ping:{pending_chat_id}") == "1"

            # Running again should respect cooldown and send nothing
            sync_http.calls.clear()
            second_count = run_chat_maintenance_task()
            assert second_count == 0
            assert len(sync_http.calls) == 0

            # Direct unauthorized reminder task also respects cooldown
            assert send_unauthorized_reminder_task(unauthorized_chat_id) is False
        finally:
            set_operations_runner(None)
            client.close()


async def test_cleanup_sweep_in_operations_worker(
    test_container: AsyncContainer,
    sync_telegram_api: NativeTelegramAPI,
    sync_prompt_sender: SyncTelegramUnauthorizedPromptSender,
    sync_http: RecordingSyncHTTP,
) -> None:
    """Verify expired window messages are deleted and removed from Valkey ZSET."""
    settings = await test_container.get(Settings)
    client = redis.Redis.from_url(settings.valkey_url, decode_responses=True)
    now = datetime.now(UTC).timestamp()

    expired_member = "-100888111:501"
    future_member = "-100888111:502"
    client.zadd("cleanup:index", {expired_member: now - 100, future_member: now + 5000})

    namespace = RuntimeNamespace("test:{cleanup}:")
    with WorkerDatabase(settings) as db:
        runner = WorkerOperationsRunner(
            ops=db.operations,
            client=client,
            namespace=namespace,
            api=sync_telegram_api,
            prompt_sender=sync_prompt_sender,
            settings=settings,
            clock=ManualClock(),
            delivery=_delivery(db, client, settings, namespace, sync_telegram_api),
        )
        set_operations_runner(runner)
        try:
            deleted = sweep_expired_cleanups_task()
            assert deleted == 1

            delete_calls = [c for c in sync_http.calls if "deleteMessage" in c[1]]
            assert len(delete_calls) == 1
            assert delete_calls[0][2] == {"chat_id": -100888111, "message_id": 501}

            remaining = client.zrange("cleanup:index", 0, -1)
            assert expired_member not in remaining
            assert future_member in remaining
        finally:
            set_operations_runner(None)
            client.zrem("cleanup:index", future_member)
            client.close()


async def test_release_notifications_in_operations_worker(
    test_container: AsyncContainer,
    sync_telegram_api: NativeTelegramAPI,
    sync_prompt_sender: SyncTelegramUnauthorizedPromptSender,
    sync_http: RecordingSyncHTTP,
) -> None:
    """Verify release notification broadcasts only to approved groups on version upgrade."""
    chats = await test_container.get(ChatStore)
    settings = await test_container.get(Settings)

    approved_chat_id = -100999001
    await chats.get_or_create_chat(approved_chat_id)
    statuses = await test_container.get(ChatStatusService)
    await statuses.set_status(approved_chat_id, ChatStatus.APPROVED)

    current_ver = get_current_version()
    namespace = RuntimeNamespace("test:{release}:")
    client = redis.Redis.from_url(settings.valkey_url, decode_responses=True)

    with WorkerDatabase(settings) as db:
        runner = WorkerOperationsRunner(
            ops=db.operations,
            client=client,
            namespace=namespace,
            api=sync_telegram_api,
            prompt_sender=sync_prompt_sender,
            settings=settings,
            clock=ManualClock(),
            delivery=_delivery(db, client, settings, namespace, sync_telegram_api),
        )
        set_operations_runner(runner)
        try:
            # 1. Fresh database: silently set version without sending messages
            session_maker = await test_container.get(async_sessionmaker)
            async with session_maker() as s:
                await s.execute(delete(SystemStateDB).where(SystemStateDB.key == "last_notified_version"))
                await s.commit()

            sync_http.calls.clear()
            assert broadcast_release_notifications_task() == 0
            assert len(sync_http.calls) == 0
            assert db.operations.get_last_notified_version() == current_ver

            # 2. Version upgrade
            db.operations.set_last_notified_version("0.1.0")
            sync_http.calls.clear()
            sent = broadcast_release_notifications_task()
            assert sent >= 1
            send_calls = [c for c in sync_http.calls if "sendMessage" in c[1]]
            assert len(send_calls) >= 1
            assert send_calls[0][2]["chat_id"] == approved_chat_id
            assert db.operations.get_last_notified_version() == current_ver

            # 3. Same version: no broadcast
            sync_http.calls.clear()
            assert broadcast_release_notifications_task() == 0
            assert len(sync_http.calls) == 0
        finally:
            set_operations_runner(None)
            client.close()


async def test_bot_startup_does_not_start_scheduler_or_duplicate_notifications(
    test_container: AsyncContainer, sync_http: RecordingSyncHTTP
) -> None:
    """Verify multiple bot instances start with zero embedded schedulers or background loops."""
    container1 = make_async_container(InfrastructureProvider(), AppProvider())
    container2 = make_async_container(InfrastructureProvider(), AppProvider())
    try:
        settings = await container1.get(Settings)
        router1 = await _build_bot_router(container1, settings)
        dp1 = _configure_dispatcher(container1, router1)
        assert dp1 is not None

        router2 = await _build_bot_router(container2, settings)
        dp2 = _configure_dispatcher(container2, router2)
        assert dp2 is not None

        # Verify no calls or notifications were triggered
        assert len(sync_http.calls) == 0
    finally:
        await _teardown_services(None, container1)
        await _teardown_services(None, container2)
