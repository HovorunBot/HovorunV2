"""Tests for the CommandConfiguration system."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Chat, Message, User
from dishka import AsyncContainer

from hovorunv2.application.services.access_service import (
    AccessService,
    CommandPolicy,
)
from hovorunv2.application.services.cleanup_service import CleanupService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.infrastructure.config import settings
from hovorunv2.interface.telegram.callbacks import CmdConfigCallback
from hovorunv2.interface.telegram.handlers import commands_config
from hovorunv2.interface.telegram.handlers.commands_config import ConfigCommandsCommand
from hovorunv2.interface.telegram.middlewares import AccessMiddleware


def create_mock_message(text: str, chat_id: int = 456, user_id: int | None = None) -> MagicMock:
    """Create a mock Telegram message."""
    message = MagicMock(spec=Message)
    message.text = text
    message.from_user = MagicMock(spec=User)
    message.from_user.id = user_id or settings.admin_ids[0]
    message.chat = MagicMock(spec=Chat)
    message.chat.id = chat_id
    message.chat.type = ChatType.SUPERGROUP
    message.answer = AsyncMock()
    # Ensure isinstance(message, Message) returns True
    message.__class__ = Message
    return message


def create_mock_callback(user_id: int, chat_id: int = 456, message_id: int = 789) -> MagicMock:
    """Create a mock Telegram callback query."""
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = MagicMock(spec=User)
    callback.from_user.id = user_id
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock(spec=Chat)
    callback.message.chat.id = chat_id
    callback.message.chat.type = ChatType.SUPERGROUP
    callback.message.message_id = message_id
    callback.answer = AsyncMock()
    # Ensure isinstance(callback, CallbackQuery) returns True
    callback.__class__ = CallbackQuery
    return callback


@pytest.mark.asyncio
async def test_middleware_blocks_unauthorized_callback(init_container: AsyncContainer) -> None:
    """Test that middleware blocks unauthorized users from clicking toggles."""
    access_service = await init_container.get(AccessService)
    middleware = AccessMiddleware(access_service)
    handler = AsyncMock()
    chat_id = 999
    user_id = 12345  # Non-admin

    callback = create_mock_callback(user_id, chat_id=chat_id)

    # Policy requires group admin
    policy = CommandPolicy(requires_group_admin=True)

    # Mock bot to return non-admin status
    bot = AsyncMock()
    bot.get_chat_member.return_value = MagicMock(status="member")

    data = {"handler": MagicMock(), "bot": bot}
    data["handler"].flags = {"policy": policy}

    result = await middleware(handler, callback, data)
    assert result is None
    handler.assert_not_called()
    callback.answer.assert_called_with("🚫 You don't have permission to do this.", show_alert=True)


@pytest.mark.asyncio
async def test_config_callback_resets_ttl(init_container: AsyncContainer) -> None:
    """Test that clicking a toggle resets the inactivity TTL."""
    handler = await init_container.get(ConfigCommandsCommand)
    cleanup_service = await init_container.get(CleanupService)

    chat_id = 555
    message_id = 777
    user_id = settings.admin_ids[0]

    callback = create_mock_callback(user_id, chat_id=chat_id, message_id=message_id)
    callback.message.edit_reply_markup = AsyncMock()

    callback_data = CmdConfigCallback(cmd_name="tiktok")

    with patch.object(cleanup_service, "reset_ttl", new_callable=AsyncMock) as mock_reset:
        await handler.handle_callback(callback, callback_data)
        mock_reset.assert_called_once_with(chat_id, message_id, commands_config.CONFIG_WINDOW_TTL)


@pytest.mark.asyncio
async def test_config_message_self_destructs(init_container: AsyncContainer) -> None:
    """Test that the config message is deleted when TTL expires."""
    handler = await init_container.get(ConfigCommandsCommand)
    cleanup_service = await init_container.get(CleanupService)

    # Mock bot
    bot = AsyncMock()
    chat_id = 555
    message_id = 777

    # Mock message
    message = create_mock_message("/config_cmds", chat_id=chat_id)
    config_msg = MagicMock(spec=Message)
    config_msg.message_id = message_id
    message.answer.return_value = config_msg

    # We need to test if register_message is called
    with patch.object(cleanup_service, "register_message", new_callable=AsyncMock) as mock_reg:
        await handler.handle(message, bot)
        mock_reg.assert_called_once_with(bot, chat_id, message_id, commands_config.CONFIG_WINDOW_TTL)


@pytest.mark.asyncio
async def test_command_service_default_commands(init_container: AsyncContainer) -> None:
    """Test that default commands are allowed by default."""
    chat_id = 111
    service = await init_container.get(CommandService)
    assert await service.is_command_allowed(chat_id, "debug") is True
    assert await service.is_command_allowed(chat_id, "allow_chat") is True
    assert await service.is_command_allowed(chat_id, "config_cmds") is True
    assert await service.is_command_allowed(chat_id, "config_lang") is True


@pytest.mark.asyncio
async def test_command_service_feature_commands_disabled_by_default(init_container: AsyncContainer) -> None:
    """Test that feature commands (tiktok, etc.) are disabled by default."""
    chat_id = 222
    service = await init_container.get(CommandService)
    assert await service.is_command_allowed(chat_id, "tiktok") is False
    assert await service.is_command_allowed(chat_id, "twitter") is False


@pytest.mark.asyncio
async def test_enable_disable_command(init_container: AsyncContainer) -> None:
    """Test enabling and disabling a command."""
    chat_id = 333
    service = await init_container.get(CommandService)

    # Enable
    await service.enable_command(chat_id, "tiktok")
    assert await service.is_command_allowed(chat_id, "tiktok") is True

    # Disable
    await service.disable_command(chat_id, "tiktok")
    assert await service.is_command_allowed(chat_id, "tiktok") is False


@pytest.mark.asyncio
async def test_middleware_blocks_disabled_command(init_container: AsyncContainer) -> None:
    """Test that middleware blocks disabled commands."""
    access_service = await init_container.get(AccessService)
    cmd_service = await init_container.get(CommandService)
    middleware = AccessMiddleware(access_service)
    handler = AsyncMock()
    chat_id = 999999
    # Non-admin user to trigger toggleable check
    user_id = 12345
    message = create_mock_message("/tiktok", chat_id=chat_id, user_id=user_id)

    # Mock handler object with flags
    mock_handler_obj = MagicMock()
    policy = CommandPolicy(requires_admin=False, requires_whitelist=True, is_toggleable=True)
    mock_handler_obj.flags = {"command_name": "tiktok", "policy": policy}
    data = {"handler": mock_handler_obj, "bot": MagicMock()}

    # Should be blocked because chat is not whitelisted by default in tests
    result = await middleware(handler, message, data)
    assert result is None
    handler.assert_not_called()

    # Enable and try again

    whitelist_service = await init_container.get(WhitelistService)
    await whitelist_service.add_to_whitelist(chat_id)

    await cmd_service.enable_command(chat_id, "tiktok")
    result = await middleware(handler, message, data)
    assert result is not None
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_config_cmds_handler(init_container: AsyncContainer) -> None:
    """Test the /config_cmds handler opens keyboard."""
    handler = await init_container.get(ConfigCommandsCommand)
    chat_id = 555
    message = create_mock_message("/config_cmds", chat_id=chat_id)

    await handler.handle(message, MagicMock())

    message.answer.assert_called_once()
    args, _kwargs = message.answer.call_args
    assert "Bot Command Configuration" in args[0]
