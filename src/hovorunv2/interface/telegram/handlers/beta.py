"""Private beta introduction announcements and Activate/Decline callbacks."""

import contextlib
from typing import Any, ClassVar

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from hovorunv2.application.access.access_service import CommandPolicy
from hovorunv2.application.access.administration_service import AdministrationService
from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.beta_prompts import BetaDecision, BetaPromptService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ChatStatus, CommandName
from hovorunv2.interface.telegram.settings.callbacks import BetaAction, BetaCallback

from .base import BaseCommand

BETA_UNAVAILABLE = "Beta activation unavailable."
BETA_FEATURES_HINT = "Use /settings to choose features."


class BetaPromptCommand(BaseCommand):
    """Announce new beta features and record only authorized Activate/Decline choices."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(requires_admin=True, requires_approval=False, is_toggleable=False)

    def __init__(
        self,
        administration: AdministrationService,
        prompts: BetaPromptService,
        users: UserStore,
        chats: ChatStore,
    ) -> None:
        self._administration = administration
        self._prompts = prompts
        self._users = users
        self._chats = chats

    @property
    def name(self) -> str:
        """Stable command identity."""
        return CommandName.BETA

    @property
    def trigger(self) -> Command:
        """Private bot-administrator announcement trigger."""
        return Command(CommandName.BETA)

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:
        """Queue announcements for newly introduced beta features on operator request."""
        if message.chat.type != ChatType.PRIVATE or message.from_user is None:
            return
        if message.from_user.is_bot or message.sender_chat is not None:
            return
        if message.chat.id != message.from_user.id:
            return
        actor = ActorContext.telegram(message.from_user.id)
        try:
            await self._administration.authorize(actor, message.chat.type)
        except PermissionError:
            await message.answer(BETA_UNAVAILABLE)
            return
        queued = await self._prompts.announce_new()
        await message.answer(f"Queued {queued} beta introduction recipient(s).")

    async def handle_callback(self, query: CallbackQuery, callback_data: BetaCallback) -> None:
        """Apply the announced subset decision with current authority revalidation."""
        message = query.message
        if not isinstance(message, Message):
            return
        if callback_data.action == BetaAction.FEATURES:
            await query.answer(BETA_FEATURES_HINT, show_alert=True)
            return
        target = await self._target(message, query.from_user.id)
        if target is None:
            await query.answer(BETA_UNAVAILABLE, show_alert=True)
            return
        decision = BetaDecision.ACTIVE if callback_data.action == BetaAction.ACTIVATE else BetaDecision.DECLINED
        try:
            await self._prompts.decide(
                target,
                callback_data.feature,
                decision,
                actor=ActorContext.telegram(query.from_user.id),
                transport_kind=message.chat.type,
            )
        except PermissionError, ValueError:
            await query.answer(BETA_UNAVAILABLE, show_alert=True)
            return
        await query.answer("Activated" if decision == BetaDecision.ACTIVE else "Declined")
        with contextlib.suppress(Exception):
            await message.edit_reply_markup(reply_markup=None)

    async def _target(self, message: Message, actor_id: int) -> PermissionTarget | None:
        """Resolve the announced message's own subject; never another recipient."""
        if message.chat.type == ChatType.PRIVATE:
            if message.chat.id != actor_id:
                return None
            user = await self._users.get_by_telegram_id(actor_id)
            if user is None:
                return None
            return PermissionTarget("user", user.id, user.generation, user.telegram_id)
        chat = await self._chats.get_chat(message.chat.id)
        if chat is None or chat.status != ChatStatus.APPROVED:
            return None
        return PermissionTarget("group", chat.id, chat.generation, chat.chat_id)

    def register_callbacks(self, router: Router, flags: dict[str, Any]) -> None:
        """Register the same private/group authority checks for announcement buttons."""
        router.callback_query.register(self.handle_callback, BetaCallback.filter(), flags=flags)
