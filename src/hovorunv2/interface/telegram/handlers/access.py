"""Intentional group activation and private approval decisions."""

import html
from typing import Any, ClassVar

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message, User
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.application.access.access_service import CommandPolicy
from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.groups.activation import ActivationAction, ActivationOutcome, ActivationResult
from hovorunv2.application.groups.activation_service import GroupActivationService
from hovorunv2.application.messaging.owner_notifier import OwnerNotifier
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.settings.callbacks import AccessAction, AccessCallback

from .base import BaseCommand

RESULT_TEXT = {
    ActivationOutcome.APPROVED: "✅ Group approval saved. Processing remains subject to current access checks.",
    ActivationOutcome.ALREADY_APPROVED: "ℹ️ This chat is already approved.",  # noqa: RUF001
    ActivationOutcome.REQUESTED: "⏳ Access request saved for bot administrator review.",
    ActivationOutcome.ALREADY_PENDING: "⏳ Approval is already pending.",
    ActivationOutcome.REJECTED: "❌ Access request rejected.",
    ActivationOutcome.DENIED: "❌ Trusted users can activate the bot. Other group admins can request approval.",
    ActivationOutcome.UNAVAILABLE: "❌ Group or user unavailable. Run /allow_chat in the group to refresh.",
    ActivationOutcome.GROUP_BANNED: "❌ This group is restricted. Approval cannot remove that restriction.",
    ActivationOutcome.STALE_REQUEST: "❌ This request is no longer available.",
}
SUCCESS_OUTCOMES = {
    ActivationOutcome.APPROVED,
    ActivationOutcome.ALREADY_APPROVED,
    ActivationOutcome.REQUESTED,
    ActivationOutcome.ALREADY_PENDING,
    ActivationOutcome.REJECTED,
}


class AccessCommand(BaseCommand):
    """Activate through trust or submit a group-administrator request for private review."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(requires_admin=False, requires_approval=False, is_toggleable=False)

    def __init__(self, activation_service: GroupActivationService, owner_notifier: OwnerNotifier) -> None:
        """Receive mandatory activation and durable administrator-notification dependencies."""
        self._activation = activation_service
        self._notifier = owner_notifier

    @property
    def name(self) -> str:
        """Command name."""
        return CommandName.ALLOW_CHAT

    @property
    def trigger(self) -> Command:
        """Trigger filter for allow_chat command."""
        return Command(CommandName.ALLOW_CHAT)

    async def is_triggered(self, message: Message) -> bool:
        """Return whether the message text targets the /allow_chat command.

        Args:
            message: Telegram message to inspect.

        Returns:
            True if the message text starts with /allow_chat.
        """
        if not message.text:
            return False
        parts = message.text.strip().split()
        if not parts:
            return False
        cmd_part = parts[0].lstrip("/").split("@")[0].lower()
        return cmd_part == CommandName.ALLOW_CHAT

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:
        """Bind activation to the known group carrying the authenticated human command.

        Args:
            message: Telegram message triggering the command.
            bot: Telegram bot used to deliver activation notifications.
            **kwargs: Additional handler context, unused.
        """
        if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            await message.answer("Run /allow_chat in the group you want to activate.")
            return
        if message.from_user is None or message.from_user.is_bot or message.sender_chat is not None:
            return
        target = await self._activation.target(message.chat.id)
        if target is None:
            await message.answer(RESULT_TEXT[ActivationOutcome.UNAVAILABLE])
            return
        result = await self._enable(message, message.from_user, bot, target)
        await message.answer(RESULT_TEXT[result.outcome])

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
                for label, action in (("✅ Approve", AccessAction.APPROVE), ("❌ Reject", AccessAction.REJECT))
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
        message = query.message
        if not isinstance(message, Message) or query.from_user.is_bot:
            await query.answer(RESULT_TEXT[ActivationOutcome.UNAVAILABLE], show_alert=True)
            return
        if callback_data.group_id <= 0 or callback_data.generation <= 0:
            await query.answer(RESULT_TEXT[ActivationOutcome.UNAVAILABLE], show_alert=True)
            return
        target = PermissionTarget("group", callback_data.group_id, callback_data.generation, callback_data.chat_id)
        if callback_data.action == AccessAction.REQUEST:
            if (
                message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}
                or message.chat.id != callback_data.chat_id
            ):
                await query.answer(RESULT_TEXT[ActivationOutcome.UNAVAILABLE], show_alert=True)
                return
            result = await self._enable(message, query.from_user, bot, target)
        else:
            if message.chat.type != ChatType.PRIVATE or message.chat.id != query.from_user.id:
                await query.answer("Bot administration requires a private chat.", show_alert=True)
                return
            action = (
                ActivationAction.APPROVE if callback_data.action == AccessAction.APPROVE else ActivationAction.REJECT
            )
            result = await self._activation.decide(
                query.from_user.id, target, callback_data.request_id, action, message.chat.type
            )
            if result.outcome == ActivationOutcome.APPROVED:
                await bot.send_message(callback_data.chat_id, RESULT_TEXT[result.outcome])
        if result.outcome in SUCCESS_OUTCOMES:
            await bot.edit_message_text(
                text=RESULT_TEXT[result.outcome],
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=None,
            )
        await query.answer(RESULT_TEXT[result.outcome], show_alert=result.outcome not in SUCCESS_OUTCOMES)

    def register_callbacks(self, router: Router, flags: dict[str, Any]) -> None:
        """Register callback handlers with router.

        Args:
            router: Router receiving the callback registration.
            flags: Handler flags bound to the callback route.
        """
        router.callback_query.register(self.handle_callback, AccessCallback.filter(), flags=flags)
