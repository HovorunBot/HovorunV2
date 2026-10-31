"""Restrictions preserve physical presence and use minimal, bounded group explanations."""

import secrets
from datetime import UTC, datetime

import pytest
from aioresponses import aioresponses
from dishka import AsyncContainer

from hovorunv2.application.access.access_service import AccessService, CommandPolicy
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.messaging.owner_notifier import OwnerNotifier
from hovorunv2.application.operations.reminder_service import ChatReminderService
from hovorunv2.domain.enums import ChatStatus
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.cache import CacheService
from hovorunv2.interface.telegram.handlers.bot_join import BotJoinHandler
from hovorunv2.interface.telegram.handlers.membership import MembershipEventHandler
from hovorunv2.interface.telegram.middlewares import AccessMiddleware
from hovorunv2.interface.telegram.settings.prompts import TelegramUnauthorizedPromptSender
from tests.conftest import provision_role
from tests.utils.activation import ACTOR, activation_runtime, group_message, sent_fields, stub_telegram
from tests.utils.telegram import bot_event


async def forbidden_handler(_event: object, _data: object) -> None:
    """Fail if a denied feature reaches its handler."""
    pytest.fail("Banned group reached feature handler")


@pytest.mark.parametrize("status", [ChatStatus.REJECTED, ChatStatus.BANNED])
async def test_restrictions_survive_rejoin_and_maintenance(test_container: AsyncContainer, status: ChatStatus) -> None:
    """Real join and maintenance paths preserve restrictions and never call leaveChat."""
    group = -(secrets.randbelow(2**40) + 1)
    settings = await test_container.get(Settings)
    cache = CacheService(settings.valkey_url)
    chats = await test_container.get(ChatStore)
    statuses = await test_container.get(ChatStatusService)
    await statuses.set_status(group, status)
    reminder = ChatReminderService(
        chats,
        cache,
        await test_container.get(OwnerNotifier),
        TelegramUnauthorizedPromptSender(chats),
    )
    join = BotJoinHandler(statuses, reminder, await test_container.get(MembershipEventHandler))
    try:
        async with activation_runtime(test_container) as runtime:
            with aioresponses() as http:
                stub_telegram(http)
                await join.handle_my_chat_member_update(
                    bot_event(group, present=False, date=datetime.now(UTC)), runtime.bot
                )
                await join.handle_my_chat_member_update(
                    bot_event(group, present=True, date=datetime.now(UTC)), runtime.bot
                )
                await reminder.run_maintenance_cycle(runtime.bot)
                assert await statuses.get_status(group) == status
                current = await chats.get_chat(group)
                assert current is not None
                assert current.is_bot_present
                assert not sent_fields(http, "leaveChat")
    finally:
        await cache.close()


async def test_group_ban_denies_admin_with_cooldown_safe_explanation(test_container: AsyncContainer) -> None:
    """Bot authority cannot bypass a group ban; public denial contains no private reasons."""
    group = -(secrets.randbelow(2**40) + 1)
    await provision_role(test_container, ACTOR)
    await (await test_container.get(ChatStatusService)).set_status(group, ChatStatus.BANNED)
    settings = await test_container.get(Settings)
    cache = CacheService(settings.valkey_url)
    middleware = AccessMiddleware(await test_container.get(AccessService), cache, settings)
    key = f"access:denial:{group}:group_banned"
    try:
        async with activation_runtime(test_container) as runtime:
            with aioresponses() as http:
                stub_telegram(http)
                message = group_message(chat_id=group).as_(runtime.bot)
                data = {"policy": CommandPolicy(is_toggleable=False), "bot": runtime.bot}
                await middleware(forbidden_handler, message, data)
                other = group_message(chat_id=group, user_id=ACTOR + 1).as_(runtime.bot)
                await middleware(forbidden_handler, other, data)
                messages = sent_fields(http, "sendMessage")
                assert len(messages) == 1
                assert messages[0]["text"] == (
                    "🚫 Bot processing is unavailable in this group. Contact a bot administrator privately."
                )
                assert await cache.get(key) == 1
                await cache.delete(key)
                await middleware(forbidden_handler, message, data)
                assert len(sent_fields(http, "sendMessage")) == 2
    finally:
        await cache.delete(key)
        await cache.close()
