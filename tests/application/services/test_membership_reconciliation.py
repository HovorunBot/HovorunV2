"""Reconcile real SQLite state through the Telegram HTTP adapter."""

import asyncio
from datetime import UTC, datetime, timedelta
from http import HTTPStatus

import pytest
from aiogram import Bot
from aioresponses import aioresponses
from dishka import AsyncContainer

from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.application.membership import MembershipEvidence, MembershipStatus
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.ports import ChatStore, TaskQueuePort, UserStore
from hovorunv2.application.services import membership_reconciliation as reconciliation
from hovorunv2.application.services.membership_reconciliation import GROUP_BATCH_SIZE, MembershipReconciliationService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.interface.telegram.membership import TelegramMembershipAdapter
from tests.utils.telegram import API, BOT_CREDENTIAL, GROUP_ID, USER_ID, RecordedResponse, member_payload


async def prepare_group(container: AsyncContainer) -> int:
    """Create only a known group and its verified synthetic administrator."""
    chats = await container.get(ChatStore)
    users = await container.get(UserStore)
    grants = await container.get(GroupGrantStore)
    group = await chats.get_or_create_chat(GROUP_ID)
    user = await users.ingest_user(USER_ID)
    assert user is not None
    await grants.approve(group.id, ActorContext.cli())
    memberships = await container.get(MembershipStore)
    verified_at = datetime.now(UTC) - timedelta(minutes=6)
    await memberships.save_member(
        group.id,
        MembershipEvidence(GROUP_ID, USER_ID, MembershipStatus.ADMIN, verified_at, verified_at + timedelta(minutes=5)),
    )
    return group.id


def record_refresh(mocked: aioresponses, *, unavailable: bool = False) -> None:
    """Record external Telegram responses for one complete group refresh."""
    if unavailable:
        for method in ("getChatMember", "getChatAdministrators", "getChatMember"):
            mocked.post(
                f"{API}/{method}",
                response_class=RecordedResponse,
                status=HTTPStatus.FORBIDDEN,
                payload={"ok": False, "error_code": 403, "description": "Synthetic unavailable"},
            )
        return
    mocked.post(
        f"{API}/getChatMember",
        response_class=RecordedResponse,
        payload={"ok": True, "result": member_payload(is_bot=True)},
    )
    mocked.post(
        f"{API}/getChatAdministrators",
        response_class=RecordedResponse,
        payload={"ok": True, "result": [member_payload("creator")]},
    )
    mocked.post(
        f"{API}/getChatMember",
        response_class=RecordedResponse,
        payload={"ok": True, "result": member_payload("creator")},
    )


async def test_startup_failure_and_later_recovery_preserve_grants(test_container: AsyncContainer) -> None:
    """An unavailable startup records uncertainty; a later refresh recovers without resetting grants."""
    group_id = await prepare_group(test_container)
    store = await test_container.get(MembershipStore)
    users = await test_container.get(UserStore)
    user = await users.get_by_telegram_id(USER_ID)
    assert user is not None
    queue = await test_container.get(TaskQueuePort)
    async with Bot(BOT_CREDENTIAL).context() as bot:
        service = MembershipReconciliationService(store, TelegramMembershipAdapter(bot), queue)
        with aioresponses() as mocked:
            record_refresh(mocked, unavailable=True)
            assert await service.run_cycle() == 1
        failed = await store.get_member(group_id, user.id)
        assert failed is not None
        assert failed.status == MembershipStatus.UNKNOWN
        assert len(await (await test_container.get(GroupGrantStore)).approvals(group_id)) == 1
        with aioresponses() as mocked:
            record_refresh(mocked)
            assert await service.run_cycle(datetime.now(UTC) + timedelta(minutes=6)) == 1
        recovered = await store.get_member(group_id, user.id)
        assert recovered is not None
        assert recovered.status == MembershipStatus.ADMIN
        assert await service.run_cycle() == 0


async def test_refresh_coordination_and_cycle_batch_are_bounded(test_container: AsyncContainer) -> None:
    """Concurrent refreshes share one group request sequence and batches exclude unrelated members."""
    group_id = await prepare_group(test_container)
    store = await test_container.get(MembershipStore)
    queue = await test_container.get(TaskQueuePort)
    chats = await test_container.get(ChatStore)
    group = await chats.get_chat(GROUP_ID)
    assert group is not None
    async with Bot(BOT_CREDENTIAL).context() as bot:
        service = MembershipReconciliationService(store, TelegramMembershipAdapter(bot), queue)
        with aioresponses() as mocked:
            record_refresh(mocked)
            results = await asyncio.gather(service.refresh_group(group), service.refresh_group(group))
        assert sorted(results) == [False, True]
    for offset in range(GROUP_BATCH_SIZE + 3):
        await chats.get_or_create_chat(GROUP_ID - offset - 1)
    assert len(await store.due_groups(datetime.now(UTC), limit=GROUP_BATCH_SIZE)) == GROUP_BATCH_SIZE
    assert len(await store.relevant_users(group_id)) == 1


async def test_cycle_deadline_leaves_evidence_due_and_releases_coordination(
    test_container: AsyncContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exhausting the wall-clock budget cannot leave a stuck refresh lock or fabricated evidence."""
    group_id = await prepare_group(test_container)
    store = await test_container.get(MembershipStore)
    queue = await test_container.get(TaskQueuePort)
    async with Bot(BOT_CREDENTIAL).context() as bot:
        service = MembershipReconciliationService(store, TelegramMembershipAdapter(bot), queue)
        with monkeypatch.context() as scoped:
            scoped.setattr(reconciliation, "CYCLE_TIMEOUT_SECONDS", 0)
            assert await service.run_cycle() == 0
        state = await store.get_state(group_id)
        assert state is not None
        assert state.bot_verified_at is None
        assert state.policy_revision == 1
        with aioresponses() as mocked:
            record_refresh(mocked)
            assert await service.run_cycle() == 1
