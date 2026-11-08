"""Exercise first-action verification through production routing and real persistence."""

from collections.abc import Mapping
from typing import ClassVar

import pytest
from aiogram import Bot, Dispatcher, F, Router
from aiogram.methods import GetChatAdministrators, GetChatMember
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from aiogram.utils.magic_filter import MagicFilter
from dishka import AsyncContainer

from hovorunv2.application.access.access_service import AccessService, CommandPolicy
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.membership_reconciliation import MembershipReconciliationService
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.messaging.message_service import MessageService
from hovorunv2.application.messaging.ports import CachePort
from hovorunv2.application.operations.reminder_service import ChatReminderService
from hovorunv2.domain.enums import CommandName
from hovorunv2.infrastructure.config import Settings
from hovorunv2.interface.telegram.bot import build_router
from hovorunv2.interface.telegram.handlers.base import BaseCommand
from hovorunv2.interface.telegram.transport.identity import IdentityMiddleware
from hovorunv2.interface.telegram.transport.membership import TelegramMembershipAdapter
from tests.utils.telegram import GROUP_ID, USER_ID, member_payload


class RecordedProtectedCommand(BaseCommand):
    """Record entry into the handler after production access checks succeed."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(is_automatic=True)

    def __init__(self) -> None:
        self.chats: list[int] = []

    @property
    def name(self) -> str:
        """Return the real provider whose split feature policy is evaluated."""
        return CommandName.INSTAGRAM

    @property
    def trigger(self) -> MagicFilter:
        """Match only the synthetic media action."""
        return F.text.startswith("https://www.instagram.com/")

    async def handle(self, message: Message, bot: Bot, **kwargs: object) -> None:
        """Record authorized message handling without starting extraction."""
        self.chats.append(message.chat.id)

    async def handle_callback(self, query: CallbackQuery) -> None:
        """Record authorized callback handling without sending a reply."""
        assert isinstance(query.message, Message)
        self.chats.append(query.message.chat.id)

    def register_callbacks(self, router: Router, flags: Mapping[str, object]) -> None:
        """Register a protected callback under the same production middleware."""
        router.callback_query.register(self.handle_callback, F.data == "membership:retry", flags=flags)


@pytest.mark.parametrize("callback", [False, True])
async def test_first_protected_action_recovers_and_reuses_membership(
    test_container: AsyncContainer, recording_bot: Bot, callback: bool
) -> None:
    """Verify missing snapshots before the triggering action, with no API calls for unmatched or fresh actions."""
    chats = await test_container.get(ChatStore)
    statuses = await test_container.get(ChatStatusService)
    await chats.get_or_create_chat(GROUP_ID)
    await statuses.approve(GROUP_ID)
    cache = await test_container.get(CachePort)
    reconciliation = MembershipReconciliationService(
        await test_container.get(MembershipStore), TelegramMembershipAdapter(recording_bot), chats, cache
    )
    command = RecordedProtectedCommand()
    dispatcher = Dispatcher()
    dispatcher.include_router(
        build_router(
            [command],
            await test_container.get(MessageService),
            await test_container.get(AccessService),
            await test_container.get(Settings),
            await test_container.get(IdentityMiddleware),
            cache,
            await test_container.get(ChatReminderService),
            reconciliation,
        )
    )
    message = Message(
        message_id=1,
        date=1,
        chat=Chat(id=GROUP_ID, type="supergroup"),
        from_user=User(id=USER_ID, is_bot=False, first_name="Synthetic"),
        text="Unmatched synthetic message",
    )
    await dispatcher.feed_update(recording_bot, Update(update_id=1, message=message))
    assert not recording_bot.session.calls
    assert not command.chats
    recording_bot.session.responses.extend(
        [member_payload(is_bot=True), [member_payload("creator")], member_payload("creator")]
    )
    protected = message.model_copy(update={"text": "https://www.instagram.com/p/Synthetic_1/"})
    query = CallbackQuery(
        id="synthetic",
        from_user=message.from_user,
        chat_instance="synthetic",
        message=protected,
        data="membership:retry",
    )
    for update_id in (2, 3):
        update = (
            Update(update_id=update_id, callback_query=query)
            if callback
            else Update(update_id=update_id, message=protected)
        )
        await dispatcher.feed_update(recording_bot, update)
    assert command.chats == [GROUP_ID, GROUP_ID]
    assert [type(call) for call in recording_bot.session.calls] == [GetChatMember, GetChatAdministrators, GetChatMember]
    assert (await statuses.evaluate_access(GROUP_ID)).allowed
