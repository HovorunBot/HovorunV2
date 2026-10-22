"""Tests for the AccessCommand class."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User
from dishka import AsyncContainer

from hovorunv2.application.data.constants import CommandName, UserTier
from hovorunv2.application.services.access_service import AccessService
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.interface.telegram.handlers.access import AccessCommand


@pytest.fixture
async def access_command(init_container: AsyncContainer) -> AccessCommand:
    """Fixture to provide an AccessCommand instance."""
    return await init_container.get(AccessCommand)


def create_mock_message(text: str | None, user_id: int = 123, chat_id: int = 456) -> MagicMock:
    """Create a mock Telegram message."""
    message = MagicMock(spec=Message)
    message.text = text
    message.from_user = MagicMock(spec=User)
    message.from_user.id = user_id
    message.chat = MagicMock(spec=Chat)
    message.chat.id = chat_id
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/allow_chat", True),
        (" /allow_chat ", True),
        ("/allow_chat extra", True),  # startswith logic
        ("/other_command", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(access_command: AccessCommand, text: str | None, expected: bool) -> None:
    """Test the is_triggered method."""
    message = create_mock_message(text)
    assert await access_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_handle_authorized(access_command: AccessCommand, init_container: AsyncContainer) -> None:
    """Test handling by an authorized user."""
    admin_id = 123
    access_service = await init_container.get(AccessService)
    await access_service.set_user_tier(admin_id, UserTier.ADMIN)

    chat_id = 789
    message = create_mock_message("/allow_chat", user_id=admin_id, chat_id=chat_id)
    bot = MagicMock(spec=Bot)

    await access_command.handle(message, cast("Bot", bot))

    # Check database directly through service
    chat_status_service = await init_container.get(ChatStatusService)
    is_approved = await chat_status_service.is_approved(chat_id)
    assert is_approved is True
    message.answer.assert_called_once_with("✅ Bot is now approved for this chat.")

    # Check that AUTO_ALLOW commands are enabled
    command_service = await init_container.get(CommandService)
    is_tiktok_allowed = await command_service.is_command_allowed(chat_id, CommandName.TIKTOK)
    assert is_tiktok_allowed is True


@pytest.mark.asyncio
async def test_handle_request_callback_vip(
    access_command: AccessCommand,
    init_container: AsyncContainer,
) -> None:
    """Test inline request approval button clicked by VIP approves immediately."""
    vip_id = 9911
    chat_id = 8822
    access_service = await init_container.get(AccessService)
    chat_status_service = await init_container.get(ChatStatusService)
    await access_service.set_user_tier(vip_id, UserTier.VIP)

    query = MagicMock()
    query.from_user.id = vip_id
    query.message = create_mock_message(None, user_id=vip_id, chat_id=chat_id)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()
    bot = MagicMock(spec=Bot)

    await access_command.handle_request_callback(query, bot)

    assert await chat_status_service.is_approved(chat_id) is True
    query.answer.assert_called_once_with("Chat approved.")
    query.message.edit_text.assert_called_once()


@pytest.mark.asyncio
async def test_handle_request_callback_banned(
    access_command: AccessCommand,
    init_container: AsyncContainer,
) -> None:
    """Test inline request approval button clicked by banned user is rejected."""
    banned_id = 9933
    chat_id = 8844
    access_service = await init_container.get(AccessService)
    await access_service.set_user_tier(banned_id, UserTier.BANNED)

    query = MagicMock()
    query.from_user.id = banned_id
    query.message = create_mock_message(None, user_id=banned_id, chat_id=chat_id)
    query.answer = AsyncMock()
    bot = MagicMock(spec=Bot)

    await access_command.handle_request_callback(query, bot)

    query.answer.assert_called_once_with("❌ You are forbidden from using this bot.", show_alert=True)


@pytest.mark.asyncio
async def test_handle_request_callback_chat_admin(
    access_command: AccessCommand,
    init_container: AsyncContainer,
) -> None:
    """Test inline request approval button clicked by group admin notifies bot admins."""
    user_id = 9955
    bot_admin_id = 9966
    chat_id = 8866
    access_service = await init_container.get(AccessService)
    await access_service.set_user_tier(user_id, UserTier.USER)
    await access_service.set_user_tier(bot_admin_id, UserTier.ADMIN)

    query = MagicMock()
    query.from_user.id = user_id
    query.from_user.full_name = "Group Admin"
    query.from_user.username = "grp_admin"
    query.message = create_mock_message(None, user_id=user_id, chat_id=chat_id)
    query.message.chat.title = "Test Group"
    query.message.chat.type = "supergroup"
    query.message.from_user = query.from_user
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    bot = MagicMock(spec=Bot)
    chat_member = MagicMock()
    chat_member.status = "administrator"
    bot.get_chat_member = AsyncMock(return_value=chat_member)
    bot.send_message = AsyncMock()

    await access_command.handle_request_callback(query, bot)

    bot.send_message.assert_called_once()
    assert bot.send_message.call_args[1]["chat_id"] == bot_admin_id
    query.answer.assert_called_once_with("Request sent.")
    query.message.edit_text.assert_called_once()
