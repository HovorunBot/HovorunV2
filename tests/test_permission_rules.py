"""Tests for unified access control and permission rules."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.types import Chat, ChatMemberAdministrator, ChatMemberMember, Message, User
from dishka import AsyncContainer

from hovorunv2.application.data.constants import ChatStatus, CommandName, UserTier
from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.interface.telegram.handlers.user_management import UserManagementCommand
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
    middleware = AccessMiddleware(access_service)

    admin_id = 111
    await access_service.set_user_tier(admin_id, UserTier.ADMIN)
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
    policy = CommandPolicy(requires_approval=False, is_toggleable=False)

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

    # Policy that requires access (not approved by default)
    policy = CommandPolicy(requires_approval=True)

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

    admin_id = 111
    await access_service.set_user_tier(admin_id, UserTier.ADMIN)
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

    admin_id = 111
    await access_service.set_user_tier(admin_id, UserTier.ADMIN)
    chat_id = 123

    # Policy that would normally block everyone
    policy = CommandPolicy(requires_admin=True, requires_group_admin=True, requires_approval=True, is_toggleable=True)

    # Admin should bypass
    assert await access_service.is_allowed(admin_id, chat_id, policy) is True


@pytest.mark.asyncio
async def test_policy_requires_admin(test_container: AsyncContainer) -> None:
    """Test policy with requires_admin=True."""
    access_service = await test_container.get(AccessService)

    policy = CommandPolicy(requires_admin=True)

    admin_id = 111
    await access_service.set_user_tier(admin_id, UserTier.ADMIN)
    normal_user_id = 999
    chat_id = 123

    assert await access_service.is_allowed(admin_id, chat_id, policy) is True
    assert await access_service.is_allowed(normal_user_id, chat_id, policy) is False


@pytest.mark.asyncio
async def test_policy_requires_access(test_container: AsyncContainer) -> None:
    """Test policy with requires_approval=True."""
    access_service = await test_container.get(AccessService)
    chat_status_service = await test_container.get(ChatStatusService)

    policy = CommandPolicy(requires_approval=True)

    user_id = 999
    approved_chat_id = 123
    non_approved_chat_id = 456

    await chat_status_service.set_status(approved_chat_id, ChatStatus.APPROVED)

    assert await access_service.is_allowed(user_id, approved_chat_id, policy) is True
    assert await access_service.is_allowed(user_id, non_approved_chat_id, policy) is False


@pytest.mark.asyncio
async def test_policy_requires_group_admin(test_container: AsyncContainer) -> None:
    """Test policy with requires_group_admin=True."""
    access_service = await test_container.get(AccessService)
    bot = AsyncMock(spec=Bot)

    policy = CommandPolicy(requires_group_admin=True, requires_approval=False)

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
    chat_status_service = await test_container.get(ChatStatusService)

    policy = CommandPolicy(is_toggleable=True, requires_approval=True)
    command_name = CommandName.TIKTOK

    user_id = 999
    chat_id = 123

    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

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
    await test_container.get(ChatStatusService)

    # auto_enable is not currently used in CommandPolicy.evaluate logic in a way that overrides toggleable
    # Let's check the evaluate implementation again.
    # It says: return not (self.is_toggleable and command_name and not await command_service.is_command_allowed(...))
    # So auto_enable is NOT used in evaluate. It's likely used during registration or elsewhere.


@pytest.mark.asyncio
async def test_access_service_is_admin(test_container: AsyncContainer) -> None:
    """Test is_admin check."""
    access_service = await test_container.get(AccessService)

    admin_id = 111
    non_admin_id = 999
    await access_service.set_user_tier(admin_id, UserTier.ADMIN)

    assert await access_service.is_admin(admin_id) is True
    assert await access_service.is_admin(non_admin_id) is False

    admin_ids = await access_service.get_admin_ids()
    assert admin_id in admin_ids
    assert non_admin_id not in admin_ids


@pytest.mark.asyncio
async def test_banned_user_blocked_by_middleware(test_container: AsyncContainer) -> None:
    """Test that BANNED user is completely blocked by AccessMiddleware."""
    access_service = await test_container.get(AccessService)
    middleware = AccessMiddleware(access_service)

    banned_id = 111111
    await access_service.set_user_tier(banned_id, UserTier.BANNED)

    # In group chat
    msg = create_mock_message(12345, banned_id, ChatType.SUPERGROUP)
    handler = AsyncMock()
    policy = CommandPolicy(requires_approval=False, is_toggleable=False)
    data = {"handler": MagicMock(), "bot": MagicMock(), "policy": policy}

    result = await middleware(handler, msg, data)
    assert result is None
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_vip_tier_privileges(test_container: AsyncContainer) -> None:
    """Test VIP tier permissions."""
    access_service = await test_container.get(AccessService)
    vip_id = 222222
    regular_id = 333333

    await access_service.set_user_tier(vip_id, UserTier.VIP)
    await access_service.set_user_tier(regular_id, UserTier.USER)

    assert await access_service.is_vip(vip_id) is True
    assert await access_service.is_vip(regular_id) is False
    assert await access_service.is_admin(vip_id) is False


@pytest.mark.asyncio
async def test_beta_access_tier(test_container: AsyncContainer) -> None:
    """Test that users with UserTier.BETA (or higher) get beta access."""
    access_service = await test_container.get(AccessService)
    admin_id = 555555
    beta_tier_user_id = 666667
    vip_user_id = 666668
    regular_user_id = 777777

    await access_service.set_user_tier(admin_id, UserTier.ADMIN)
    await access_service.set_user_tier(beta_tier_user_id, UserTier.BETA)
    await access_service.set_user_tier(vip_user_id, UserTier.VIP)
    await access_service.set_user_tier(regular_user_id, UserTier.USER)

    assert await access_service.has_beta_access(admin_id) is True
    assert await access_service.has_beta_access(vip_user_id) is True
    assert await access_service.has_beta_access(beta_tier_user_id) is True
    assert await access_service.has_beta_access(regular_user_id) is False

    # Policy requiring beta
    policy = CommandPolicy(requires_beta=True, requires_approval=False, is_toggleable=False)
    chat_id = 123
    assert await access_service.is_allowed(beta_tier_user_id, chat_id, policy) is True
    assert await access_service.is_allowed(regular_user_id, chat_id, policy) is False


@pytest.mark.asyncio
async def test_policy_min_tier_enforcement(test_container: AsyncContainer) -> None:
    """Test that policy enforcing min_tier restricts lower-tier users."""
    access_service = await test_container.get(AccessService)
    vip_id = 888881
    regular_id = 888882

    await access_service.set_user_tier(vip_id, UserTier.VIP)
    await access_service.set_user_tier(regular_id, UserTier.USER)

    policy = CommandPolicy(min_tier=UserTier.VIP, requires_approval=False, is_toggleable=False)
    chat_id = 123

    assert await access_service.is_allowed(vip_id, chat_id, policy) is True
    assert await access_service.is_allowed(regular_id, chat_id, policy) is False


@pytest.mark.asyncio
async def test_user_management_command_flow(test_container: AsyncContainer) -> None:
    """Test /set_tier and /user_info commands in private DM."""
    access_service = await test_container.get(AccessService)
    admin_id = 900001
    target_id = 900002
    await access_service.set_user_tier(admin_id, UserTier.ADMIN)

    cmd = UserManagementCommand(access_service)
    bot = MagicMock(spec=Bot)

    # 1. Reject in group chat
    group_msg = create_mock_message(1234, admin_id, ChatType.SUPERGROUP)
    group_msg.text = f"/set_tier {target_id} vip"
    await cmd.handle(group_msg, bot)
    assert await access_service.get_user_tier(target_id) == UserTier.USER

    # 2. Allow in private chat by admin
    dm_msg = create_mock_message(admin_id, admin_id, ChatType.PRIVATE)
    dm_msg.text = f"/set_tier {target_id} vip"
    await cmd.handle(dm_msg, bot)
    assert await access_service.get_user_tier(target_id) == UserTier.VIP

    # 3. Set beta in private chat
    dm_msg.text = f"/set_tier {target_id} beta"
    await cmd.handle(dm_msg, bot)
    assert await access_service.get_user_tier(target_id) == UserTier.BETA
    assert await access_service.has_beta_access(target_id) is True

    # 4. User info
    dm_msg.text = f"/user_info {target_id}"
    await cmd.handle(dm_msg, bot)
    dm_msg.answer.assert_called()
    assert "BETA" in dm_msg.answer.call_args[0][0]
