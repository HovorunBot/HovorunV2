"""Real SQLite action authorization, middleware guards, and private vs group policy rules (022)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.types import Chat, Message, User
from dishka import AsyncContainer

from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.application.membership import MembershipEvidence, MembershipStatus
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.ban_service import BanService
from hovorunv2.application.services.role_service import RoleService
from hovorunv2.application.services.trust_service import TrustService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.enums import Role, TargetKind
from hovorunv2.infrastructure.config import Settings
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from hovorunv2.interface.telegram.middlewares import AccessMiddleware
from tests.conftest import verify_group_admins


def make_message(chat_id: int, user_id: int, chat_type: ChatType) -> MagicMock:
    """Create a mock Telegram message for middleware testing."""
    message = MagicMock(spec=Message)
    message.from_user = MagicMock(spec=User)
    message.from_user.id = user_id
    message.from_user.is_bot = False
    message.sender_chat = None
    message.chat = MagicMock(spec=Chat)
    message.chat.id = chat_id
    message.chat.type = chat_type
    message.answer = AsyncMock()
    message.__class__ = Message
    return message


@pytest.mark.asyncio
async def test_missing_policy_denies_by_default(test_container: AsyncContainer) -> None:
    """An event missing declarative command policy is denied by default."""
    access_service = await test_container.get(AccessService)
    settings = await test_container.get(Settings)
    middleware = AccessMiddleware(access_service, settings)

    message = make_message(100, 100, ChatType.PRIVATE)
    handler = AsyncMock()
    data = {"handler": MagicMock(), "bot": MagicMock(spec=Bot), "policy": None}

    result = await middleware(handler, message, data)
    assert result is None
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_personally_banned_user_direct_command_vs_automatic_media(
    test_container: AsyncContainer,
) -> None:
    """Personal ban blocks explicit commands but allows automatic media in approved groups."""
    chats = await test_container.get(ChatStore)
    users = await test_container.get(UserStore)
    grants = await test_container.get(GroupGrantStore)
    bans = await test_container.get(BanService)
    access_service = await test_container.get(AccessService)
    settings = await test_container.get(Settings)
    middleware = AccessMiddleware(access_service, settings)
    actor = ActorContext.cli(reason="operational audit")

    chat = await chats.get_or_create_chat(-100301)
    await verify_group_admins(test_container, chat.chat_id)
    await grants.approve(chat.id, actor)

    banned_user = await users.ingest_user(401)
    assert banned_user is not None
    await bans.apply(TargetKind.USER, banned_user.telegram_id, BanKind.PERSONAL, actor)

    message = make_message(chat.chat_id, banned_user.telegram_id, ChatType.SUPERGROUP)

    # 1. Direct explicit command from personally banned user: BLOCKED
    direct_policy = CommandPolicy(requires_approval=True, is_toggleable=False, is_automatic=False)
    handler = AsyncMock()
    data = {"handler": MagicMock(), "bot": MagicMock(spec=Bot), "policy": direct_policy}
    result = await middleware(handler, message, data)
    assert result is None
    handler.assert_not_called()

    # 2. Automatic media link posted by personally banned user: ALLOWED for group
    auto_policy = RichMediaCommand.POLICY  # is_automatic = True
    handler = AsyncMock(return_value="processed")
    data = {"handler": MagicMock(), "bot": MagicMock(spec=Bot), "policy": auto_policy}
    result = await middleware(handler, message, data)
    assert result == "processed"
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_private_chat_trusted_user_allowed_feature_use(
    test_container: AsyncContainer,
) -> None:
    """Trusted users can execute media features in private chats; untrusted users are blocked."""
    users = await test_container.get(UserStore)
    trust = await test_container.get(TrustService)
    access_service = await test_container.get(AccessService)
    settings = await test_container.get(Settings)
    middleware = AccessMiddleware(access_service, settings)
    actor = ActorContext.cli(reason="operational audit")

    trusted_user = await users.ingest_user(402)
    untrusted_user = await users.ingest_user(403)
    assert trusted_user is not None
    assert untrusted_user is not None
    await trust.set_trust(str(trusted_user.telegram_id), is_trusted=True, actor=actor)

    feature_policy = RichMediaCommand.POLICY

    # 1. Trusted user in private chat: ALLOWED
    trusted_msg = make_message(trusted_user.telegram_id, trusted_user.telegram_id, ChatType.PRIVATE)
    handler = AsyncMock(return_value="processed")
    data = {"handler": MagicMock(), "bot": MagicMock(spec=Bot), "policy": feature_policy}
    result = await middleware(handler, trusted_msg, data)
    assert result == "processed"
    handler.assert_called_once()

    # 2. Untrusted user in private chat: BLOCKED and received landing help text
    untrusted_msg = make_message(untrusted_user.telegram_id, untrusted_user.telegram_id, ChatType.PRIVATE)
    handler = AsyncMock()
    data = {"handler": MagicMock(), "bot": MagicMock(spec=Bot), "policy": feature_policy}
    result = await middleware(handler, untrusted_msg, data)
    assert result is None
    handler.assert_not_called()
    untrusted_msg.answer.assert_called_once()


@pytest.mark.asyncio
async def test_private_chat_banned_user_blocked(test_container: AsyncContainer) -> None:
    """Even if previously trusted, banned users are denied in private chats."""
    users = await test_container.get(UserStore)
    trust = await test_container.get(TrustService)
    bans = await test_container.get(BanService)
    access_service = await test_container.get(AccessService)
    settings = await test_container.get(Settings)
    middleware = AccessMiddleware(access_service, settings)
    actor = ActorContext.cli(reason="operational audit")

    user = await users.ingest_user(404)
    assert user is not None
    await trust.set_trust(str(user.telegram_id), is_trusted=True, actor=actor)
    await bans.apply(TargetKind.USER, user.telegram_id, BanKind.PERSONAL, actor)

    message = make_message(user.telegram_id, user.telegram_id, ChatType.PRIVATE)
    handler = AsyncMock()
    data = {"handler": MagicMock(), "bot": MagicMock(spec=Bot), "policy": RichMediaCommand.POLICY}

    result = await middleware(handler, message, data)
    assert result is None
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_private_chat_untrusted_group_admin_management_vs_feature(
    test_container: AsyncContainer,
) -> None:
    """Untrusted group admin can access private group management but not private media features."""
    chats = await test_container.get(ChatStore)
    users = await test_container.get(UserStore)
    memberships = await test_container.get(MembershipStore)
    access_service = await test_container.get(AccessService)
    settings = await test_container.get(Settings)
    middleware = AccessMiddleware(access_service, settings)
    actor = ActorContext.cli(reason="operational audit")

    chat = await chats.get_or_create_chat(-100302)
    admin_user = await users.ingest_user(405)
    assert admin_user is not None

    now = datetime.now(UTC)
    evidence = MembershipEvidence(
        chat.chat_id,
        admin_user.telegram_id,
        MembershipStatus.ADMIN,
        now - timedelta(minutes=1),
        now + timedelta(minutes=4),
    )
    await memberships.save_member(chat.id, evidence, actor)

    message = make_message(admin_user.telegram_id, admin_user.telegram_id, ChatType.PRIVATE)

    # 1. Private group management (requires_group_admin=True): ALLOWED
    management_policy = CommandPolicy(requires_group_admin=True, requires_approval=False)
    handler = AsyncMock(return_value="managed")
    data = {"handler": MagicMock(), "bot": MagicMock(spec=Bot), "policy": management_policy}
    result = await middleware(handler, message, data)
    assert result == "managed"
    handler.assert_called_once()

    # 2. Private media feature (requires_approval=True, is_automatic=True): BLOCKED
    feature_policy = RichMediaCommand.POLICY
    handler = AsyncMock()
    data = {"handler": MagicMock(), "bot": MagicMock(spec=Bot), "policy": feature_policy}
    result = await middleware(handler, message, data)
    assert result is None
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_bot_admin_commands_private_only(test_container: AsyncContainer) -> None:
    """Bot administrative operations are strictly private-only and rejected in groups."""
    chats = await test_container.get(ChatStore)
    roles = await test_container.get(RoleService)
    access_service = await test_container.get(AccessService)
    settings = await test_container.get(Settings)
    middleware = AccessMiddleware(access_service, settings)
    actor = ActorContext.cli(reason="operational audit")

    admin_id = 406
    await roles.set_role(admin_id, Role.ADMIN, actor)
    chat = await chats.get_or_create_chat(-100303)

    admin_policy = CommandPolicy(requires_admin=True, is_toggleable=False)

    # 1. In private chat: ALLOWED
    private_msg = make_message(admin_id, admin_id, ChatType.PRIVATE)
    handler = AsyncMock(return_value="admin_ok")
    data = {"handler": MagicMock(), "bot": MagicMock(spec=Bot), "policy": admin_policy}
    result = await middleware(handler, private_msg, data)
    assert result == "admin_ok"
    handler.assert_called_once()

    # 2. In group chat: REJECTED (bot administration remains private-only)
    group_msg = make_message(chat.chat_id, admin_id, ChatType.SUPERGROUP)
    handler = AsyncMock()
    data = {"handler": MagicMock(), "bot": MagicMock(spec=Bot), "policy": admin_policy}
    result = await middleware(handler, group_msg, data)
    assert result is None
    handler.assert_not_called()
