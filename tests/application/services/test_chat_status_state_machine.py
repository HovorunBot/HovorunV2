"""Access lifecycle through real stores, local Valkey and intercepted Telegram HTTP."""

import json
import secrets
from datetime import UTC, datetime, timedelta

from aiogram.types import CallbackQuery, Chat, ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, Message, User
from aioresponses import aioresponses
from dishka import AsyncContainer

from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.messaging.owner_notifier import OwnerNotifier
from hovorunv2.application.operations.reminder_service import ChatReminderService
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.domain.enums import ChatStatus
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.cache import CacheService
from hovorunv2.interface.telegram.handlers.bot_join import BotJoinHandler
from hovorunv2.interface.telegram.handlers.membership import MembershipEventHandler
from hovorunv2.interface.telegram.settings.callbacks import AccessAction, AccessCallback
from hovorunv2.interface.telegram.settings.prompts import TelegramUnauthorizedPromptSender
from tests.conftest import provision_role
from tests.utils.activation import ACTOR, ADMIN, BOT_ID, activation_runtime, group_message, sent_fields, stub_telegram


def bot_event(chat_id: int, *, present: bool, date: datetime) -> ChatMemberUpdated:
    """Create authentic event shapes for the synthetic bot without group-directory data."""
    user = User(id=BOT_ID, is_bot=True, first_name="Bot")
    return ChatMemberUpdated(
        chat=Chat(id=chat_id, type="supergroup", title="Synthetic group"),
        from_user=User(id=ACTOR, is_bot=False, first_name="Synthetic"),
        date=date,
        old_chat_member=ChatMemberLeft(user=user),
        new_chat_member=ChatMemberMember(user=user) if present else ChatMemberLeft(user=user),
    )


async def test_state_machine_flow(test_container: AsyncContainer) -> None:
    """Joining, requests, departure, private approval and restrictions preserve durable state."""
    chat_id = -(secrets.randbelow(2**40) + 1)
    cooldown_key = f"periodic:unauthorized_prompt:{chat_id}"
    settings = await test_container.get(Settings)
    cache = CacheService(settings.valkey_url)
    chats = await test_container.get(ChatStore)
    users = await test_container.get(UserStore)
    await users.ingest_user(ACTOR)
    await provision_role(test_container, ADMIN)
    statuses = await test_container.get(ChatStatusService)
    reminder = ChatReminderService(
        chats,
        cache,
        await test_container.get(OwnerNotifier),
        TelegramUnauthorizedPromptSender(chats),
    )
    join = BotJoinHandler(statuses, reminder, await test_container.get(MembershipEventHandler))
    now = datetime.now(UTC)
    try:
        async with activation_runtime(test_container) as runtime:
            with aioresponses() as http:
                stub_telegram(http)
                await join.handle_my_chat_member_update(
                    bot_event(chat_id, present=True, date=now - timedelta(minutes=2)), runtime.bot
                )
                assert await statuses.get_status(chat_id) == ChatStatus.UNAUTHORIZED
                assert len(sent_fields(http, "sendMessage")) == 1
                assert await cache.get(cooldown_key) == 1
                message = group_message(chat_id=chat_id).as_(runtime.bot)
                await runtime.command.handle(message, runtime.bot)
                assert await statuses.get_status(chat_id) == ChatStatus.PENDING
                count = len(sent_fields(http, "sendMessage"))
                await runtime.command.handle(message, runtime.bot)
                assert len(sent_fields(http, "sendMessage")) == count + 1
                assert sent_fields(http, "sendMessage")[-1]["text"] == "⏳ Approval is already pending."
                await join.handle_my_chat_member_update(
                    bot_event(chat_id, present=False, date=now - timedelta(minutes=1)), runtime.bot
                )
                assert await statuses.get_status(chat_id) == ChatStatus.PENDING
                count = len(sent_fields(http, "sendMessage"))
                await join.handle_my_chat_member_update(
                    bot_event(chat_id, present=True, date=now - timedelta(seconds=30)), runtime.bot
                )
                assert len(sent_fields(http, "sendMessage")) == count
                notification = next(
                    item
                    for item in sent_fields(http, "sendMessage")
                    if "reply_markup" in item and "Access Request" in item["text"]
                )
                data = AccessCallback.unpack(
                    json.loads(notification["reply_markup"])["inline_keyboard"][0][0]["callback_data"]
                )
                assert data.action == AccessAction.APPROVE
                query = CallbackQuery(
                    id="decision",
                    from_user=User(id=ADMIN, is_bot=False, first_name="Admin"),
                    chat_instance="test",
                    message=Message(message_id=2, date=now, chat=Chat(id=ADMIN, type="private"), text="Request"),
                    data=data.pack(),
                ).as_(runtime.bot)
                await runtime.command.handle_callback(query, data, runtime.bot)
                assert await statuses.get_status(chat_id) == ChatStatus.APPROVED
                count = len(sent_fields(http, "sendMessage"))
                await join.handle_my_chat_member_update(bot_event(chat_id, present=True, date=now), runtime.bot)
                assert len(sent_fields(http, "sendMessage")) == count
                await statuses.set_status(chat_id, ChatStatus.BANNED)
                await join.handle_my_chat_member_update(bot_event(chat_id, present=True, date=now), runtime.bot)
                assert not sent_fields(http, "leaveChat")
    finally:
        await cache.delete(cooldown_key)
        await cache.close()
