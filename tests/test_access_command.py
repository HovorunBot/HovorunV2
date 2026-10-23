"""Tests for the AccessCommand class."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User
from dishka import AsyncContainer

from hovorunv2.application.data.constants import CommandName
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.infrastructure.config import settings
from hovorunv2.interface.telegram.callbacks import AccessAction, AccessCallback
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
    admin_id = settings.admin_ids[0]
    chat_id = 789
    message = create_mock_message("/allow_chat", user_id=admin_id, chat_id=chat_id)
    bot = MagicMock(spec=Bot)

    await access_command.handle(message, cast("Bot", bot))

    # Check database directly through service
    chat_status_service = await init_container.get(ChatStatusService)
    is_approved = await chat_status_service.is_approved(chat_id)
    assert is_approved is True
    message.answer.assert_called_once_with("✅ Bot is now approved for this chat by administrator.")

    # Check that AUTO_ALLOW commands are enabled
    command_service = await init_container.get(CommandService)
    is_tiktok_allowed = await command_service.is_command_allowed(chat_id, CommandName.TIKTOK)
    assert is_tiktok_allowed is True


@pytest.mark.asyncio
async def test_forged_approve_from_non_owner_rejected(access_command: AccessCommand) -> None:
    """Verify non-owner cannot approve or reject chat via callback forgery."""
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    callback_data = AccessCallback(chat_id=12345, action=AccessAction.APPROVE)
    query = MagicMock()
    query.from_user = User(id=999999, is_bot=False, first_name="Attacker")
    query.answer = AsyncMock()
    query.message = MagicMock(spec=Message)
    query.message.text = "Request text"
    query.message.edit_text = AsyncMock()

    await access_command.handle_callback(query, callback_data, bot)

    query.answer.assert_called_once_with("🚫 Owners only.", show_alert=True)
    bot.send_message.assert_not_called()
    query.message.edit_text.assert_not_called()
