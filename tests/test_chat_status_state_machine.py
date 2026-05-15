"""Tests for the Access State Machine."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, ChatMemberUpdated
from dishka import AsyncContainer

from hovorunv2.application.data.constants import ChatStatus
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.interface.telegram.handlers.access import AccessCommand
from hovorunv2.interface.telegram.handlers.bot_join import BotJoinHandler


@pytest.fixture
async def chat_status_service(init_container: AsyncContainer) -> ChatStatusService:
    """Provide ChatStatusService."""
    return await init_container.get(ChatStatusService)


@pytest.fixture
async def bot_join_handler(init_container: AsyncContainer) -> BotJoinHandler:
    """Provide BotJoinHandler."""
    return await init_container.get(BotJoinHandler)


@pytest.fixture
async def access_command(init_container: AsyncContainer) -> AccessCommand:
    """Provide AccessCommand."""
    return await init_container.get(AccessCommand)


def create_mock_event(chat_id: int, status: str) -> MagicMock:
    """Create mock ChatMemberUpdated event."""
    event = MagicMock(spec=ChatMemberUpdated)
    event.chat = MagicMock(spec=Chat)
    event.chat.id = chat_id
    event.new_chat_member = MagicMock()
    event.new_chat_member.status = status
    return event


@pytest.mark.asyncio
async def test_state_machine_flow(
    chat_status_service: ChatStatusService,
    bot_join_handler: BotJoinHandler,
    access_command: AccessCommand,
) -> None:
    """Test full access state machine lifecycle."""
    chat_id = 111
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()
    bot.leave_chat = AsyncMock()

    # 1. Join UNAUTHORIZED
    event = create_mock_event(chat_id, "member")
    await bot_join_handler.handle_my_chat_member_update(event, bot)
    assert await chat_status_service.get_status(chat_id) == ChatStatus.UNAUTHORIZED
    bot.send_message.assert_called()

    # 2. Request Approval -> PENDING
    message = MagicMock()
    message.chat.id = chat_id
    message.from_user.id = 123
    message.from_user.full_name = "User"
    message.from_user.username = "user"
    message.answer = AsyncMock()

    await access_command._send_access_request(message, bot)  # noqa: SLF001
    assert await chat_status_service.get_status(chat_id) == ChatStatus.PENDING

    # 3. Duplicate request should be handled
    message.answer.reset_mock()
    await access_command._send_access_request(message, bot)  # noqa: SLF001
    message.answer.assert_called_with("⏳ Approval is already pending. Owners have been notified.")

    # 4. Kick -> INACTIVE
    event_kick = create_mock_event(chat_id, "kicked")
    await bot_join_handler.handle_my_chat_member_update(event_kick, bot)
    assert await chat_status_service.get_status(chat_id) == ChatStatus.INACTIVE

    # 5. Re-join -> UNAUTHORIZED prompt
    bot.send_message.reset_mock()
    await bot_join_handler.handle_my_chat_member_update(event, bot)
    bot.send_message.assert_called()

    # 6. Approve -> APPROVED
    # Mocking callback_data for Approve
    callback_data = MagicMock()
    callback_data.chat_id = chat_id
    callback_data.action = "approve"

    query = MagicMock()
    query.from_user.full_name = "Owner"
    query.from_user.id = 999
    query.message.text = "Request"
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    await access_command.handle_callback(query, callback_data, bot)
    assert await chat_status_service.get_status(chat_id) == ChatStatus.APPROVED

    # 7. Join when APPROVED -> Silent
    bot.send_message.reset_mock()
    await bot_join_handler.handle_my_chat_member_update(event, bot)
    bot.send_message.assert_not_called()

    # 8. BANNED -> Auto-leave
    await chat_status_service.set_status(chat_id, ChatStatus.BANNED)
    await bot_join_handler.handle_my_chat_member_update(event, bot)
    bot.leave_chat.assert_called_with(chat_id)
