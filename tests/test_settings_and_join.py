"""Tests for the unified settings menu and on-join verification."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, Message, User
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.ports import ChatStore
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.domain.audit import AuditEventDB
from hovorunv2.domain.enums import ChatStatus, CommandName
from hovorunv2.interface.telegram.callbacks import (
    SettingsAction,
    SettingsCallback,
    SettingsMenu,
)
from hovorunv2.interface.telegram.handlers.bot_join import BotJoinHandler
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from tests.conftest import create_mock_message, provision_role, verify_group_admins
from tests.utils.recorded_responses import RecordedResponses
from tests.utils.settings import bind_settings_callback
from tests.utils.telegram import RecordedResponse


async def test_bot_join_prompt(test_container: AsyncContainer) -> None:
    """Use real services and intercept only the external Telegram HTTP prompt."""
    handler = await test_container.get(BotJoinHandler)
    user = User(id=777, is_bot=True, first_name="Synthetic bot")
    event = ChatMemberUpdated(
        chat=Chat(id=-100777, type="supergroup", title="Synthetic group"),
        from_user=User(id=101, is_bot=False, first_name="Synthetic actor"),
        date=datetime.now(UTC),
        old_chat_member=ChatMemberLeft(user=user),
        new_chat_member=ChatMemberMember(user=user),
    )
    bot = Bot("777:synthetic-token")
    try:
        with RecordedResponses() as responses:
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/sendMessage",
                payload={
                    "ok": True,
                    "result": {"message_id": 1, "date": 1, "chat": {"id": -100777, "type": "supergroup"}},
                },
            )
            await handler.handle_my_chat_member_update(event, bot)
            assert len(responses.requests) == 1
        store = await test_container.get(ChatStore)
        chat = await store.get_chat(-100777)
        assert chat is not None
        assert chat.status == ChatStatus.UNAUTHORIZED
        assert chat.is_bot_present
    finally:
        await bot.session.close()


async def test_settings_modules_toggle(test_container: AsyncContainer) -> None:
    """Only registered split features are configurable, using current real authority."""
    command = await test_container.get(SettingsCommand)
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(-100777)
    await verify_group_admins(test_container, -100777)
    status = await test_container.get(ChatStatusService)
    await status.approve(-100777)
    await provision_role(test_container, 456)
    message = Message(
        message_id=789,
        date=datetime.now(UTC),
        chat=Chat(id=-100777, type="supergroup"),
    )
    bot = Bot("777:synthetic-token")
    query = CallbackQuery(
        id="synthetic",
        from_user=User(id=456, first_name="Synthetic", is_bot=False),
        chat_instance="synthetic",
        message=message.as_(bot),
    ).as_(bot)
    callback = await bind_settings_callback(
        test_container,
        message,
        456,
        SettingsCallback(menu=SettingsMenu.MODULES, action=SettingsAction.TOGGLE, value=CommandName.TWITTER_POST),
    )
    try:
        with RecordedResponses() as responses:
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/answerCallbackQuery",
                payload={"ok": True, "result": True},
                response_class=RecordedResponse,
            )
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/editMessageText",
                payload={"ok": True, "result": True},
                response_class=RecordedResponse,
            )
            await command.handle_callback(
                query,
                callback,
            )
        commands = await test_container.get(CommandService)
        assert not await commands.is_command_allowed(-100777, CommandName.TWITTER_POST)
        assert await commands.is_command_allowed(-100777, CommandName.TWITTER_VIDEO)
        sessions = await test_container.get(async_sessionmaker)
        async with sessions() as session:
            event = await session.scalar(
                select(AuditEventDB).where(
                    AuditEventDB.target_id == "-100777:twitter_post", AuditEventDB.action == "disable_command"
                )
            )
            assert event is not None
            assert event.actor_id == 456
    finally:
        await bot.session.close()


async def test_settings_trigger_mention_filtering(init_container: AsyncContainer) -> None:
    """Verify settings trigger filter matches exact name and own mention, rejects other bots."""
    settings_cmd = await init_container.get(SettingsCommand)
    bot = MagicMock(spec=Bot)
    bot.me = AsyncMock(return_value=User(id=1, is_bot=True, first_name="Hovorun", username="HovorunBot"))

    msg_plain = create_mock_message("/settings")
    msg_other_cmd = create_mock_message("/settingsXYZ")
    msg_own = create_mock_message("/settings@HovorunBot")
    msg_other = create_mock_message("/settings@OtherBot")

    assert await settings_cmd.is_triggered(msg_plain) is True
    assert await settings_cmd.is_triggered(msg_other_cmd) is False
    assert await settings_cmd.is_triggered(msg_own) is True

    assert bool(await settings_cmd.trigger(msg_own, bot)) is True
    assert bool(await settings_cmd.trigger(msg_other, bot)) is False
