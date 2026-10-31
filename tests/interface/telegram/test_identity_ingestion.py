"""Public identity ingestion against real audited SQLite stores."""

from datetime import UTC, datetime

from aiogram.types import Chat, Message, User
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import PublicHandleStore, UserStore
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.enums import ChatStatus, TargetKind
from hovorunv2.interface.telegram.transport.identity import IdentityMiddleware


async def test_observation_updates_public_metadata_without_moving_authority(test_container: AsyncContainer) -> None:
    """A handle can move while trust, grants and salts stay with numeric identities."""
    middleware = await test_container.get(IdentityMiddleware)
    users = await test_container.get(UserStore)
    chats = await test_container.get(ChatStore)
    handles = await test_container.get(PublicHandleStore)
    chat = Chat(id=-1701, type="supergroup", title="Public Group", username="group_old")
    assert await middleware.observe(chat, User(id=1701, is_bot=False, first_name="Ignored", username="public_old"))
    await users.set_trust(1701, is_trusted=True)
    await chats.update_status(chat.id, ChatStatus.APPROVED)
    await chats.set_encryption_salt(chat.id, "synthetic-salt")
    assert await middleware.observe(
        chat.model_copy(update={"title": "New title", "username": None}),
        User(id=1701, is_bot=False, first_name="Ignored", username="public_new"),
    )
    assert await middleware.observe(None, User(id=1702, is_bot=False, first_name="Ignored", username="public_old"))
    original = await users.get_by_telegram_id(1701)
    replacement = await users.get_by_telegram_id(1702)
    assert original is not None
    assert original.is_trusted
    assert replacement is not None
    assert not replacement.is_trusted
    stored = await chats.get_chat(chat.id)
    assert stored is not None
    assert stored.title == "New title"
    assert stored.status == ChatStatus.APPROVED
    assert stored.encryption_salt == "synthetic-salt"
    assert await handles.get_handle_by_target(TargetKind.GROUP, chat.id) is None
    assert (await handles.resolve_handle("public_old")).target_id == 1702


async def test_tombstones_block_downstream_ingestion(test_container: AsyncContainer) -> None:
    """Normal updates cannot resurrect either deleted identity or transport."""
    middleware = await test_container.get(IdentityMiddleware)
    users = await test_container.get(UserStore)
    chats = await test_container.get(ChatStore)
    user = User(id=1703, is_bot=False, first_name="Ignored")
    chat = Chat(id=-1703, type="group", title="Group")
    assert await middleware.observe(chat, user)
    await chats.soft_delete_chat(chat.id, ActorContext.cli(), "test")
    assert not await middleware.observe(chat, user)
    assert len(await chats.get_historical_by_chat_id(chat.id)) == 1
    await users.soft_delete_user(user.id, ActorContext.cli(), "test")
    assert not await middleware.observe(None, user)
    assert len(await users.get_historical_by_telegram_id(user.id)) == 1


async def test_sender_chat_and_bot_do_not_create_human_identity(test_container: AsyncContainer) -> None:
    """Anonymous administrators, bots and private payload fields never become authority facts."""
    middleware = await test_container.get(IdentityMiddleware)
    users = await test_container.get(UserStore)
    chat = Chat(id=-1704, type="group", title="Public Group")
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=chat,
        sender_chat=chat,
        from_user=User(id=1704, is_bot=False, first_name="Ignored"),
        text="private payload",
    )
    await middleware(_finish, message, {})
    assert await users.get_by_telegram_id(1704) is None
    assert await middleware.observe(None, User(id=1705, is_bot=True, first_name="Bot"))
    assert await users.get_by_telegram_id(1705) is None
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        events = list((await session.scalars(select(AuditEventDB))).all())
        assert "private payload" not in str([event.changed_fields for event in events])
        assert "Ignored" not in str([event.changed_fields for event in events])


async def _finish(event: object, data: dict[str, object]) -> None:
    """Complete middleware dispatch without an external Telegram call."""
