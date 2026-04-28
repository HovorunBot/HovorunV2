"""Tests for the AllowBotCommand class."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User
from dishka import AsyncContainer

from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.infrastructure.config import settings
from hovorunv2.interface.telegram.handlers.base import BaseCommand
from hovorunv2.interface.telegram.handlers.whitelist import AllowBotCommand


@pytest.fixture
async def whitelist_command(init_container: AsyncContainer) -> AllowBotCommand:
    """Fixture to provide an AllowBotCommand instance."""
    return await init_container.get(AllowBotCommand)


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
        ("/allow_chat extra", False),
        ("/other_command", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(whitelist_command: AllowBotCommand, text: str | None, expected: bool) -> None:
    """Test the is_triggered method."""
    message = create_mock_message(text)
    assert await whitelist_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_handle_authorized(whitelist_command: AllowBotCommand, init_container: AsyncContainer) -> None:
    """Test handling by an authorized user."""
    admin_id = settings.admin_ids[0]
    chat_id = 789
    message = create_mock_message("/allow_chat", user_id=admin_id, chat_id=chat_id)
    bot = MagicMock(spec=Bot)

    commands = await init_container.get(list[BaseCommand])
    await whitelist_command.handle(message, cast("Bot", bot), commands=commands)

    # Check database directly through service
    whitelist_service = await init_container.get(WhitelistService)
    is_whitelisted = await whitelist_service.is_whitelisted(chat_id)
    assert is_whitelisted is True
    message.answer.assert_called_once_with("Bot is now allowed in this chat.")

    # Check that AUTO_ALLOW commands are enabled
    command_service = await init_container.get(CommandService)
    is_tiktok_allowed = await command_service.is_command_allowed(chat_id, "tiktok")
    assert is_tiktok_allowed is True


@pytest.mark.asyncio
async def test_handle_unauthorized(whitelist_command: AllowBotCommand, init_container: AsyncContainer) -> None:
    """Test handling by an unauthorized user."""
    user_id = 999  # Not in admin_ids
    chat_id = 7890
    message = create_mock_message("/allow_chat", user_id=user_id, chat_id=chat_id)
    bot = MagicMock(spec=Bot)

    commands = await init_container.get(list[BaseCommand])
    await whitelist_command.handle(message, cast("Bot", bot), commands=commands)

    whitelist_service = await init_container.get(WhitelistService)
    is_whitelisted = await whitelist_service.is_whitelisted(chat_id)
    assert is_whitelisted is False
    message.answer.assert_not_called()
