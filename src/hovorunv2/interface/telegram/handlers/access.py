"""Access management command module."""

import html
from collections.abc import Sequence
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.application.data.constants import ChatStatus, CommandName, UserTier
from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.callbacks import AccessAction, AccessCallback

from .base import BaseCommand

logger = get_logger(__name__)


class AccessCommand(BaseCommand):
    """Command to handle chat access requests and approvals."""

    def __init__(
        self,
        chat_status_service: ChatStatusService,
        command_service: CommandService,
        access_service: AccessService,
        commands: Sequence[BaseCommand],
    ) -> None:
        """Initialize command with its dependencies."""
        self._chat_status_service = chat_status_service
        self._command_service = command_service
        self._access_service = access_service
        self._commands = commands

    @property
    def name(self) -> str:
        """Command name."""
        return CommandName.ALLOW_CHAT

    @property
    def policy(self) -> CommandPolicy:
        """Everyone can trigger it (it will send a request if not VIP/admin)."""
        return CommandPolicy(requires_admin=False, requires_approval=False, is_toggleable=False)

    async def is_triggered(self, message: Message) -> bool:
        """Check if message is /allow_chat."""
        return bool(message.text and message.text.strip().startswith("/allow_chat"))

    async def handle(
        self,
        message: Message,
        bot: Bot,
        **kwargs: Any,
    ) -> None:
        """Handle access command."""
        user_id = message.from_user.id if message.from_user else 0
        if await self._access_service.is_banned(user_id):
            await message.answer("❌ You are forbidden from activating this bot.")
            return

        chat_id = message.chat.id
        status = await self._chat_status_service.get_status(chat_id)
        if status == ChatStatus.APPROVED:
            await message.answer("ℹ️ This chat is already approved.")  # noqa: RUF001
            return

        if await self._access_service.is_vip(user_id):
            await self._approve_immediately(message)
            return

        await self._send_access_request(message, bot)

    async def _approve_chat(self, chat_id: int) -> None:
        """Persist approved status for a chat and auto-enable designated commands."""
        await self._chat_status_service.set_status(chat_id, ChatStatus.APPROVED)
        for command in (c for c in self._commands if c.policy.auto_enable):
            await self._command_service.enable_command(chat_id, command.name)

    async def _approve_immediately(self, message: Message) -> None:
        """Approve the chat immediately for VIPs and admins."""
        chat_id = message.chat.id
        await self._approve_chat(chat_id)
        await message.answer("✅ Bot is now approved for this chat.")
        logger.info("Bot approved in chat %d by user %s", chat_id, message.from_user.id if message.from_user else "0")

    async def _send_access_request(self, message: Message, bot: Bot, *, silent: bool = False) -> None:
        """Send access request to bot administrators."""
        chat = message.chat
        user = message.from_user
        if not user:
            return

        chat_id = chat.id
        status = await self._chat_status_service.get_status(chat_id)

        match status:
            case ChatStatus.APPROVED:
                await message.answer("ℹ️ This chat is already approved.")  # noqa: RUF001
                return
            case ChatStatus.PENDING if not silent:
                await message.answer("⏳ Approval is already pending. Administrators have been notified.")
                return
            case ChatStatus.PENDING:
                pass
            case ChatStatus.BANNED:
                await message.answer("❌ This chat is forbidden from using this bot.")
                await bot.leave_chat(chat_id)
                return
            case _:
                pass

        await self._chat_status_service.set_status(chat_id, ChatStatus.PENDING)
        if not silent:
            await message.answer("⏳ <i>Request sent to bot administrators for approval.</i>", parse_mode="HTML")

        request_text = self._build_request_text(
            chat.title, chat_id, chat.type, user.full_name, user.id, user.username
        )
        markup = self._build_admin_decision_keyboard(chat_id)
        await self._broadcast_request_to_admins(bot, request_text, markup)

    def _build_request_text(
        self,
        chat_title: str | None,
        chat_id: int,
        chat_type: str,
        user_name: str,
        user_id: int,
        username: str | None,
    ) -> str:
        """Construct access request notification HTML text."""
        return (
            "🔔 <b>Access Request</b>\n\n"
            f"<b>Group:</b> {html.escape(chat_title or 'Unknown')}\n"
            f"<b>ID:</b> <code>{chat_id}</code>\n"
            f"<b>Type:</b> {chat_type}\n\n"
            f"<b>Requester:</b> {html.escape(user_name)}\n"
            f"<b>User ID:</b> <code>{user_id}</code>\n"
            f"<b>Username:</b> @{username or 'N/A'}"
        )

    def _build_admin_decision_keyboard(self, chat_id: int) -> Any:
        """Build inline keyboard for admin approval/rejection."""
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
        return builder.as_markup()

    async def _broadcast_request_to_admins(self, bot: Bot, text: str, reply_markup: Any) -> None:
        """Deliver request message to all registered administrators."""
        admin_ids = await self._access_service.get_admin_ids()
        for admin_id in admin_ids:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
            except TelegramForbiddenError:
                logger.warning(
                    "Cannot send access request to admin %d: Bot is blocked or conversation not initiated.",
                    admin_id,
                )
            except Exception:
                logger.exception("Failed to send access request to admin %d", admin_id)

    async def handle_callback(
        self,
        query: CallbackQuery,
        callback_data: AccessCallback,
        bot: Bot,
    ) -> None:
        """Process administrator's decision or join request from callback query."""
        match callback_data.action:
            case AccessAction.REQUEST:
                await self.handle_request_callback(query, bot, callback_data)
            case AccessAction.APPROVE | AccessAction.REJECT:
                await self._handle_admin_decision(query, callback_data, bot)
            case _:
                return

    async def _handle_admin_decision(
        self,
        query: CallbackQuery,
        callback_data: AccessCallback,
        bot: Bot,
    ) -> None:
        """Process administrator approve or reject callback."""
        admin = query.from_user
        if not await self._access_service.is_admin(admin.id):
            await query.answer("🚫 Only administrators can approve chats.", show_alert=True)
            return

        chat_id = callback_data.chat_id
        match callback_data.action:
            case AccessAction.APPROVE:
                status_text = await self._apply_admin_approval(chat_id, admin.full_name, bot)
            case AccessAction.REJECT:
                status_text = await self._apply_admin_rejection(chat_id, admin.full_name)
            case _:
                return

        await self._update_admin_decision_message(query, status_text, callback_data.action)

    async def _apply_admin_approval(self, chat_id: int, admin_name: str, bot: Bot) -> str:
        """Apply approval status and notify group."""
        await self._approve_chat(chat_id)
        logger.info("Chat %d APPROVED by admin %s", chat_id, admin_name)
        await bot.send_message(
            chat_id=chat_id,
            text="🚀 <b>Bot has been approved for this chat!</b>",
            parse_mode="HTML",
        )
        return f"✅ Approved by {admin_name}"

    async def _apply_admin_rejection(self, chat_id: int, admin_name: str) -> str:
        """Apply rejected status to chat."""
        await self._chat_status_service.set_status(chat_id, ChatStatus.REJECTED)
        logger.info("Chat %d REJECTED by admin %s", chat_id, admin_name)
        return f"❌ Rejected by {admin_name}"

    async def _update_admin_decision_message(
        self,
        query: CallbackQuery,
        status_text: str,
        action: AccessAction,
    ) -> None:
        """Update inline message text in admin DM."""
        if not isinstance(query.message, Message):
            return

        curr_text = query.message.text or ""
        new_text = f"{curr_text}\n\n<b>Status:</b> {status_text}" if curr_text else status_text
        await query.message.edit_text(text=new_text, parse_mode="HTML", reply_markup=None)
        await query.answer(f"Chat {action}ed.")

    async def handle_request_callback(
        self,
        query: CallbackQuery,
        bot: Bot,
        callback_data: AccessCallback | None = None,
    ) -> None:
        """Handle request for access from inline button."""
        if not isinstance(query.message, Message):
            return

        user_id = query.from_user.id
        tier = await self._access_service.get_user_tier(user_id)
        if tier <= UserTier.BANNED:
            await query.answer("❌ You are forbidden from using this bot.", show_alert=True)
            return

        chat_id = callback_data.chat_id if callback_data else query.message.chat.id

        if tier >= UserTier.VIP:
            await self._process_vip_callback_approval(query, chat_id)
            return

        await self._process_chat_member_request(query, bot, chat_id, user_id)

    async def _process_vip_callback_approval(self, query: CallbackQuery, chat_id: int) -> None:
        """Approve chat directly when inline button clicked by VIP or admin."""
        if not isinstance(query.message, Message):
            return
        await self._approve_chat(chat_id)
        await query.message.edit_text(
            text="✅ <b>Bot has been approved for this chat!</b>",
            parse_mode="HTML",
            reply_markup=None,
        )
        await query.answer("Chat approved.")

    async def _process_chat_member_request(
        self,
        query: CallbackQuery,
        bot: Bot,
        chat_id: int,
        user_id: int,
    ) -> None:
        """Validate group admin permissions and forward request to bot admins."""
        if not isinstance(query.message, Message):
            return

        member = await bot.get_chat_member(chat_id, user_id)
        match member.status:
            case "creator" | "administrator":
                await self._send_access_request(query.message, bot, silent=True)
                await query.message.edit_text(
                    text="⏳ <b>Request sent to bot administrators for approval.</b>",
                    parse_mode="HTML",
                    reply_markup=None,
                )
                await query.answer("Request sent.")
            case _:
                await query.answer("❌ Only administrators can request approval.", show_alert=True)

    def register_callbacks(self, router: Router, flags: dict[str, Any]) -> None:
        """Register callback handlers with router."""
        router.callback_query.register(
            self.handle_request_callback,
            AccessCallback.filter(F.action == AccessAction.REQUEST),
        )
        router.callback_query.register(
            self.handle_callback,
            AccessCallback.filter(F.action != AccessAction.REQUEST),
        )
