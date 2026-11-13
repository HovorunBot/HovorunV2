"""Intentional group activation and private approval decisions."""

import html
import logging
from collections.abc import Mapping
from typing import ClassVar

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.application.access.access_service import CommandPolicy
from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.groups.activation import ActivationAction, ActivationOutcome, ActivationResult
from hovorunv2.application.groups.activation_service import GroupActivationService
from hovorunv2.application.messaging.owner_notifier import OwnerNotifier
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.i18n import AudienceI18nManager, get_message
from hovorunv2.interface.telegram.settings.callbacks import AccessAction, AccessCallback, SetupAction, SetupCallback

from .base import BaseCommand

logger = logging.getLogger(__name__)

RESULT_KEYS: dict[ActivationOutcome, str] = {
    ActivationOutcome.APPROVED: "access-approved",
    ActivationOutcome.ALREADY_APPROVED: "access-already-approved",
    ActivationOutcome.REQUESTED: "access-requested",
    ActivationOutcome.ALREADY_PENDING: "access-already-pending",
    ActivationOutcome.REJECTED: "access-rejected",
    ActivationOutcome.DENIED: "access-denied",
    ActivationOutcome.UNAVAILABLE: "access-unavailable",
    ActivationOutcome.GROUP_BANNED: "access-group-banned",
    ActivationOutcome.STALE_REQUEST: "access-stale-request",
}
SUCCESS_OUTCOMES = {
    ActivationOutcome.APPROVED,
    ActivationOutcome.ALREADY_APPROVED,
    ActivationOutcome.REQUESTED,
    ActivationOutcome.ALREADY_PENDING,
    ActivationOutcome.REJECTED,
}


def _build_configure_chat_markup(locale: str | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=get_message("setup-btn-configure", locale),
        callback_data=SetupCallback(action=SetupAction.START).pack(),
    )
    return builder.as_markup()


class AccessCommand(BaseCommand):
    """Activate through trust or submit a group-administrator request for private review."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(requires_admin=False, requires_approval=False, is_toggleable=False)

    def __init__(
        self,
        activation_service: GroupActivationService,
        owner_notifier: OwnerNotifier,
        i18n_manager: AudienceI18nManager,
    ) -> None:
        """Receive mandatory activation, administrator-notification, and i18n dependencies."""
        self._activation = activation_service
        self._notifier = owner_notifier
        self._i18n_manager = i18n_manager

    @property
    def name(self) -> str:
        """Command name."""
        return CommandName.ALLOW_CHAT

    @property
    def trigger(self) -> Command:
        """Trigger filter for allow_chat command."""
        return Command(CommandName.ALLOW_CHAT)

    async def is_triggered(self, message: Message) -> bool:
        """Return whether the message targets this command."""
        return self._is_slash_command_triggered(message, CommandName.ALLOW_CHAT)

    async def handle(self, message: Message, bot: Bot, **kwargs: object) -> None:
        """Bind activation to the known group carrying the authenticated human command.

        Args:
            message: Telegram message triggering the command.
            bot: Telegram bot used to deliver activation notifications.
            **kwargs: Additional handler context, unused.
        """
        if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            await message.answer(get_message("access-run-in-group"))
            return
        if message.from_user is None or message.from_user.is_bot or message.sender_chat is not None:
            return
        logger.info("Processing /allow_chat command in chat %d from user %d", message.chat.id, message.from_user.id)
        target = await self._activation.target(message.chat.id)
        if target is None:
            await message.answer(get_message(RESULT_KEYS[ActivationOutcome.UNAVAILABLE]))
            return
        result = await self._enable(message, message.from_user, bot, target)
        logger.info("Allow chat result in chat %d: outcome=%s", message.chat.id, result.outcome.value)
        group_locale = await self._i18n_manager.get_chat_locale(message.chat.id)
        markup = _build_configure_chat_markup(group_locale) if result.outcome == ActivationOutcome.APPROVED else None
        await message.answer(get_message(RESULT_KEYS[result.outcome], group_locale), reply_markup=markup)

    async def _enable(self, message: Message, actor: User, bot: Bot, target: PermissionTarget) -> ActivationResult:
        result = await self._activation.enable(actor.id, target, message.chat.type)
        if result.outcome == ActivationOutcome.REQUESTED and result.request_id is not None:
            await self._notify_request(message, actor, bot, target, result.request_id)
        return result

    async def _notify_request(
        self, message: Message, actor: User, bot: Bot, target: PermissionTarget, request_id: int
    ) -> None:
        """Notify current ADMIN/OWNER recipients once, using only public operational metadata.

        Args:
            message: Telegram message carrying the group context.
            actor: User requesting activation.
            bot: Telegram bot used to broadcast the request.
            target: Permission target for the group being activated.
            request_id: Identifier of the saved activation request.
        """
        request_text = (
            "🔔 <b>Access Request</b>\n\n"
            f"<b>Group:</b> {html.escape(message.chat.title or 'Unknown')}\n"
            f"<b>Group ID:</b> <code>{target.telegram_id}</code>\n"
            f"<b>Requester ID:</b> <code>{actor.id}</code>\n"
            f"<b>Username:</b> {html.escape('@' + actor.username) if actor.username else 'Unavailable'}"
        )
        builder = InlineKeyboardBuilder()
        builder.row(
            *[
                InlineKeyboardButton(
                    text=label,
                    callback_data=AccessCallback(
                        chat_id=message.chat.id,
                        group_id=target.entity_id or 0,
                        generation=target.generation,
                        request_id=request_id,
                        action=action,
                    ).pack(),
                )
                for label, action in (
                    (get_message("access-btn-approve"), AccessAction.APPROVE),
                    (get_message("access-btn-reject"), AccessAction.REJECT),
                )
            ]
        )
        await self._notifier.broadcast(bot, request_text, reply_markup=builder.as_markup(), include_admins=True)

    async def handle_callback(self, query: CallbackQuery, callback_data: AccessCallback, bot: Bot) -> None:
        """Use the callback actor and revalidate the exact selected group/request at mutation.

        Args:
            query: Callback query carrying the approval decision.
            callback_data: Unpacked access callback payload.
            bot: Telegram bot used to confirm approvals.
        """
        user_locale = await self._i18n_manager.get_user_locale(query.from_user.id)
        message = query.message
        if not isinstance(message, Message) or query.from_user.is_bot:
            await query.answer(get_message(RESULT_KEYS[ActivationOutcome.UNAVAILABLE], user_locale), show_alert=True)
            return
        if callback_data.group_id <= 0 or callback_data.generation <= 0:
            await query.answer(get_message(RESULT_KEYS[ActivationOutcome.UNAVAILABLE], user_locale), show_alert=True)
            return
        logger.info(
            "Processing access callback in chat %d: action=%s group_id=%d",
            callback_data.chat_id,
            callback_data.action.value,
            callback_data.group_id,
        )
        target = PermissionTarget("group", callback_data.group_id, callback_data.generation, callback_data.chat_id)
        if callback_data.action == AccessAction.REQUEST:
            if (
                message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}
                or message.chat.id != callback_data.chat_id
            ):
                await query.answer(
                    get_message(RESULT_KEYS[ActivationOutcome.UNAVAILABLE], user_locale), show_alert=True
                )
                return
            result = await self._enable(message, query.from_user, bot, target)
        else:
            if message.chat.type != ChatType.PRIVATE or message.chat.id != query.from_user.id:
                await query.answer(get_message("access-private-required", user_locale), show_alert=True)
                return
            action = (
                ActivationAction.APPROVE if callback_data.action == AccessAction.APPROVE else ActivationAction.REJECT
            )
            result = await self._activation.decide(
                query.from_user.id, target, callback_data.request_id, action, message.chat.type
            )
            if result.outcome == ActivationOutcome.APPROVED:
                group_locale = await self._i18n_manager.get_chat_locale(callback_data.chat_id)
                try:
                    await bot.send_message(
                        callback_data.chat_id,
                        get_message(RESULT_KEYS[result.outcome], group_locale),
                        reply_markup=_build_configure_chat_markup(group_locale),
                    )
                except TelegramAPIError:
                    logger.warning("Failed to deliver approval confirmation to chat %d", callback_data.chat_id)
        if result.outcome in SUCCESS_OUTCOMES:
            try:
                await bot.edit_message_text(
                    text=get_message(RESULT_KEYS[result.outcome]),
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=None,
                )
            except TelegramAPIError:
                logger.warning("Failed to edit access request prompt in chat %d", message.chat.id)
        await query.answer(
            get_message(RESULT_KEYS[result.outcome], user_locale),
            show_alert=result.outcome not in SUCCESS_OUTCOMES,
        )

    def register_callbacks(self, router: Router, flags: Mapping[str, object]) -> None:
        """Register callback handlers with router.

        Args:
            router: Router receiving the callback registration.
            flags: Handler flags bound to the callback route.
        """
        router.callback_query.register(self.handle_callback, AccessCallback.filter(), flags=dict(flags))
