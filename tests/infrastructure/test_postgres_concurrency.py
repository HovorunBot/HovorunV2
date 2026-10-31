"""Real PostgreSQL concurrency without application-wide writer serialization."""

import asyncio
from functools import partial

import pytest
from dishka import AsyncContainer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus
from hovorunv2.infrastructure.persistence.locking import MutationLocks, lock_async_mutation


class HeldMutation:
    """Hold a real transaction at a barrier, after acquiring its ordinary subject locks."""

    def __init__(self, actor_id: int) -> None:
        self.actor_id = actor_id
        self.acquired = asyncio.Event()
        self.release = asyncio.Event()

    async def update(self, session: AsyncSession, chat_id: int) -> None:
        """Mutate one subject while another independent subject must still progress."""
        await lock_async_mutation(session, MutationLocks(chats=(chat_id,), actors=(self.actor_id,)))
        chat = await session.scalar(select(ChatDB).where(ChatDB.chat_id == chat_id))
        assert chat is not None
        chat.status = ChatStatus.APPROVED
        await session.flush()
        self.acquired.set()
        await self.release.wait()


async def test_independent_groups_share_actor_without_serializing_writers(test_container: AsyncContainer) -> None:
    """Both transactions hold their locks before either commits, even with the same actor."""
    users = await test_container.get(UserStore)
    chats = await test_container.get(ChatStore)
    actor_id = 1 << 40
    await users.ingest_user(actor_id)
    for chat_id in (-100901, -100902):
        await chats.get_or_create_chat(chat_id)
    uow = await test_container.get(AuditedUnitOfWork)
    mutations = (HeldMutation(actor_id), HeldMutation(actor_id))
    tasks = [
        asyncio.create_task(
            uow.execute(
                ActorContext.telegram(actor_id),
                "chat",
                chat_id,
                "update_status",
                partial(mutation.update, chat_id=chat_id),
                changed_fields={"status": "approved", "old_status": "unauthorized"},
            )
        )
        for mutation, chat_id in zip(mutations, (-100901, -100902), strict=True)
    ]
    try:
        async with asyncio.timeout(5):
            await asyncio.gather(*(mutation.acquired.wait() for mutation in mutations))
    finally:
        for mutation in mutations:
            mutation.release.set()
        await asyncio.gather(*tasks)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(AuditEventDB)
            .where(AuditEventDB.actor_id == actor_id, AuditEventDB.action == "update_status")
        )
        assert count == 2


async def test_concurrent_identity_ingestion_retains_one_identity_and_audit(test_container: AsyncContainer) -> None:
    """The absent natural key gate protects creation without a table-wide writer lock."""
    users = await test_container.get(UserStore)
    rows = await asyncio.gather(*(users.ingest_user(1 << 42) for _ in range(6)))
    assert all(row is not None for row in rows)
    assert len({row.id for row in rows if row is not None}) == 1
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEventDB)
                .where(AuditEventDB.target_type == "user", AuditEventDB.target_id == str(1 << 42))
            )
            == 1
        )


async def test_many_independent_groups_progress_concurrently(test_container: AsyncContainer) -> None:
    """Independent group subjects complete under load without one global writer gate."""
    users = await test_container.get(UserStore)
    chats = await test_container.get(ChatStore)
    actor_id = 1 << 43
    await users.ingest_user(actor_id)
    chat_ids = tuple(-100910 - index for index in range(8))
    for chat_id in chat_ids:
        await chats.get_or_create_chat(chat_id)

    await asyncio.gather(
        *(
            chats.update_status(chat_id, ChatStatus.APPROVED, actor=ActorContext.telegram(actor_id))
            for chat_id in chat_ids
        )
    )

    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(AuditEventDB)
            .where(AuditEventDB.actor_id == actor_id, AuditEventDB.action == "update_status")
        )
        assert count == len(chat_ids)


async def test_rolled_back_subject_lock_releases_without_audit(test_container: AsyncContainer) -> None:
    """An audit validation failure rolls back the mutation and releases its subject gate."""
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(-100903)
    uow = await test_container.get(AuditedUnitOfWork)
    mutation = HeldMutation(0)
    mutation.release.set()
    with pytest.raises(ValueError, match="Unsupported audit field"):
        await uow.execute(
            ActorContext.cli(),
            "chat",
            -100903,
            "update_status",
            partial(mutation.update, chat_id=-100903),
            changed_fields={"unknown": True},
        )
    async with asyncio.timeout(5):
        await chats.update_status(-100903, ChatStatus.APPROVED, actor=ActorContext.cli())
    chat = await chats.get_chat(-100903)
    assert chat is not None
    assert chat.status == ChatStatus.APPROVED
