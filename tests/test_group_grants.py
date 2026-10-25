"""Real SQLite group grant lifecycle and atomic audit contracts."""

from datetime import UTC, datetime

import pytest
from dishka import AsyncContainer
from sqlalchemy import select, text
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
    GroupSponsorDB,
    RequestStatus,
)
from hovorunv2.domain.user import UserDB


async def seed_grant_subjects(container: AsyncContainer) -> tuple[int, int, int]:
    """Persist synthetic active subjects through real sessions."""
    sessions = await container.get(async_sessionmaker)
    async with sessions() as session:
        group = ChatDB(chat_id=-100101, status=ChatStatus.UNAUTHORIZED, chat_type="supergroup")
        first, second = UserDB(telegram_id=101), UserDB(telegram_id=102)
        session.add_all([group, first, second])
        await session.commit()
        return group.id, first.id, second.id


@pytest.mark.asyncio
async def test_multiple_sponsors_idempotency_and_history(test_container: AsyncContainer) -> None:
    """Sponsors coexist and revoke/regrant appends history without granting trust or authority."""
    group_id, first_id, second_id = await seed_grant_subjects(test_container)
    store = await test_container.get(GroupGrantStore)
    actor = ActorContext.cli()
    assert await store.sponsor(group_id, first_id, actor)
    assert not await store.sponsor(group_id, first_id, actor)
    assert await store.sponsor(group_id, second_id, actor)
    assert {row.user_id for row in await store.sponsors(group_id)} == {first_id, second_id}
    assert await store.revoke_sponsor(group_id, first_id, actor)
    assert not await store.revoke_sponsor(group_id, first_id, actor)
    assert [row.user_id for row in await store.sponsors(group_id)] == [second_id]
    assert await store.sponsor(group_id, first_id, actor)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        history = list((await session.scalars(select(GroupSponsorDB))).all())
        assert len(history) == 3
        assert sum(row.revoked_at is not None for row in history) == 1
        assert (await session.get(ChatDB, group_id)).status == ChatStatus.UNAUTHORIZED
        events = list(
            (await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "group_grant"))).all()
        )
        assert len(events) == 4
        assert all(event.actor_origin == actor.origin for event in events)


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_grant_queries_filter_deleted_parents_and_associations(test_container: AsyncContainer) -> None:
    """A deleted identity cannot preserve ordinary association visibility or accept mutations."""
    group_id, first_id, second_id = await seed_grant_subjects(test_container)
    store = await test_container.get(GroupGrantStore)
    actor = ActorContext.cli()
    await store.sponsor(group_id, first_id, actor)
    await store.sponsor(group_id, second_id, actor)
    await store.approve(group_id, actor)
    await store.request(group_id, first_id, actor)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        first = await session.get(UserDB, first_id)
        first.deleted_at = datetime.now(UTC)
        second_sponsor = await session.scalar(select(GroupSponsorDB).where(GroupSponsorDB.user_id == second_id))
        second_sponsor.deleted_at = datetime.now(UTC)
        await session.commit()
    assert not await store.sponsors(group_id)
    assert not await store.requests(group_id)
    with pytest.raises(ValueError, match="active user"):
        await store.sponsor(group_id, first_id, actor)
    async with sessions() as session:
        group = await session.get(ChatDB, group_id)
        group.deleted_at = datetime.now(UTC)
        await session.commit()
    assert not await store.approvals(group_id)
    with pytest.raises(ValueError, match="active group"):
        await store.approve(group_id, actor)


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_decision_and_approval(test_container: AsyncContainer) -> None:
    """SQLite audit failure rolls back both records of the composite approve command."""
    group_id, first_id, _ = await seed_grant_subjects(test_container)
    store = await test_container.get(GroupGrantStore)
    actor = ActorContext.cli()
    request_id = await store.request(group_id, first_id, actor)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        await session.execute(
            text(
                "CREATE TRIGGER fail_grant_audit BEFORE INSERT ON audit_events "
                "WHEN NEW.action = 'decide_request' BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"
            )
        )
        await session.commit()
    with pytest.raises(IntegrityError, match="audit unavailable"):
        await store.decide(request_id, RequestStatus.APPROVED, actor)
    assert not await store.approvals(group_id)
    assert (await store.requests(group_id))[0].status == RequestStatus.PENDING
    async with sessions() as session:
        assert not list((await session.scalars(select(GroupApprovalDB))).all())
