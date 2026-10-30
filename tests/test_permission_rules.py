"""Tests for unified access control and permission rules."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.types import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    User,
)
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from hovorunv2.application.ports import CachePort
from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import (
    DEFAULT_COMMANDS,
    PROVIDER_SPLIT_COMMANDS,
    ChatStatus,
    CommandName,
    Role,
)
from hovorunv2.infrastructure.config import Settings
from hovorunv2.interface.telegram.handlers.bot_join import BotJoinHandler
from hovorunv2.interface.telegram.middlewares import AccessMiddleware
from tests.conftest import provision_role, verify_group_admins


def create_mock_message(chat_id: int, user_id: int, chat_type: ChatType = ChatType.SUPERGROUP) -> MagicMock:
    """Create a mock Telegram message."""
    message = MagicMock(spec=Message)
    message.from_user = MagicMock(spec=User)
    message.from_user.id = user_id
    message.from_user.is_bot = False
    message.sender_chat = None
    message.from_user.full_name = "Test User"
    message.chat = MagicMock(spec=Chat)
    message.chat.id = chat_id
    message.chat.type = chat_type
    message.answer = AsyncMock()
    # Ensure isinstance(message, Message) returns True
    message.__class__ = Message
    return message


def create_mock_event(chat_id: int, status: str) -> MagicMock:
    """Create mock ChatMemberUpdated event."""
    event = MagicMock(spec=ChatMemberUpdated)
    event.chat = MagicMock(spec=Chat)
    event.chat.id = chat_id
    event.chat.type = "supergroup"
    event.new_chat_member = MagicMock()
    event.new_chat_member.status = status
    return event


async def get_persisted_chat_and_commands(
    session_maker: async_sessionmaker,
    chat_id: int,
) -> tuple[ChatStatus | None, set[str]]:
    """Directly query real SQLite database without using internal application services."""
    async with session_maker() as session:
        stmt = select(ChatDB).where(ChatDB.chat_id == chat_id).options(selectinload(ChatDB.commands))
        res = await session.execute(stmt)
        chat = res.scalar_one_or_none()
        if not chat:
            return None, set()
        status = ChatStatus(chat.status)
        commands = {cmd.name for cmd in chat.commands}
        return status, commands


async def test_access_middleware_private_chat_admin(test_container: AsyncContainer) -> None:
    """Test that admins have access in private chats."""
    access_service = await test_container.get(AccessService)
    middleware = AccessMiddleware(
        access_service, await test_container.get(CachePort), await test_container.get(Settings)
    )

    admin_id = await provision_role(test_container)
    message = create_mock_message(admin_id, admin_id, ChatType.PRIVATE)

    handler = AsyncMock()
    # Any policy
    policy = CommandPolicy()
    data = {"handler": MagicMock(), "bot": MagicMock(), "policy": policy}
    data["handler"].flags = {"policy": policy}

    result = await middleware(handler, message, data)
    assert result is not None
    handler.assert_called_once()


async def test_access_middleware_private_chat_non_admin(test_container: AsyncContainer) -> None:
    """Test that non-admins are blocked in private chats and receive help text."""
    access_service = await test_container.get(AccessService)
    settings = await test_container.get(Settings)
    middleware = AccessMiddleware(access_service, await test_container.get(CachePort), settings)

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
    assert settings.project_description in message.answer.call_args[0][0]


async def test_access_middleware_allowed_group(test_container: AsyncContainer) -> None:
    """Test that allowed interaction in group proceeds to handler."""
    access_service = await test_container.get(AccessService)
    settings = await test_container.get(Settings)
    middleware = AccessMiddleware(access_service, await test_container.get(CachePort), settings)

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


async def test_access_middleware_denied_group(test_container: AsyncContainer) -> None:
    """Test that denied interaction in group is blocked."""
    access_service = await test_container.get(AccessService)
    settings = await test_container.get(Settings)
    middleware = AccessMiddleware(access_service, await test_container.get(CachePort), settings)

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


async def test_policy_bypass_for_global_admin(test_container: AsyncContainer) -> None:
    """Test that global admins bypass all policy restrictions."""
    access_service = await test_container.get(AccessService)

    admin_id = await provision_role(test_container)
    chat_id = 123

    # Policy that would normally block everyone
    policy = CommandPolicy(requires_admin=True, requires_group_admin=True, requires_approval=True, is_toggleable=True)

    # Admin should bypass
    assert await access_service.is_allowed(admin_id, chat_id, policy) is True


async def test_policy_requires_admin(test_container: AsyncContainer) -> None:
    """Test policy with requires_admin=True."""
    access_service = await test_container.get(AccessService)

    policy = CommandPolicy(requires_admin=True)

    admin_id = await provision_role(test_container)
    normal_user_id = 999
    chat_id = 123

    assert await access_service.is_allowed(admin_id, chat_id, policy) is True
    assert await access_service.is_allowed(normal_user_id, chat_id, policy) is False


async def test_policy_requires_access(test_container: AsyncContainer) -> None:
    """Test policy with requires_approval=True."""
    access_service = await test_container.get(AccessService)
    chat_status_service = await test_container.get(ChatStatusService)

    policy = CommandPolicy(requires_approval=True)

    user_id = 999
    approved_chat_id = 123
    non_approved_chat_id = 456

    await verify_group_admins(test_container, approved_chat_id)
    await chat_status_service.set_status(approved_chat_id, ChatStatus.APPROVED)

    assert await access_service.is_allowed(user_id, approved_chat_id, policy) is True
    assert await access_service.is_allowed(user_id, non_approved_chat_id, policy) is False


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


async def test_policy_is_toggleable(test_container: AsyncContainer) -> None:
    """Test policy with is_toggleable=True."""
    access_service = await test_container.get(AccessService)
    command_service = await test_container.get(CommandService)
    chat_status_service = await test_container.get(ChatStatusService)

    policy = CommandPolicy(is_toggleable=True, requires_approval=True)
    command_name = CommandName.TIKTOK

    user_id = 999
    chat_id = 123

    await verify_group_admins(test_container, chat_id)
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

    # Disabled by default for features
    assert await access_service.is_allowed(user_id, chat_id, policy, command_name=command_name) is False

    # Enable it
    await command_service.enable_command(chat_id, command_name)
    assert await access_service.is_allowed(user_id, chat_id, policy, command_name=command_name) is True

    # Disable it
    await command_service.disable_command(chat_id, command_name)
    assert await access_service.is_allowed(user_id, chat_id, policy, command_name=command_name) is False

    # Toggle it on
    assert await command_service.toggle(chat_id, command_name) is True
    assert await access_service.is_allowed(user_id, chat_id, policy, command_name=command_name) is True

    # Toggle it off
    assert await command_service.toggle(chat_id, command_name) is False
    assert await access_service.is_allowed(user_id, chat_id, policy, command_name=command_name) is False


async def test_chat_status_service_approve(test_container: AsyncContainer) -> None:
    """Test that chat_status_service.approve sets APPROVED and enables default commands."""
    chat_status_service = await test_container.get(ChatStatusService)
    command_service = await test_container.get(CommandService)
    chat_id = 12345

    await verify_group_admins(test_container, chat_id)
    await chat_status_service.approve(chat_id)

    assert await chat_status_service.is_approved(chat_id) is True
    allowed = await command_service.get_allowed_commands(chat_id)
    assert CommandName.YOUTUBE in allowed
    assert CommandName.TIKTOK in allowed


async def test_access_service_is_owner(test_container: AsyncContainer) -> None:
    """Test is_owner check."""
    access_service = await test_container.get(AccessService)

    owner_id = await provision_role(test_container, 201, Role.OWNER)
    non_owner_id = 999

    assert await access_service.is_owner(owner_id) is True
    assert await access_service.is_owner(non_owner_id) is False


async def test_access_service_is_admin(test_container: AsyncContainer) -> None:
    """Test is_admin check."""
    access_service = await test_container.get(AccessService)

    admin_id = await provision_role(test_container)
    owner_id = await provision_role(test_container, 201, Role.OWNER)
    non_admin_id = 999

    assert await access_service.is_admin(admin_id) is True
    assert await access_service.is_admin(owner_id) is True
    assert await access_service.is_admin(non_admin_id) is False


async def test_command_service_is_capture_allowed(test_container: AsyncContainer) -> None:
    """Test is_capture_allowed for video and post commands."""
    command_service = await test_container.get(CommandService)
    chat_id = 998877

    # New empty configurations are disabled until an intentional selection.
    assert await command_service.is_capture_allowed(chat_id, CommandName.TWITTER, is_video=True) is False
    assert await command_service.is_capture_allowed(chat_id, CommandName.TWITTER, is_video=False) is False

    # Enable twitter_post only (now chat has explicit configuration)
    await command_service.enable_command(chat_id, CommandName.TWITTER_POST)
    assert await command_service.is_capture_allowed(chat_id, CommandName.TWITTER, is_video=True) is False
    assert await command_service.is_capture_allowed(chat_id, CommandName.TWITTER, is_video=False) is True

    # Enable twitter_video
    await command_service.enable_command(chat_id, CommandName.TWITTER_VIDEO)
    assert await command_service.is_capture_allowed(chat_id, CommandName.TWITTER, is_video=True) is True
    assert await command_service.is_capture_allowed(chat_id, CommandName.TWITTER, is_video=False) is True


# ==============================================================================
# Legacy Characterization Tests
# ==============================================================================


async def test_characterize_legacy_group_status_matrix(test_container: AsyncContainer) -> None:
    """Characterize legacy permission evaluation across all group statuses in real SQLite.

    Legacy Matrix:
    - Stored statuses: APPROVED, PENDING, REJECTED, BANNED, INACTIVE, and UNAUTHORIZED (nonexistent).
    - Requires approval (policy.requires_approval=True):
      Only APPROVED grants access to regular users; all other statuses deny access.
    - Exempt policy (policy.requires_approval=False):
      Access is GRANTED to regular users across ALL statuses (including BANNED and REJECTED)!
    - Global Admin bypass:
      Global admin bypasses requires_approval across ALL statuses (including BANNED and REJECTED)!
    """
    session_maker = await test_container.get(async_sessionmaker)
    access_service = await test_container.get(AccessService)
    chat_status_service = await test_container.get(ChatStatusService)

    admin_id = await provision_role(test_container)
    regular_user_id = 9999

    policy_requires_approval = CommandPolicy(requires_approval=True, is_toggleable=False)
    policy_exempt = CommandPolicy(requires_approval=False, is_toggleable=False)

    statuses_to_test = [
        ChatStatus.APPROVED,
        ChatStatus.PENDING,
        ChatStatus.REJECTED,
        ChatStatus.BANNED,
        ChatStatus.INACTIVE,
    ]

    base_chat_id = 10000
    for idx, status in enumerate(statuses_to_test):
        chat_id = base_chat_id + idx
        # Persist chat with target status
        await verify_group_admins(test_container, chat_id)
        await chat_status_service.set_status(chat_id, status)

        # 1. Assert real SQLite state directly
        persisted_status, _ = await get_persisted_chat_and_commands(session_maker, chat_id)
        assert persisted_status == status

        # 2. Service-level status reporting
        assert await chat_status_service.get_status(chat_id) == status
        assert await chat_status_service.is_approved(chat_id) is (status == ChatStatus.APPROVED)

        # 3. Regular user evaluation: requires_approval
        is_allowed_regular = await access_service.is_allowed(regular_user_id, chat_id, policy_requires_approval)
        assert is_allowed_regular is (status == ChatStatus.APPROVED)

        # 4. Regular user evaluation: policy exempt from approval (legacy quirk: all allowed, even BANNED)
        is_allowed_exempt = await access_service.is_allowed(regular_user_id, chat_id, policy_exempt)
        assert is_allowed_exempt is True

        # 5. Global admin evaluation: bypasses approval check across all statuses (legacy quirk)
        is_allowed_admin = await access_service.is_allowed(admin_id, chat_id, policy_requires_approval)
        assert is_allowed_admin is True

    # Also characterize nonexistent/unrecorded chat
    unrecorded_chat_id = 99999
    persisted_status, _ = await get_persisted_chat_and_commands(session_maker, unrecorded_chat_id)
    assert persisted_status is None
    assert await chat_status_service.get_status(unrecorded_chat_id) == ChatStatus.UNAUTHORIZED
    assert await chat_status_service.is_approved(unrecorded_chat_id) is False
    assert await access_service.is_allowed(regular_user_id, unrecorded_chat_id, policy_requires_approval) is False
    assert await access_service.is_allowed(regular_user_id, unrecorded_chat_id, policy_exempt) is True
    assert await access_service.is_allowed(admin_id, unrecorded_chat_id, policy_requires_approval) is True


async def test_characterize_legacy_empty_command_set_capture(test_container: AsyncContainer) -> None:
    """Characterize legacy capture and command check behavior for an empty command set.

    Legacy Behavior / Quirk:
    - When no commands are associated with a chat in SQLite (`chat_commands` is empty):
      - `command_service.get_allowed_commands(chat_id)` returns an empty set.
      - `is_command_allowed` for media commands (e.g. TWITTER, TWITTER_VIDEO, TIKTOK) returns False.
      - BUT `is_capture_allowed` returns True for ALL media providers and formats (both video and post)!
      - `DEFAULT_COMMANDS` (DEBUG, ALLOW_CHAT, SETTINGS, HELP, START) return True for `is_command_allowed`
        and cannot be disabled via `disable_command`.
    """
    session_maker = await test_container.get(async_sessionmaker)
    command_service = await test_container.get(CommandService)
    chat_status_service = await test_container.get(ChatStatusService)

    chat_id = 20001
    await verify_group_admins(test_container, chat_id)
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)
    # Represent the compatibility marker produced by the real legacy016 migration.
    async with session_maker.begin() as session:
        chat = (await session.execute(select(ChatDB).where(ChatDB.chat_id == chat_id))).scalar_one()
        chat.legacy_empty_capture = True

    # 1. Assert real SQLite state: chat exists, but has 0 associated commands
    persisted_status, persisted_commands = await get_persisted_chat_and_commands(session_maker, chat_id)
    assert persisted_status == ChatStatus.APPROVED
    assert persisted_commands == set()

    # 2. Allowed commands set is empty
    allowed = await command_service.get_allowed_commands(chat_id)
    assert allowed == set()

    # 3. Direct command queries return False for media providers and split commands
    assert await command_service.is_command_allowed(chat_id, CommandName.TWITTER) is False
    assert await command_service.is_command_allowed(chat_id, CommandName.TWITTER_VIDEO) is False
    assert await command_service.is_command_allowed(chat_id, CommandName.TWITTER_POST) is False
    assert await command_service.is_command_allowed(chat_id, CommandName.TIKTOK) is False
    assert await command_service.is_command_allowed(chat_id, CommandName.TIKTOK_VIDEO) is False

    # 4. OBSERVED QUIRK: Empty command set allows capture for ALL media providers and formats
    assert await command_service.is_capture_allowed(chat_id, CommandName.TWITTER, is_video=True) is True
    assert await command_service.is_capture_allowed(chat_id, CommandName.TWITTER, is_video=False) is True
    assert await command_service.is_capture_allowed(chat_id, CommandName.TIKTOK, is_video=True) is True
    assert await command_service.is_capture_allowed(chat_id, CommandName.TIKTOK, is_video=False) is True
    assert await command_service.is_capture_allowed(chat_id, CommandName.YOUTUBE, is_video=True) is True

    # 5. Default commands are always allowed and cannot be disabled
    for default_cmd in DEFAULT_COMMANDS:
        assert await command_service.is_command_allowed(chat_id, default_cmd) is True
        # Attempting to disable returns False and leaves DB untouched
        assert await command_service.disable_command(chat_id, default_cmd) is False

    _, persisted_commands_after = await get_persisted_chat_and_commands(session_maker, chat_id)
    assert persisted_commands_after == set()


async def test_characterize_legacy_partially_enabled_command_set(test_container: AsyncContainer) -> None:
    """Characterize parent provider checks and capture when command set is partially enabled.

    Legacy Behavior:
    - When at least one command is enabled (e.g. `twitter_post`):
      - SQLite contains only `twitter_post`.
      - `get_allowed_commands` returns `{"twitter_post", "twitter"}` (parent is synthesized).
      - `is_command_allowed(TWITTER)` is True because one of its split sub-commands is active.
      - `is_command_allowed(TWITTER_POST)` is True; `is_command_allowed(TWITTER_VIDEO)` is False.
      - Effective capture: `is_capture_allowed(TWITTER, is_video=False)` is True; `is_video=True` is False.
      - Side effect: Empty-set fallback no longer applies to ANY provider, so unconfigured providers
        (e.g. TIKTOK, YOUTUBE) now return False for `is_capture_allowed`!
    """
    session_maker = await test_container.get(async_sessionmaker)
    command_service = await test_container.get(CommandService)
    chat_status_service = await test_container.get(ChatStatusService)

    chat_id = 20002
    await verify_group_admins(test_container, chat_id)
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

    # Enable ONLY twitter_post
    await command_service.enable_command(chat_id, CommandName.TWITTER_POST)

    # 1. Assert real SQLite state: exactly 1 command record
    _, persisted_commands = await get_persisted_chat_and_commands(session_maker, chat_id)
    assert persisted_commands == {str(CommandName.TWITTER_POST)}

    # 2. Allowed commands includes parent provider
    allowed = await command_service.get_allowed_commands(chat_id)
    assert allowed == {str(CommandName.TWITTER_POST), str(CommandName.TWITTER)}

    # 3. Command checks
    assert await command_service.is_command_allowed(chat_id, CommandName.TWITTER) is True
    assert await command_service.is_command_allowed(chat_id, CommandName.TWITTER_POST) is True
    assert await command_service.is_command_allowed(chat_id, CommandName.TWITTER_VIDEO) is False

    # 4. Effective capture checks for Twitter
    assert await command_service.is_capture_allowed(chat_id, CommandName.TWITTER, is_video=False) is True
    assert await command_service.is_capture_allowed(chat_id, CommandName.TWITTER, is_video=True) is False

    # 5. Effective capture for other providers now DENIED because allowed commands is non-empty
    assert await command_service.is_capture_allowed(chat_id, CommandName.TIKTOK, is_video=True) is False
    assert await command_service.is_capture_allowed(chat_id, CommandName.TIKTOK, is_video=False) is False
    assert await command_service.is_capture_allowed(chat_id, CommandName.YOUTUBE, is_video=True) is False


async def test_characterize_legacy_parent_provider_bulk_and_toggle(test_container: AsyncContainer) -> None:
    """Characterize parent provider bulk operations and toggle behavior in real SQLite."""
    session_maker = await test_container.get(async_sessionmaker)
    command_service = await test_container.get(CommandService)
    chat_status_service = await test_container.get(ChatStatusService)

    chat_id = 20003
    await verify_group_admins(test_container, chat_id)
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

    # 1. Enabling parent provider enables both split sub-commands
    assert await command_service.enable_command(chat_id, CommandName.INSTAGRAM) is True
    _, persisted_commands = await get_persisted_chat_and_commands(session_maker, chat_id)
    assert persisted_commands == {str(CommandName.INSTAGRAM_VIDEO), str(CommandName.INSTAGRAM_POST)}

    assert await command_service.is_capture_allowed(chat_id, CommandName.INSTAGRAM, is_video=True) is True
    assert await command_service.is_capture_allowed(chat_id, CommandName.INSTAGRAM, is_video=False) is True

    # 2. Toggle when parent is active disables BOTH sub-commands
    new_state = await command_service.toggle(chat_id, CommandName.INSTAGRAM)
    assert new_state is False
    _, persisted_after_toggle = await get_persisted_chat_and_commands(session_maker, chat_id)
    assert persisted_after_toggle == set()

    # 3. Toggle when parent is inactive enables BOTH sub-commands
    new_state_on = await command_service.toggle(chat_id, CommandName.INSTAGRAM)
    assert new_state_on is True
    _, persisted_after_re_toggle = await get_persisted_chat_and_commands(session_maker, chat_id)
    assert persisted_after_re_toggle == {str(CommandName.INSTAGRAM_VIDEO), str(CommandName.INSTAGRAM_POST)}


async def test_characterize_legacy_default_activation(test_container: AsyncContainer) -> None:
    """Characterize default activation via chat_status_service.approve in real SQLite.

    When chat_status_service.approve is called:
    - Chat status is set to APPROVED in SQLite.
    - All 16 split media sub-commands (8 providers x 2) are inserted into `chat_commands` in SQLite.
    - Regular users are granted access to all media commands.
    """
    session_maker = await test_container.get(async_sessionmaker)
    chat_status_service = await test_container.get(ChatStatusService)
    command_service = await test_container.get(CommandService)
    access_service = await test_container.get(AccessService)

    chat_id = 30001
    await verify_group_admins(test_container, chat_id)
    await chat_status_service.approve(chat_id)

    # 1. Assert SQLite state directly
    persisted_status, persisted_commands = await get_persisted_chat_and_commands(session_maker, chat_id)
    assert persisted_status == ChatStatus.APPROVED

    expected_commands = {str(sub_cmd) for sub_pair in PROVIDER_SPLIT_COMMANDS.values() for sub_cmd in sub_pair}
    assert len(expected_commands) == 16  # 8 providers * 2
    assert persisted_commands == expected_commands

    # 2. Verify all parent providers and sub-commands are allowed
    allowed = await command_service.get_allowed_commands(chat_id)
    for provider in PROVIDER_SPLIT_COMMANDS:
        assert str(provider) in allowed
        assert await command_service.is_command_allowed(chat_id, provider) is True
        assert await command_service.is_capture_allowed(chat_id, provider, is_video=True) is True
        assert await command_service.is_capture_allowed(chat_id, provider, is_video=False) is True

    # 3. Regular user is allowed by AccessService for approved commands
    regular_user_id = 9999
    policy = CommandPolicy(requires_approval=True, is_toggleable=True)
    assert (
        await access_service.is_allowed(regular_user_id, chat_id, policy, command_name=str(CommandName.YOUTUBE)) is True
    )


@pytest.mark.parametrize("status", [ChatStatus.APPROVED, ChatStatus.BANNED, ChatStatus.REJECTED])
async def test_removal_and_rejoin_preserve_access_status(test_container: AsyncContainer, status: ChatStatus) -> None:
    """Physical bot removal/rejoin preserves durable status and command selections (019)."""
    session_maker = await test_container.get(async_sessionmaker)
    statuses = await test_container.get(ChatStatusService)
    handler = await test_container.get(BotJoinHandler)
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()
    bot.leave_chat = AsyncMock()
    chat_id = 30002
    await statuses.approve(chat_id)
    await statuses.set_status(chat_id, status)
    _, initial_commands = await get_persisted_chat_and_commands(session_maker, chat_id)
    for event_status in ("kicked", "member"):
        await handler.handle_my_chat_member_update(create_mock_event(chat_id, event_status), bot)
        persisted_status, commands = await get_persisted_chat_and_commands(session_maker, chat_id)
        assert persisted_status == status
        assert commands == initial_commands
    bot.send_message.assert_not_called()
    bot.leave_chat.assert_not_called()
