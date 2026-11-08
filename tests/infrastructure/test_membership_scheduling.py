"""Verify recovery scheduling against real PostgreSQL through both execution models."""

from datetime import UTC, datetime, timedelta

import pytest
from dishka import AsyncContainer
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.identity.membership_reconciliation import GROUP_BATCH_SIZE
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus
from hovorunv2.domain.group_grant import ApprovalBasis, GroupApprovalDB
from hovorunv2.domain.membership import GroupMembershipStateDB
from hovorunv2.infrastructure.config import Settings


@pytest.mark.parametrize("legacy", [False, True])
async def test_due_groups_prioritize_approved_recovery_and_skip_absent_bots(
    test_container: AsyncContainer, legacy: bool
) -> None:
    """Historical groups cannot consume the batch ahead of approved groups awaiting recovery."""
    sessions = await test_container.get(async_sessionmaker)
    now = datetime.now(UTC)
    stale = now - timedelta(minutes=10)
    async with sessions() as session, session.begin():
        # Older absent and unapproved groups used to fill every bounded worker batch.
        session.add_all(
            ChatDB(chat_id=-2000 - offset, status=ChatStatus.APPROVED, is_bot_present=False)
            for offset in range(GROUP_BATCH_SIZE)
        )
        session.add_all(ChatDB(chat_id=-3000 - offset) for offset in range(GROUP_BATCH_SIZE))
        recovering = ChatDB(chat_id=-4000, status=ChatStatus.APPROVED if legacy else ChatStatus.UNAUTHORIZED)
        missing = ChatDB(chat_id=-4001, status=ChatStatus.APPROVED)
        session.add_all((recovering, missing))
        await session.flush()
        if not legacy:
            session.add(GroupApprovalDB(group_id=recovering.id, basis=ApprovalBasis.EXPLICIT))
        session.add(
            GroupMembershipStateDB(
                group_id=recovering.id,
                admins_known=True,
                admins_verified_at=stale,
                admins_expires_at=stale + timedelta(minutes=5),
                bot_expires_at=stale + timedelta(minutes=5),
            )
        )
        expected = [missing.id, recovering.id]
    memberships = await test_container.get(MembershipStore)
    assert [group.id for group in await memberships.due_groups(now, limit=2)] == expected
    settings = await test_container.get(Settings)
    with WorkerDatabase(settings) as database:
        assert [group.id for group in database.operations.due_groups(now, limit=2)] == expected
        due = database.operations.due_groups(now, limit=GROUP_BATCH_SIZE)
        assert len(due) == GROUP_BATCH_SIZE
        assert all(group.is_bot_present for group in due)
        assert database.operations.due_groups(now, limit=0) == []
