"""Group/chat identity lifecycle: soft-delete tombstones, generations, and audit."""

from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, Platform


async def test_soft_deleted_chat_and_active_coexist(test_container: AsyncContainer) -> None:
    """Soft-deleted chats and active chat generations can coexist."""
    session_maker = await test_container.get(async_sessionmaker)
    chat_store = await test_container.get(ChatStore)
    chat_id = -1009876543210

    # 1. Create chat
    chat = await chat_store.get_or_create_chat(chat_id)
    assert chat is not None
    assert chat.generation == 1

    # 2. Soft-delete generation 1
    actor = ActorContext.cli(actor_id=None, reason="group reset")
    deleted = await chat_store.soft_delete_chat(chat_id, actor=actor, reason="group reset")
    assert deleted is True

    # 3. Normal lookup excludes soft-deleted chat
    assert await chat_store.get_chat(chat_id) is None

    # 4. Insert the next active generation directly (deletion advanced the tombstone to 2)
    async with session_maker() as session:
        gen2 = ChatDB(
            chat_id=chat_id,
            platform=Platform.TELEGRAM,
            status=ChatStatus.APPROVED,
            generation=3,
        )
        session.add(gen2)
        await session.commit()

    # 5. Historical lookup returns both generations
    history = await chat_store.get_historical_by_chat_id(chat_id)
    assert len(history) == 2
    assert [c.generation for c in history] == [3, 2]

    # Active lookup returns only the fresh generation
    active = await chat_store.get_chat(chat_id)
    assert active is not None
    assert active.generation == 3


async def test_soft_delete_is_audited_transactionally(test_container: AsyncContainer) -> None:
    """Soft-deleting a chat records an audit event in the same transaction."""
    session_maker = await test_container.get(async_sessionmaker)
    chat_store = await test_container.get(ChatStore)
    chat_id = -1005544332211

    await chat_store.get_or_create_chat(chat_id)

    del_actor = ActorContext.telegram(user_id=12345, reason="group decommissioned")
    assert await chat_store.soft_delete_chat(chat_id, actor=del_actor, reason="group decommissioned") is True

    async with session_maker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEventDB).where(
                        AuditEventDB.target_type == "chat",
                        AuditEventDB.target_id == str(chat_id),
                        AuditEventDB.action == "delete",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].reason == "group decommissioned"
        assert events[0].changed_fields is not None
        assert events[0].changed_fields["reason"] == "group decommissioned"
