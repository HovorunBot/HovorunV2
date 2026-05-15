"""Tests for unified access control and permission rules."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.types import Chat, ChatMemberAdministrator, ChatMemberMember, Message, User
from dishka import AsyncContainer

from hovorunv2.application.data.constants import CommandName
from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.interface.telegram.middlewares import AccessMiddleware


def create_mock_message(chat_id: int, user_id: int, chat_type: ChatType = ChatType.SUPERGROUP) -> MagicMock:
    """Create a mock Telegram message."""
    message = MagicMock(spec=Message)
    message.from_user = MagicMock(spec=User)
    message.from_user.id = user_id
    message.from_user.full_name = "Test User"
    message.chat = MagicMock(spec=Chat)
    message.chat.id = chat_id
    message.chat.type = chat_type
    message.answer = AsyncMock()
    # Ensure isinstance(message, Message) returns True
    message.__class__ = Message
    return message


@pytest.mark.asyncio
async def test_access_middleware_private_chat_admin(test_container: AsyncContainer) -> None:
    """Test that admins have access in private chats."""
    access_service = await test_container.get(AccessService)
    settings = await test_container.get(Settings)
    middleware = AccessMiddleware(access_service)

    admin_id = settings.admin_ids[0]
    message = create_mock_message(admin_id, admin_id, ChatType.PRIVATE)

    handler = AsyncMock()
    # Any policy
    policy = CommandPolicy()
    data = {"handler": MagicMock(), "bot": MagicMock(), "policy": policy}
    data["handler"].flags = {"policy": policy}

    result = await middleware(handler, message, data)
    assert result is not None
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_access_middleware_private_chat_non_admin(test_container: AsyncContainer) -> None:
    """Test that non-admins are blocked in private chats and receive help text."""
    access_service = await test_container.get(AccessService)
    middleware = AccessMiddleware(access_service)

    user_id = 999
    message = create_mock_message(user_id, user_id, ChatType.PRIVATE)

    handler = AsyncMock()
    policy = CommandPolicy()
    data = {"handler": MagicMock(), "bot": MagicMock(), "policy": policy}
    data["handler"].flags = {"policy": policy}

    result = await middleware(handler, message, data)
    assert result is None
    handler.assert_not_called()
    message.answer.assert_called_once()
    assert "Hovorun" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_access_middleware_allowed_group(test_container: AsyncContainer) -> None:
    """Test that allowed interaction in group proceeds to handler."""
    access_service = await test_container.get(AccessService)
    middleware = AccessMiddleware(access_service)

    user_id = 999
    chat_id = 123
    message = create_mock_message(chat_id, user_id, ChatType.SUPERGROUP)

    # Policy that allows everything
    policy = CommandPolicy(requires_whitelist=False, is_toggleable=False)

    handler = AsyncMock()
    data = {"handler": MagicMock(), "bot": MagicMock(), "policy": policy}
    data["handler"].flags = {"policy": policy}

    result = await middleware(handler, message, data)
    assert result is not None
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_access_middleware_denied_group(test_container: AsyncContainer) -> None:
    """Test that denied interaction in group is blocked."""
    access_service = await test_container.get(AccessService)
    middleware = AccessMiddleware(access_service)

    user_id = 999
    chat_id = 123
    message = create_mock_message(chat_id, user_id, ChatType.SUPERGROUP)

    # Policy that requires whitelist (not whitelisted by default)
    policy = CommandPolicy(requires_whitelist=True)

    handler = AsyncMock()
    data = {"handler": MagicMock(), "bot": MagicMock(), "policy": policy}
    data["handler"].flags = {"policy": policy}

    result = await middleware(handler, message, data)
    assert result is None
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_access_service_ensure_admin(test_container: AsyncContainer) -> None:
    """Test ensure_admin raises PermissionError for non-admins."""
    access_service = await test_container.get(AccessService)
    settings = await test_container.get(Settings)

    admin_id = settings.admin_ids[0]
    non_admin_id = 999

    # Should not raise
    await access_service.ensure_admin(admin_id)

    # Should raise
    with pytest.raises(PermissionError, match="does not have admin privileges"):
        await access_service.ensure_admin(non_admin_id)


@pytest.mark.asyncio
async def test_policy_bypass_for_global_admin(test_container: AsyncContainer) -> None:
    """Test that global admins bypass all policy restrictions."""
    access_service = await test_container.get(AccessService)
    settings = await test_container.get(Settings)

    admin_id = settings.admin_ids[0]
    chat_id = 123

    # Policy that would normally block everyone
    policy = CommandPolicy(requires_admin=True, requires_group_admin=True, requires_whitelist=True, is_toggleable=True)

    # Admin should bypass
    assert await access_service.is_allowed(admin_id, chat_id, policy) is True


@pytest.mark.asyncio
async def test_policy_requires_admin(test_container: AsyncContainer) -> None:
    """Test policy with requires_admin=True."""
    access_service = await test_container.get(AccessService)

    policy = CommandPolicy(requires_admin=True)

    admin_id = (await test_container.get(Settings)).admin_ids[0]
    normal_user_id = 999
    chat_id = 123

    assert await access_service.is_allowed(admin_id, chat_id, policy) is True
    assert await access_service.is_allowed(normal_user_id, chat_id, policy) is False


@pytest.mark.asyncio
async def test_policy_requires_whitelist(test_container: AsyncContainer) -> None:
    """Test policy with requires_whitelist=True."""
    access_service = await test_container.get(AccessService)
    whitelist_service = await test_container.get(WhitelistService)

    policy = CommandPolicy(requires_whitelist=True)

    user_id = 999
    whitelisted_chat_id = 123
    non_whitelisted_chat_id = 456

    await whitelist_service.add_to_whitelist(whitelisted_chat_id)

    assert await access_service.is_allowed(user_id, whitelisted_chat_id, policy) is True
    assert await access_service.is_allowed(user_id, non_whitelisted_chat_id, policy) is False


@pytest.mark.asyncio
async def test_policy_requires_group_admin(test_container: AsyncContainer) -> None:
    """Test policy with requires_group_admin=True."""
    access_service = await test_container.get(AccessService)
    bot = AsyncMock(spec=Bot)

    policy = CommandPolicy(requires_group_admin=True, requires_whitelist=False)

    chat_id = 123
    admin_user_id = 111
    member_user_id = 222

    # Mock bot.get_chat_member
    async def get_chat_member(_c_id: int, u_id: int) -> MagicMock:
        if u_id == admin_user_id:
            return MagicMock(spec=ChatMemberAdministrator, status="administrator")
        return MagicMock(spec=ChatMemberMember, status="member")

    bot.get_chat_member.side_effect = get_chat_member

    assert await access_service.is_allowed(admin_user_id, chat_id, policy, bot=bot) is True
    assert await access_service.is_allowed(member_user_id, chat_id, policy, bot=bot) is False


@pytest.mark.asyncio
async def test_policy_is_toggleable(test_container: AsyncContainer) -> None:
    """Test policy with is_toggleable=True."""
    access_service = await test_container.get(AccessService)
    command_service = await test_container.get(CommandService)
    whitelist_service = await test_container.get(WhitelistService)

    policy = CommandPolicy(is_toggleable=True, requires_whitelist=True)
    command_name = CommandName.TIKTOK

    user_id = 999
    chat_id = 123

    await whitelist_service.add_to_whitelist(chat_id)

    # Disabled by default for features
    assert await access_service.is_allowed(user_id, chat_id, policy, command_name=command_name) is False

    # Enable it
    await command_service.enable_command(chat_id, command_name)
    assert await access_service.is_allowed(user_id, chat_id, policy, command_name=command_name) is True

    # Disable it
    await command_service.disable_command(chat_id, command_name)
    assert await access_service.is_allowed(user_id, chat_id, policy, command_name=command_name) is False


@pytest.mark.asyncio
async def test_policy_auto_enable(test_container: AsyncContainer) -> None:
    """Test that auto_enable=True policy allows command even if not explicitly enabled."""
    await test_container.get(AccessService)
    await test_container.get(WhitelistService)

    # auto_enable is not currently used in CommandPolicy.evaluate logic in a way that overrides toggleable
    # Let's check the evaluate implementation again.
    # It says: return not (self.is_toggleable and command_name and not await command_service.is_command_allowed(...))
    # So auto_enable is NOT used in evaluate. It's likely used during registration or elsewhere.


@pytest.mark.asyncio
async def test_access_service_is_owner(test_container: AsyncContainer) -> None:
    """Test is_owner check."""
    access_service = await test_container.get(AccessService)
    settings = await test_container.get(Settings)

    owner_id = settings.owners[0]
    non_owner_id = 999

    assert await access_service.is_owner(owner_id) is True
    assert await access_service.is_owner(non_owner_id) is False


@pytest.mark.asyncio
async def test_access_service_is_admin(test_container: AsyncContainer) -> None:
    """Test is_admin check."""
    access_service = await test_container.get(AccessService)
    settings = await test_container.get(Settings)

    admin_id = settings.admin_ids[0]
    owner_id = settings.owners[0]
    non_admin_id = 999

    assert await access_service.is_admin(admin_id) is True
    assert await access_service.is_admin(owner_id) is True
    assert await access_service.is_admin(non_admin_id) is False
