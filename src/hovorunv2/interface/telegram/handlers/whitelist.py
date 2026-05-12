"""Whitelist command module."""

import html
from collections.abc import Sequence
from typing import Any, cast

from aiogram import Bot, Router
from aiogram.types import InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.callbacks import WhitelistCallback

from .base import BaseCommand

logger = get_logger(__name__)


class AllowBotCommand(BaseCommand):
    """Command to allow bot in a chat or request allowance."""

    def __init__(
        self,
        whitelist_service: WhitelistService,
        command_service: CommandService,
        access_service: AccessService,
        settings: Settings,
        commands: Sequence[BaseCommand],
    ) -> None:
        """Initialize command with its dependencies."""
        self._whitelist_service = whitelist_service
        self._command_service = command_service
        self._access_service = access_service
        self._settings = settings
        self._commands = commands

    @property
    def name(self) -> str:
        """Command name."""
        return "allow_chat"

    @property
    def policy(self) -> CommandPolicy:
        """Everyone can trigger it (it will send a request if not admin)."""
        return CommandPolicy(requires_admin=False, requires_whitelist=False, is_toggleable=False)

    async def is_triggered(self, message: Message) -> bool:
        """Check if message is /allow_chat."""
        return bool(message.text and message.text.strip().startswith("/allow_chat"))

    async def handle(
        self,
        message: Message,
        bot: Bot,
        **kwargs: Any,  # noqa: ANN401, ARG002
    ) -> None:
        """Handle allow bot command."""
        user_id = message.from_user.id if message.from_user else 0
        is_admin = await self._access_service.is_admin(user_id)

        if is_admin:
            await self._allow_immediately(message)
            return

        await self._send_whitelist_request(message, bot)

    async def _allow_immediately(self, message: Message) -> None:
        """Whitelist the chat immediately for admins."""
        chat_id = message.chat.id
        await self._whitelist_service.add_to_whitelist(chat_id)

        # Auto-allow commands
        for command in (c for c in self._commands if c.policy.auto_enable):
            await self._command_service.enable_command(chat_id, command.name)

        await message.answer("✅ Bot is now allowed in this chat by administrator.")
        logger.info("Bot allowed in chat %d by admin %s", chat_id, message.from_user.id if message.from_user else "0")

    async def _send_whitelist_request(self, message: Message, bot: Bot) -> None:
        """Send whitelist request to bot owners."""
        chat = message.chat
        user = message.from_user
        if not user:
            return

        chat_id = chat.id
        # Check if already whitelisted
        if await self._whitelist_service.is_whitelisted(chat_id):
            await message.answer("ℹ️ This chat is already whitelisted.")  # noqa: RUF001
            return

        # Notify group
        await message.answer("⏳ <i>Request sent to bot owners for approval.</i>", parse_mode="HTML")

        # Build request message for owners
        request_text = (
            "🔔 <b>Whitelist Request</b>\n\n"
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
                text="✅ Approve", callback_data=WhitelistCallback(chat_id=chat_id, action="approve").pack()
            ),
            InlineKeyboardButton(
                text="❌ Reject", callback_data=WhitelistCallback(chat_id=chat_id, action="reject").pack()
            ),
        )

        # Send to all owners
        for owner_id in self._settings.owners:
            try:
                await bot.send_message(
                    chat_id=owner_id,
                    text=request_text,
                    reply_markup=builder.as_markup(),
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception("Failed to send whitelist request to owner %d", owner_id)

    async def handle_callback(self, query: Any, callback_data: WhitelistCallback, bot: Bot) -> None:  # noqa: ANN401
        """Process owner's decision from callback query."""
        from aiogram.types import CallbackQuery  # noqa: PLC0415

        query = cast(CallbackQuery, query)

        chat_id = callback_data.chat_id
        action = callback_data.action
        owner = query.from_user

        if action == "approve":
            await self._whitelist_service.add_to_whitelist(chat_id)
            # Auto-allow commands
            for command in (c for c in self._commands if c.policy.auto_enable):
                await self._command_service.enable_command(chat_id, command.name)

            status_text = f"✅ Approved by {owner.full_name}"
            logger.info("Chat %d APPROVED for whitelist by owner %d", chat_id, owner.id)
            await bot.send_message(
                chat_id=chat_id, text="🚀 <b>Bot has been approved for this chat!</b>", parse_mode="HTML"
            )
        else:
            status_text = f"❌ Rejected by {owner.full_name}"
            logger.info("Chat %d REJECTED for whitelist by owner %d", chat_id, owner.id)

        # Update owner's message
        if not query.message or not isinstance(query.message, Message):
            return

        curr_text = query.message.text or ""
        new_text = f"{curr_text}\n\n<b>Status:</b> {status_text}" if curr_text else status_text
        await query.message.edit_text(text=new_text, parse_mode="HTML", reply_markup=None)
        await query.answer(f"Chat {action}ed.")

    def register_callbacks(self, router: Router, flags: dict[str, Any]) -> None:  # noqa: ARG002
        """Register callback handlers with router."""
        router.callback_query.register(self.handle_callback, WhitelistCallback.filter())
