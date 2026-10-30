"""Real SQLite group grant lifecycle and atomic audit contracts."""

from datetime import UTC, datetime

import pytest
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus
from hovorunv2.domain.group_grant import (
    ApprovalBasis,
    GroupAccessRequestDB,
    GroupApprovalDB,
    RequestStatus,
)
from hovorunv2.domain.user import UserDB
from tests.utils.postgres import reject_audit


async def seed_grant_subjects(container: AsyncContainer) -> tuple[int, int, int]:
    """Persist synthetic active subjects through real sessions."""
    sessions = await container.get(async_sessionmaker)
    async with sessions() as session:
        group = ChatDB(chat_id=-100101, status=ChatStatus.UNAUTHORIZED, chat_type="supergroup")
        first, second = UserDB(telegram_id=101), UserDB(telegram_id=102)
        session.add_all([group, first, second])
        await session.commit()
        return group.id, first.id, second.id


async def test_approval_idempotency_and_regrant_history(test_container: AsyncContainer) -> None:
    """Independent approval retains revoke/regrant history without user sponsorship."""
    group_id, _, _ = await seed_grant_subjects(test_container)
    store = await test_container.get(GroupGrantStore)
    actor = ActorContext.cli()
    assert await store.approve(group_id, actor)
    assert not await store.approve(group_id, actor)
    assert await store.revoke_approval(group_id, ApprovalBasis.EXPLICIT, actor)
    assert not await store.revoke_approval(group_id, ApprovalBasis.EXPLICIT, actor)
    assert not await store.approvals(group_id)
    assert await store.approve(group_id, actor)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        history = list((await session.scalars(select(GroupApprovalDB))).all())
        assert len(history) == 2
        assert sum(row.revoked_at is not None for row in history) == 1
        assert (await session.get(ChatDB, group_id)).status == ChatStatus.UNAUTHORIZED
        events = list(
            (await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "group_grant"))).all()
        )
        assert len(events) == 3
        assert all(event.actor_origin == actor.origin for event in events)


async def test_request_rejection_is_not_ban_and_approval_is_atomic(test_container: AsyncContainer) -> None:
    """Independent requests and decisions preserve original chat status and separate bases."""
    group_id, first_id, second_id = await seed_grant_subjects(test_container)
    store = await test_container.get(GroupGrantStore)
    actor = ActorContext.cli()
    request_id = await store.request(group_id, first_id, actor)
    assert await store.request(group_id, second_id, actor) == request_id
    assert await store.decide(request_id, RequestStatus.REJECTED, actor)
    assert not await store.approvals(group_id)
    second_request = await store.request(group_id, second_id, actor)
    assert second_request != request_id
    assert await store.decide(second_request, RequestStatus.APPROVED, actor)
    assert not await store.decide(second_request, RequestStatus.REJECTED, actor)
    assert not await store.approve(group_id, actor)
    assert [row.basis for row in await store.approvals(group_id)] == [ApprovalBasis.EXPLICIT]
    assert [row.status for row in await store.requests(group_id)] == [RequestStatus.REJECTED, RequestStatus.APPROVED]
    assert await store.delete_request(request_id, actor)
    assert not await store.delete_request(request_id, actor)
    assert len(await store.requests(group_id)) == 1
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        assert (await session.get(ChatDB, group_id)).status == ChatStatus.UNAUTHORIZED
        assert (await session.get(GroupAccessRequestDB, request_id)).deleted_at is not None
    assert await store.revoke_approval(group_id, ApprovalBasis.EXPLICIT, actor)
    assert not await store.approvals(group_id)


async def test_grant_queries_filter_deleted_parents_and_associations(test_container: AsyncContainer) -> None:
    """A deleted identity cannot preserve ordinary association visibility or accept mutations."""
    group_id, first_id, _ = await seed_grant_subjects(test_container)
    store = await test_container.get(GroupGrantStore)
    actor = ActorContext.cli()
    await store.approve(group_id, actor)
    await store.request(group_id, first_id, actor)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        first = await session.get(UserDB, first_id)
        first.deleted_at = datetime.now(UTC)
        await session.commit()
    assert not await store.requests(group_id)
    with pytest.raises(ValueError, match="active user"):
        await store.request(group_id, first_id, actor)
    async with sessions() as session:
        group = await session.get(ChatDB, group_id)
        group.deleted_at = datetime.now(UTC)
        await session.commit()
    assert not await store.approvals(group_id)
    with pytest.raises(ValueError, match="active group"):
        await store.approve(group_id, actor)


async def test_audit_failure_rolls_back_decision_and_approval(test_container: AsyncContainer) -> None:
    """SQLite audit failure rolls back both records of the composite approve command."""
    group_id, first_id, _ = await seed_grant_subjects(test_container)
    store = await test_container.get(GroupGrantStore)
    actor = ActorContext.cli()
    request_id = await store.request(group_id, first_id, actor)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        await reject_audit(session, "fail_grant_audit", "NEW.action = 'decide_request'", "audit unavailable")
        await session.commit()
    with pytest.raises(IntegrityError, match="audit unavailable"):
        await store.decide(request_id, RequestStatus.APPROVED, actor)
    assert not await store.approvals(group_id)
    assert (await store.requests(group_id))[0].status == RequestStatus.PENDING
    async with sessions() as session:
        assert not list((await session.scalars(select(GroupApprovalDB))).all())
