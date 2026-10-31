"""Private access administration with target previews and single-use confirmation."""

import html
import json
import shlex
from typing import Any, ClassVar

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from hovorunv2.application.access.access_service import CommandPolicy
from hovorunv2.application.access.administration import AdminAction
from hovorunv2.application.access.administration_service import AdministrationService
from hovorunv2.application.settings.admin_session_service import AdminSessionService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.access_arguments import parse_access
from hovorunv2.interface.telegram.handlers.base import BaseCommand
from hovorunv2.interface.telegram.settings.callbacks import AdminCallback

ADMIN_HELP = (
    "Use /admin ACTION user|group ID|@known_username [options].\n"
    "Actions: inspect, trust, untrust, ban, unban, requests, approve, accept, reject, beta-grant, beta-revoke.\n"
    'Options: --reason "internal reason", --public "safe explanation", --ban-kind personal|full, '
    "--feature KEY|*, --request-id ID.\n"
    "Internal reasons stay private. Only --public is suitable for disclosure. Role changes require CLI."
)
ADMIN_UNAVAILABLE = "Administration unavailable. Repeat /admin privately."


class AdministrationCommand(BaseCommand):
    """Require current bot authority at entry and immediately before each mutation."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(requires_admin=True, requires_approval=False, is_toggleable=False)

    def __init__(self, administration: AdministrationService, sessions: AdminSessionService) -> None:
        self._administration = administration
        self._sessions = sessions

    @property
    def name(self) -> str:
        """Stable command identity."""
        return CommandName.ADMIN

    @property
    def trigger(self) -> Command:
        """Private bot administration entry."""
        return Command(CommandName.ADMIN)

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:
        """Preview exact active target; never mutate on initial command entry."""
        if message.chat.type != ChatType.PRIVATE or message.from_user is None:
            return
        if message.from_user.is_bot or message.sender_chat is not None:
            return
        if message.chat.id != message.from_user.id:
            return
        actor = ActorContext.telegram(message.from_user.id)
        try:
            await self._administration.authorize(actor, message.chat.type)
            words = shlex.split(message.text or "")[1:]
            if not words:
                await message.answer(ADMIN_HELP)
                return
            request = parse_access(words)
            target = await self._administration.resolve(request.kind, request.target)
            state = await self._administration.inspect(target, actor, message.chat.type)
            if request.action in {AdminAction.INSPECT, AdminAction.REQUESTS}:
                await message.answer(html.escape(json.dumps(state, ensure_ascii=False)))
                return
            preview = (
                f"Action: {request.action}\nTarget: {target.kind} {target.telegram_id}\nGeneration: {target.generation}"
            )
            preview += f"\nInternal reason (private): {request.reason or 'None'}"
            preview += f"\nPublic explanation: {request.public_explanation or 'None'}"
            preview += f"\nBan kind: {request.ban_kind}; feature: {request.feature}; request: {request.request_id}"
            window = await message.answer(html.escape(preview))
            token = await self._sessions.issue(request, target, actor.actor_id or 0, window.message_id)
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Confirm", callback_data=AdminCallback(token=token).pack()),
                        InlineKeyboardButton(
                            text="Cancel", callback_data=AdminCallback(token=token, cancel=True).pack()
                        ),
                    ]
                ]
            )
            await window.edit_reply_markup(reply_markup=keyboard)
        except PermissionError, ValueError:
            await message.answer(ADMIN_UNAVAILABLE)

    async def handle_callback(self, query: CallbackQuery, callback_data: AdminCallback) -> None:
        """Reject other transports, stale authority and replay before touching audited state."""
        message = query.message
        if (
            not isinstance(message, Message)
            or message.chat.type != ChatType.PRIVATE
            or message.chat.id != query.from_user.id
            or query.from_user.is_bot
        ):
            await query.answer(ADMIN_UNAVAILABLE, show_alert=True)
            return
        try:
            actor = ActorContext.telegram(query.from_user.id)
            await self._administration.authorize(actor, message.chat.type)
            confirmation = await self._sessions.resolve(
                callback_data.token, query.from_user.id, message.message_id, consume=True
            )
            if confirmation is None:
                await query.answer(ADMIN_UNAVAILABLE, show_alert=True)
                return
            if callback_data.cancel:
                result: dict[str, object] = {"cancelled": True}
            else:
                actor = ActorContext.telegram(query.from_user.id, reason=confirmation.request.reason)
                result = await self._administration.execute(
                    confirmation.request, confirmation.target, actor, message.chat.type
                )
            await message.edit_text(html.escape(json.dumps(result, ensure_ascii=False)), reply_markup=None)
            await query.answer()
        except PermissionError, ValueError:
            await query.answer(ADMIN_UNAVAILABLE, show_alert=True)

    def register_callbacks(self, router: Router, flags: dict[str, Any]) -> None:
        """Register the same private admin policy for opaque confirmations."""
        router.callback_query.register(self.handle_callback, AdminCallback.filter(), flags=flags)
