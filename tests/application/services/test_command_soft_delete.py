"""Command selection removal keeps a soft-deleted association row and revives on re-enable."""

from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.features.ports import CommandStore
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import chat_commands
from hovorunv2.domain.enums import CommandName

USER = 630001
FEATURE = str(CommandName.TWITTER_VIDEO)


async def test_disable_soft_deletes_and_reenable_revives(test_container: AsyncContainer) -> None:
    """Disabling hides the selection but retains one row that re-enable revives."""
    users = await test_container.get(UserStore)
    await users.ingest_user(USER)
    await users.set_trust(USER, is_trusted=True, actor=ActorContext.cli())
    chat = await (await test_container.get(ChatStore)).get_or_create_chat(USER)
    commands = await test_container.get(CommandStore)
    actor = ActorContext.telegram(USER)
    assert await commands.enable_command(USER, FEATURE, actor=actor)
    assert FEATURE in await commands.get_allowed_commands(USER)
    assert await commands.disable_command(USER, FEATURE, actor=actor)
    assert FEATURE not in await commands.get_allowed_commands(USER)
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        rows = (
            await session.execute(
                select(chat_commands.c.chat_id, chat_commands.c.command_id, chat_commands.c.deleted_at).where(
                    chat_commands.c.chat_id == chat.id
                )
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].deleted_at is not None
    assert await commands.enable_command(USER, FEATURE, actor=actor)
    async with session_maker() as session:
        rows = (
            await session.execute(
                select(chat_commands.c.chat_id, chat_commands.c.command_id, chat_commands.c.deleted_at).where(
                    chat_commands.c.chat_id == chat.id
                )
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].deleted_at is None
    assert FEATURE in await commands.get_allowed_commands(USER)
