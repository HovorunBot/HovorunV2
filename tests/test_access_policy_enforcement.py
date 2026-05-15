"""Tests for Access Policy enforcement and admin bypass rules."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User
from dishka import AsyncContainer

from hovorunv2.application.data.constants import ChatStatus, CommandName
from hovorunv2.application.services.access_service import AccessService
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.interface.telegram.handlers.access import AccessCommand
from hovorunv2.interface.telegram.handlers.youtube import YoutubeShortsCommand
from hovorunv2.interface.telegram.middlewares import AccessMiddleware


def create_mock_message(text: str, chat_id: int = 456, user_id: int = 123) -> MagicMock:
    """Create a mock Telegram message."""
    message = MagicMock(spec=Message)
    message.text = text
    message.from_user = MagicMock(spec=User)
    message.from_user.id = user_id
    message.from_user.is_bot = False
    message.from_user.full_name = "Mock User"
    message.from_user.username = "mockuser"
    message.chat = MagicMock(spec=Chat)
    message.chat.id = chat_id
    message.chat.type = "group"
    message.chat.title = "Mock Group"
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_youtube_disabled_for_regular_user(init_container: AsyncContainer) -> None:
    """Test that YouTube command is NOT allowed for regular users when disabled."""
    chat_id = 789
    user_id = 123
    platform = "telegram"

    # 1. Setup services
    access_service = await init_container.get(AccessService)
    command_service = await init_container.get(CommandService)
    chat_status_service = await init_container.get(ChatStatusService)
    youtube_cmd = await init_container.get(YoutubeShortsCommand)

    # 2. Access chat but DON'T enable youtube command
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED, platform)

    # Verify youtube is disabled
    allowed = await command_service.get_allowed_commands(chat_id, platform)
    assert CommandName.YOUTUBE not in allowed

    # 3. Check access using the policy
    bot = MagicMock(spec=Bot)
    is_allowed = await access_service.is_allowed(
        user_id=user_id,
        chat_id=chat_id,
        policy=youtube_cmd.policy,
        bot=bot,
        command_name=youtube_cmd.name,
        platform=platform,
    )

    assert is_allowed is False, "YouTube command should be disabled for regular user"


@pytest.mark.asyncio
async def test_youtube_admin_blocked_if_disabled(init_container: AsyncContainer) -> None:
    """Test that even admins are blocked if a toggleable command is explicitly disabled."""
    chat_id = 777
    user_id = 1  # Admin ID
    platform = "telegram"

    # 1. Setup services
    access_service = await init_container.get(AccessService)
    command_service = await init_container.get(CommandService)
    chat_status_service = await init_container.get(ChatStatusService)
    youtube_cmd = await init_container.get(YoutubeShortsCommand)
    settings = await init_container.get(Settings)

    if user_id not in settings.owners:
        settings.owners.append(user_id)

    # 2. Access chat and DISABLE youtube
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED, platform)
    await command_service.disable_command(chat_id, CommandName.YOUTUBE, platform)

    # 3. Check access
    bot = MagicMock(spec=Bot)
    is_allowed = await access_service.is_allowed(
        user_id=user_id,
        chat_id=chat_id,
        policy=youtube_cmd.policy,
        bot=bot,
        command_name=youtube_cmd.name,
        platform=platform,
    )

    assert is_allowed is False, "Admin should be blocked if toggleable command is disabled"


@pytest.mark.asyncio
async def test_youtube_auto_enable_on_access(init_container: AsyncContainer) -> None:
    """Test that YouTube command is automatically enabled when chat is approved via AccessCommand."""
    chat_id = 999
    # Ensure owner ID is in settings
    settings = await init_container.get(Settings)
    user_id = 1
    if user_id not in settings.owners:
        settings.owners.append(user_id)

    platform = "telegram"

    # 1. Setup services
    allow_cmd = await init_container.get(AccessCommand)
    command_service = await init_container.get(CommandService)

    # 2. Trigger /allow_chat as admin
    message = create_mock_message("/allow_chat", chat_id=chat_id, user_id=user_id)
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    await allow_cmd.handle(message, bot)

    # 3. Check if YouTube is enabled
    allowed = await command_service.get_allowed_commands(chat_id, platform)
    assert CommandName.YOUTUBE in allowed, "YouTube should be auto-enabled by AccessCommand"


@pytest.mark.asyncio
async def test_access_middleware_enforcement(init_container: AsyncContainer) -> None:
    """Test that AccessMiddleware correctly enforces blocked state."""
    chat_id = 888
    user_id = 123
    platform = "telegram"

    # 1. Setup services and middleware
    access_service = await init_container.get(AccessService)
    middleware = AccessMiddleware(access_service)
    youtube_cmd = await init_container.get(YoutubeShortsCommand)
    chat_status_service = await init_container.get(ChatStatusService)

    # 2. Access chat (youtube stays disabled)
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED, platform)

    # 3. Create mock handler and event
    handler = AsyncMock(return_value="handled")
    event = create_mock_message("https://youtube.com/shorts/123", chat_id=chat_id, user_id=user_id)

    # 4. Create data with flags
    data = {"bot": MagicMock(spec=Bot), "policy": youtube_cmd.policy, "command_name": youtube_cmd.name}

    # 5. Call middleware
    result = await middleware(handler, event, data)

    # 6. Verify blocked
    assert result is None, "Middleware should return None when access is denied"
    handler.assert_not_called()
