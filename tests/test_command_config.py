"""Tests for the CommandConfiguration system."""

import random
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Chat, Message, User

from hovorunv2.infrastructure.config import settings
from hovorunv2.interface.telegram.handlers.commands_config import EnableCommand
from hovorunv2.interface.telegram.middlewares import CommandConfigurationMiddleware

if TYPE_CHECKING:
    from hovorunv2.infrastructure.container import Container


def create_mock_message(text: str, chat_id: int = 456, user_id: int | None = None) -> MagicMock:
    """Create a mock Telegram message."""
    message = MagicMock(spec=Message)
    message.text = text
    message.from_user = MagicMock(spec=User)
    message.from_user.id = user_id or settings.admin_ids[0]
    message.chat = MagicMock(spec=Chat)
    message.chat.id = chat_id
    message.reply = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_command_service_default_commands(init_container: Container) -> None:
    """Test that default commands are allowed by default."""
    chat_id = 111
    service = init_container.command_service
    assert await service.is_command_allowed(chat_id, "debug") is True
    assert await service.is_command_allowed(chat_id, "allow_chat") is True
    assert await service.is_command_allowed(chat_id, "enable_cmd") is True
    assert await service.is_command_allowed(chat_id, "disable_cmd") is True


@pytest.mark.asyncio
async def test_command_service_feature_commands_disabled_by_default(init_container: Container) -> None:
    """Test that feature commands (tiktok, etc.) are disabled by default."""
    chat_id = 222
    service = init_container.command_service
    assert await service.is_command_allowed(chat_id, "tiktok") is False
    assert await service.is_command_allowed(chat_id, "twitter") is False


@pytest.mark.asyncio
async def test_enable_disable_command(init_container: Container) -> None:
    """Test enabling and disabling a command."""
    chat_id = 333
    service = init_container.command_service

    # Enable
    await service.enable_command(chat_id, "tiktok")
    assert await service.is_command_allowed(chat_id, "tiktok") is True

    # Disable
    await service.disable_command(chat_id, "tiktok")
    assert await service.is_command_allowed(chat_id, "tiktok") is False


@pytest.mark.asyncio
async def test_middleware_blocks_disabled_command(init_container: Container) -> None:
    """Test that middleware blocks disabled commands."""
    middleware = CommandConfigurationMiddleware()
    handler = AsyncMock()
    chat_id = random.randint(1000000, 9999999)  # noqa: S311
    message = create_mock_message("/tiktok", chat_id=chat_id)

    # Mock handler object with flags
    mock_handler_obj = MagicMock()
    mock_handler_obj.flags = {"command_name": "tiktok"}
    data = {"handler": mock_handler_obj}

    # Should be blocked
    result = await middleware(handler, message, data)
    assert result is None
    handler.assert_not_called()

    # Enable and try again
    await init_container.command_service.enable_command(chat_id, "tiktok")
    result = await middleware(handler, message, data)
    assert result is not None
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_enable_cmd_handler(init_container: Container) -> None:
    """Test the /enable_cmd handler."""
    handler = EnableCommand()
    chat_id = 555
    message = create_mock_message("/enable_cmd tiktok", chat_id=chat_id)

    await handler.handle(message, MagicMock())

    assert await init_container.command_service.is_command_allowed(chat_id, "tiktok") is True
    message.reply.assert_called_once_with("Command /tiktok enabled for this chat.")
