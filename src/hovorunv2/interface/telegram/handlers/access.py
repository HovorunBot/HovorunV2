"""Access management command module."""

import html
from typing import Any, ClassVar, cast

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.owner_notifier import OwnerNotifier
from hovorunv2.domain.enums import ChatStatus, CommandName
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.callbacks import AccessAction, AccessCallback

from .base import BaseCommand

logger = get_logger(__name__)


class AccessCommand(BaseCommand):
    """Command to handle chat access requests and approvals."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(requires_admin=False, requires_approval=False, is_toggleable=False)

    def __init__(
        self,
        chat_status_service: ChatStatusService,
        access_service: AccessService,
        owner_notifier: OwnerNotifier,
    ) -> None:
        """Initialize command with its dependencies."""
        self._chat_status_service = chat_status_service
        self._access_service = access_service
        self._owner_notifier = owner_notifier

    @property
    def name(self) -> str:
        """Command name."""
        return CommandName.ALLOW_CHAT

    @property
    def trigger(self) -> Command:
        """Trigger filter for allow_chat command."""
        return Command(CommandName.ALLOW_CHAT)

    async def is_triggered(self, message: Message) -> bool:
        """Check if message is /allow_chat."""
        if not message.text:
            return False
        parts = message.text.strip().split()
        if not parts:
            return False
        cmd_part = parts[0].lstrip("/").split("@")[0].lower()
        return cmd_part == CommandName.ALLOW_CHAT

    async def handle(
        self,
        message: Message,
        bot: Bot,
        **kwargs: Any,
    ) -> None:
        """Handle access command."""
        user_id = message.from_user.id if message.from_user else 0
        is_admin = await self._access_service.is_admin(user_id)

        if is_admin:
            await self._approve_immediately(message)
            return

        await self._send_access_request(message, bot)

    async def _approve_immediately(self, message: Message) -> None:
        """Approve the chat immediately for admins."""
        chat_id = message.chat.id
        await self._chat_status_service.approve(chat_id)

        await message.answer("✅ Bot is now approved for this chat by administrator.")
        logger.info("Bot approved in chat %d by admin %s", chat_id, message.from_user.id if message.from_user else "0")

    async def _send_access_request(self, message: Message, bot: Bot, *, silent: bool = False) -> None:
        """Send access request to bot owners."""
        chat = message.chat
        user = message.from_user
        if not user:
            return

        chat_id = chat.id
        status = await self._chat_status_service.get_status(chat_id)

        if status == ChatStatus.APPROVED:
            await message.answer("ℹ️ This chat is already approved.")  # noqa: RUF001
            return

        if status == ChatStatus.PENDING and not silent:
            await message.answer("⏳ Approval is already pending. Owners have been notified.")
            return

        if status == ChatStatus.BANNED:
            await message.answer("❌ This chat is forbidden from using this bot.")
            await bot.leave_chat(chat_id)
            return

        # Set status to PENDING
        await self._chat_status_service.set_status(chat_id, ChatStatus.PENDING)

        # Notify group
        if not silent:
            await message.answer("⏳ <i>Request sent to bot owners for approval.</i>", parse_mode="HTML")

        # Build request message for owners
        request_text = (
            "🔔 <b>Access Request</b>\n\n"
            f"<b>Group:</b> {html.escape(chat.title or 'Unknown')}\n"
            f"<b>ID:</b> <code>{chat_id}</code>\n"
            f"<b>Type:</b> {chat.type}\n\n"
            f"<b>Requester:</b> {html.escape(user.full_name)}\n"
            f"<b>User ID:</b> <code>{user.id}</code>\n"
            f"<b>Username:</b> @{user.username or 'N/A'}"
        )

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text="✅ Approve",
                callback_data=AccessCallback(chat_id=chat_id, action=AccessAction.APPROVE).pack(),
            ),
            InlineKeyboardButton(
                text="❌ Reject", callback_data=AccessCallback(chat_id=chat_id, action=AccessAction.REJECT).pack()
            ),
        )

        # Send to all owners
        await self._owner_notifier.broadcast(bot, request_text, reply_markup=builder.as_markup())

    async def handle_callback(self, query: Any, callback_data: AccessCallback, bot: Bot) -> None:
        """Process owner's decision or join request from callback query."""
        query = cast(CallbackQuery, query)
        chat_id = callback_data.chat_id
        action = callback_data.action

        if action == AccessAction.REQUEST:
            await self._handle_request_callback(query, bot)
            return

        if not await self._access_service.is_owner(query.from_user.id):
            await query.answer("🚫 Owners only.", show_alert=True)
            return

        owner = query.from_user

        if action == AccessAction.APPROVE:
            await self._chat_status_service.approve(chat_id)

            status_text = f"✅ Approved by {owner.full_name}"
            logger.info("Chat %d APPROVED by owner %d", chat_id, owner.id)
            await bot.send_message(
                chat_id=chat_id, text="🚀 <b>Bot has been approved for this chat!</b>", parse_mode="HTML"
            )
        else:
            await self._chat_status_service.set_status(chat_id, ChatStatus.REJECTED)
            status_text = f"❌ Rejected by {owner.full_name}"
            logger.info("Chat %d REJECTED by owner %d", chat_id, owner.id)

        # Update owner's message
        if not query.message or not isinstance(query.message, Message):
            return

        curr_text = query.message.text or ""
        new_text = f"{curr_text}\n\n<b>Status:</b> {status_text}" if curr_text else status_text
        await query.message.edit_text(text=new_text, parse_mode="HTML", reply_markup=None)
        await query.answer(f"Chat {action}ed.")

    async def _handle_request_callback(self, query: CallbackQuery, bot: Bot) -> None:
        """Handle request for access from inline button."""
        if not query.message or not isinstance(query.message, Message):
            return

        # Check if user is admin in the chat
        chat_id = query.message.chat.id
        user_id = query.from_user.id
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status not in ("creator", "administrator") and not await self._access_service.is_admin(user_id):
            await query.answer("❌ Only administrators can request approval.", show_alert=True)
            return

        # Send request to owners
        await self._send_access_request(query.message, bot, silent=True)

        # Update message in group
        await query.message.edit_text(
            text="⏳ <b>Request sent to bot owners for approval.</b>",
            parse_mode="HTML",
            reply_markup=None,
        )
        await query.answer("Request sent.")

    def register_callbacks(self, router: Router, flags: dict[str, Any]) -> None:
        """Register callback handlers with router."""
        router.callback_query.register(self.handle_callback, AccessCallback.filter(), flags=flags)
