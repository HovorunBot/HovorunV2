"""Private access administration with target previews and single-use confirmation."""

import json
import logging
import shlex
from collections.abc import Mapping
from typing import ClassVar

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestUsers,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from hovorunv2.application.access.access_service import CommandPolicy
from hovorunv2.application.access.administration import AdminAction, AdminRequest, UnknownAdminHandleError
from hovorunv2.application.access.administration_service import AdministrationService
from hovorunv2.application.settings.admin_session_service import AdminSessionService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName, TargetKind
from hovorunv2.interface.access_arguments import parse_access
from hovorunv2.interface.telegram.handlers.base import BaseCommand
from hovorunv2.interface.telegram.i18n import get_message
from hovorunv2.interface.telegram.settings.callbacks import AdminCallback

logger = logging.getLogger(__name__)


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

    async def handle(self, message: Message, bot: Bot, **kwargs: object) -> None:
        """Preview the target, provisioning unseen numeric users without granting trust.

        Args:
            message: Telegram message triggering the command.
            bot: Telegram bot used to deliver the preview.
            **kwargs: Additional handler context, unused.
        """
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
                await message.answer(get_message("admin-help"))
                return
            if words == [AdminAction.TRUST, TargetKind.USER]:
                await self._offer_user_selection(message, message.from_user.id)
                return
            request = parse_access(words)
            logger.info(
                "Processing /admin command from user %d: action=%s kind=%s",
                message.from_user.id,
                request.action.value,
                request.kind.value,
            )
            await self._preview_request(message, request, message.from_user.id)
        except UnknownAdminHandleError:
            await message.answer(get_message("admin-unknown-handle"))
        except ValueError:
            await message.answer(get_message("admin-invalid-request"))
        except PermissionError:
            await message.answer(get_message("admin-unavailable"))

    async def _offer_user_selection(self, message: Message, actor_id: int, request: AdminRequest | None = None) -> None:
        """Offer a single-person Telegram picker requesting only the numeric identity."""
        request_id = await self._sessions.issue_user_selection(actor_id, request)
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text=get_message("admin-choose-user"),
                        request_users=KeyboardButtonRequestUsers(
                            request_id=request_id,
                            user_is_bot=False,
                            max_quantity=1,
                            request_name=False,
                            request_username=False,
                            request_photo=False,
                        ),
                    )
                ]
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await message.answer(get_message("admin-user-selection-prompt"), reply_markup=keyboard)

    async def _preview_request(self, message: Message, request: AdminRequest, actor_id: int) -> None:
        """Resolve typed or selected users through the same confirmation workflow."""
        actor = ActorContext.telegram(actor_id)
        try:
            target = await self._administration.prepare_target(request, actor, message.chat.type)
        except UnknownAdminHandleError:
            if request.action != AdminAction.TRUST:
                raise
            await message.answer(get_message("admin-unknown-handle"))
            await self._offer_user_selection(message, actor_id, request)
            return
        state = await self._administration.inspect(target, actor, message.chat.type)
        if request.action in {AdminAction.INSPECT, AdminAction.REQUESTS}:
            await message.answer(json.dumps(state, ensure_ascii=False), parse_mode=None)
            return
        preview = (
            f"Action: {request.action}\nTarget: {target.kind} {target.telegram_id}\nGeneration: {target.generation}"
        )
        preview += f"\nInternal reason (private): {request.reason or 'None'}"
        preview += f"\nPublic explanation: {request.public_explanation or 'None'}"
        preview += f"\nBan kind: {request.ban_kind}; feature: {request.feature}; request: {request.request_id}"
        if request.bucket is not None:
            preview += (
                f"\nLimit: {request.bucket} {request.unit} window={request.window_seconds}s "
                f"limit={request.limit} burst={request.burst} concurrency={request.concurrency} "
                f"pending={request.pending}"
            )
        window = await message.answer(preview, parse_mode=None)
        token = await self._sessions.issue(request, target, actor_id, window.message_id)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=get_message("admin-btn-confirm"), callback_data=AdminCallback(token=token).pack()
                    ),
                    InlineKeyboardButton(
                        text=get_message("admin-btn-cancel"),
                        callback_data=AdminCallback(token=token, cancel=True).pack(),
                    ),
                ]
            ]
        )
        await window.edit_reply_markup(reply_markup=keyboard)

    async def handle_users_shared(self, message: Message) -> None:
        """Preview a selected user only for the current private administrator.

        Args:
            message: Telegram service message containing the selected user identity.
        """
        if message.chat.type != ChatType.PRIVATE or message.from_user is None:
            return
        if message.from_user.is_bot or message.sender_chat is not None or message.chat.id != message.from_user.id:
            return
        shared = message.users_shared
        if shared is None:
            return
        if len(shared.users) != 1:
            await message.answer(get_message("admin-selection-unavailable"), reply_markup=ReplyKeyboardRemove())
            return
        actor_id = message.from_user.id
        try:
            await self._administration.authorize(ActorContext.telegram(actor_id), message.chat.type)
            selection = await self._sessions.consume_user_selection(actor_id, shared.request_id)
            await message.answer(get_message("admin-user-selected"), reply_markup=ReplyKeyboardRemove())
            target_id = str(shared.users[0].user_id)
            request = (
                selection.request.model_copy(update={"target": target_id})
                if selection.request is not None
                else AdminRequest(action=AdminAction.TRUST, kind=TargetKind.USER, target=target_id)
            )
            await self._preview_request(message, request, actor_id)
        except PermissionError:
            await message.answer(get_message("admin-unavailable"), reply_markup=ReplyKeyboardRemove())
        except ValueError:
            await message.answer(get_message("admin-selection-unavailable"), reply_markup=ReplyKeyboardRemove())

    async def handle_callback(self, query: CallbackQuery, callback_data: AdminCallback) -> None:
        """Reject other transports, stale authority and replay before touching audited state.

        Args:
            query: Callback query carrying the confirmation decision.
            callback_data: Unpacked admin callback payload.
        """
        message = query.message
        if (
            not isinstance(message, Message)
            or message.chat.type != ChatType.PRIVATE
            or message.chat.id != query.from_user.id
            or query.from_user.is_bot
        ):
            await query.answer(get_message("admin-unavailable"), show_alert=True)
            return
        try:
            actor = ActorContext.telegram(query.from_user.id)
            await self._administration.authorize(actor, message.chat.type)
            confirmation = await self._sessions.resolve(
                callback_data.token, query.from_user.id, message.message_id, consume=True
            )
            if confirmation is None:
                await query.answer(get_message("admin-unavailable"), show_alert=True)
                return
            if callback_data.cancel:
                result: dict[str, object] = {"cancelled": True}
                logger.info("Admin callback cancelled by user %d", query.from_user.id)
            else:
                actor = ActorContext.telegram(query.from_user.id, reason=confirmation.request.reason)
                result = await self._administration.execute(
                    confirmation.request, confirmation.target, actor, message.chat.type
                )
                logger.info(
                    "Admin callback executed action: action=%s outcome=%s",
                    confirmation.request.action.value,
                    result.get("outcome", "success"),
                )
            if result.get("cancelled") is True:
                feedback = get_message("admin-cancelled")
            elif isinstance(result.get("changed"), bool):
                feedback = get_message("admin-saved" if result["changed"] else "admin-unchanged")
            else:
                feedback = json.dumps(result, ensure_ascii=False)
            await message.edit_text(feedback, parse_mode=None, reply_markup=None)
            await query.answer()
        except PermissionError, ValueError:
            await query.answer(get_message("admin-unavailable"), show_alert=True)

    def register_callbacks(self, router: Router, flags: Mapping[str, object]) -> None:
        """Register the same private admin policy for opaque confirmations.

        Args:
            router: Router receiving the callback registration.
            flags: Handler flags bound to the callback route.
        """
        router.callback_query.register(self.handle_callback, AdminCallback.filter(), flags=dict(flags))
        router.message.register(self.handle_users_shared, F.users_shared, flags=dict(flags))
