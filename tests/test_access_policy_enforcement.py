"""Tests for Access Policy enforcement and admin bypass rules."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aioresponses import aioresponses
from dishka import AsyncContainer

from hovorunv2.application.ports import CachePort, ChatStore
from hovorunv2.application.services.access_service import AccessService
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.domain.enums import ChatStatus, CommandName, Role
from hovorunv2.interface.telegram.middlewares import AccessMiddleware
from tests.conftest import create_mock_message, get_media_command, provision_role
from tests.utils.activation import activation_runtime, group_message, stub_telegram


@pytest.mark.asyncio
async def test_youtube_disabled_for_regular_user(init_container: AsyncContainer) -> None:
    """Test that YouTube command is NOT allowed for regular users when disabled."""
    chat_id = 789
    user_id = 123

    # 1. Setup services
    access_service = await init_container.get(AccessService)
    command_service = await init_container.get(CommandService)
    chat_status_service = await init_container.get(ChatStatusService)
    youtube_cmd = await get_media_command(init_container, CommandName.YOUTUBE)

    # 2. Access chat but DON'T enable youtube command
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

    # Verify youtube is disabled
    allowed = await command_service.get_allowed_commands(chat_id)
    assert CommandName.YOUTUBE not in allowed

    # 3. Check access using the policy
    bot = MagicMock(spec=Bot)
    is_allowed = await access_service.is_allowed(
        user_id=user_id,
        chat_id=chat_id,
        policy=youtube_cmd.policy,
        bot=bot,
        command_name=youtube_cmd.name,
    )

    assert is_allowed is False, "YouTube command should be disabled for regular user"


@pytest.mark.asyncio
async def test_youtube_admin_blocked_if_disabled(init_container: AsyncContainer) -> None:
    """Test that even admins are blocked if a toggleable command is explicitly disabled."""
    chat_id = 777
    user_id = 1  # Admin ID

    # 1. Setup services
    access_service = await init_container.get(AccessService)
    command_service = await init_container.get(CommandService)
    chat_status_service = await init_container.get(ChatStatusService)
    youtube_cmd = await get_media_command(init_container, CommandName.YOUTUBE)

    await provision_role(init_container, user_id, Role.OWNER)

    # 2. Access chat and DISABLE youtube
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)
    await command_service.disable_command(chat_id, CommandName.YOUTUBE)

    # 3. Check access
    bot = MagicMock(spec=Bot)
    is_allowed = await access_service.is_allowed(
        user_id=user_id,
        chat_id=chat_id,
        policy=youtube_cmd.policy,
        bot=bot,
        command_name=youtube_cmd.name,
    )

    assert is_allowed is False, "Admin should be blocked if toggleable command is disabled"


@pytest.mark.asyncio
async def test_youtube_auto_enable_on_access(init_container: AsyncContainer) -> None:
    """Test that YouTube command is automatically enabled when chat is approved via AccessCommand."""
    chat_id = 999
    # Provision the owner through durable role storage
    user_id = 1
    await provision_role(init_container, user_id, Role.OWNER)

    # 1. Setup services
    command_service = await init_container.get(CommandService)
    chats = await init_container.get(ChatStore)
    await chats.get_or_create_chat(chat_id)

    # 2. Trigger /allow_chat as admin
    async with activation_runtime(init_container) as runtime:
        with aioresponses() as http:
            stub_telegram(http)
            await runtime.command.handle(group_message(chat_id=chat_id, user_id=user_id).as_(runtime.bot), runtime.bot)

    # 3. Check if YouTube is enabled
    allowed = await command_service.get_allowed_commands(chat_id)
    assert CommandName.YOUTUBE in allowed, "YouTube should be auto-enabled by AccessCommand"


@pytest.mark.asyncio
async def test_access_middleware_enforcement(init_container: AsyncContainer) -> None:
    """Test that AccessMiddleware correctly enforces blocked state."""
    chat_id = 888
    user_id = 123

    # 1. Setup services and middleware
    access_service = await init_container.get(AccessService)
    middleware = AccessMiddleware(access_service, await init_container.get(CachePort))
    youtube_cmd = await get_media_command(init_container, CommandName.YOUTUBE)
    chat_status_service = await init_container.get(ChatStatusService)

    # 2. Access chat (youtube stays disabled)
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

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
