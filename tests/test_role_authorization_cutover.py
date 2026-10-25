"""Database-authority cutover and live owner routing with real application services."""

import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import cast

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import Chat, Message
from dishka import AsyncContainer

from hovorunv2.application.ports import UserStore
from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.owner_notifier import OwnerNotifier
from hovorunv2.application.services.role_service import RoleService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ChatStatus, CommandName, Role
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.logger import setup_telegram_logging
from tests.conftest import provision_role


class RecordingSession(BaseSession):
    """Capture outgoing Telegram requests at the transport boundary, with no network."""

    def __init__(self) -> None:
        super().__init__()
        self.recipients: list[int] = []

    async def close(self) -> None:
        """No socket resources are owned by this recording transport."""

    async def make_request(self, bot: Bot, method: TelegramMethod[TelegramType], **kwargs: object) -> TelegramType:
        """Return a valid Telegram message for each captured SendMessage request."""
        if not isinstance(method, SendMessage) or not isinstance(method.chat_id, int):
            msg = "Only numeric SendMessage requests are supported by this test transport."
            raise TypeError(msg)
        self.recipients.append(method.chat_id)
        return cast(
            TelegramType,
            Message(
                message_id=1, date=datetime.now(UTC), chat=Chat(id=method.chat_id, type="private"), text=method.text
            ),
        )

    async def stream_content(self, url: str, **kwargs: object) -> AsyncGenerator[bytes]:
        """Reject unexpected downloads rather than falling through to real network access."""
        msg = "Downloads are unsupported by this test transport."
        raise AssertionError(msg)
        yield b""


@pytest.mark.asyncio
async def test_old_environment_ids_never_grant_authority(
    test_container: AsyncContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deprecated settings are ignored and never imported into durable roles."""
    monkeypatch.setenv("ADMIN_IDS", "[101]")
    monkeypatch.setenv("OWNERS", "[101]")
    monkeypatch.setenv("BOT_TOKEN", "123456:synthetic-token")
    assert Settings(_env_file=None).db_path
    assert "admin_ids" not in Settings.model_fields
    assert "owners" not in Settings.model_fields
    access = await test_container.get(AccessService)
    roles = await test_container.get(RoleService)
    policy = CommandPolicy(requires_admin=True, requires_approval=False, is_toggleable=False)
    assert await access.is_admin(101) is False
    assert await access.is_owner(101) is False
    assert await access.is_allowed(101, -100101, policy) is False
    assert await roles.list_roles() == []
    monkeypatch.setenv("ADMIN_IDS", "not-json")
    assert Settings(_env_file=None).db_path


@pytest.mark.asyncio
async def test_live_role_gates_follow_promotion_demotion_and_deletion(test_container: AsyncContainer) -> None:
    """Every live authority query uses current database state through the same policy."""
    access = await test_container.get(AccessService)
    roles = await test_container.get(RoleService)
    users = await test_container.get(UserStore)
    await provision_role(test_container, 101, Role.ADMIN)
    assert await access.is_admin(101) is True
    assert await access.is_owner(101) is False
    await roles.set_role(101, Role.OWNER, actor=ActorContext.cli())
    assert await access.is_admin(101) is True
    assert await access.is_owner(101) is True
    await roles.set_role(101, Role.USER, actor=ActorContext.cli())
    assert await access.is_admin(101) is False
    assert await access.is_owner(101) is False
    await roles.set_role(101, Role.OWNER, actor=ActorContext.cli())
    await users.soft_delete_user(101, actor=ActorContext.cli(), reason="retired")
    assert await access.is_owner(101) is False


@pytest.mark.asyncio
async def test_empty_role_registry_preserves_existing_group_access(test_container: AsyncContainer) -> None:
    """Resolving access with no operators never erases approvals or enabled commands."""
    statuses = await test_container.get(ChatStatusService)
    commands = await test_container.get(CommandService)
    await statuses.set_status(-100101, ChatStatus.APPROVED)
    await commands.enable_command(-100101, CommandName.TIKTOK)
    before = await commands.get_allowed_commands(-100101)
    access = await test_container.get(AccessService)
    assert await access.is_admin(101) is False
    assert await access.is_allowed(101, -100101, CommandPolicy(is_toggleable=False)) is True
    assert await statuses.get_status(-100101) == ChatStatus.APPROVED
    assert await commands.get_allowed_commands(-100101) == before


@pytest.mark.asyncio
async def test_owner_notifications_use_current_active_recipients(test_container: AsyncContainer) -> None:
    """Recipient lookup reflects additions, demotion and deletion without restarting the notifier."""
    roles = await test_container.get(RoleService)
    users = await test_container.get(UserStore)
    notifier = await test_container.get(OwnerNotifier)
    session = RecordingSession()
    async with Bot("123456:synthetic-token", session=session) as bot:
        assert await notifier.broadcast(bot, "test notice") == 0
        await provision_role(test_container, 101, Role.OWNER)
        await provision_role(test_container, 102, Role.OWNER)
        await provision_role(test_container, 103, Role.ADMIN)
        assert await notifier.broadcast(bot, "test notice") == 2
        assert session.recipients == [101, 102]
        session.recipients.clear()
        await roles.set_role(101, Role.ADMIN, actor=ActorContext.cli())
        await users.soft_delete_user(102, actor=ActorContext.cli(), reason="retired")
        assert await notifier.broadcast(bot, "test notice") == 0
        assert session.recipients == []


@pytest.mark.asyncio
async def test_error_routing_tracks_owners_from_empty_registry(test_container: AsyncContainer) -> None:
    """One attached log handler follows current owner authority and drains before shutdown."""
    roles = await test_container.get(RoleService)
    session = RecordingSession()
    async with Bot("123456:synthetic-token", session=session) as bot:
        handler = setup_telegram_logging(bot, roles)
        record = logging.LogRecord("test.cutover", logging.ERROR, __file__, 1, "test operational alert", (), None)
        try:
            handler.handle(record)
            await handler.drain()
            assert session.recipients == []
            await provision_role(test_container, 101, Role.OWNER)
            handler.handle(record)
            await handler.drain()
            assert session.recipients == [101]
            session.recipients.clear()
            await roles.set_role(101, Role.ADMIN, actor=ActorContext.cli())
            handler.handle(record)
            await handler.drain()
            assert session.recipients == []
        finally:
            await handler.shutdown()
        assert handler not in logging.getLogger().handlers
