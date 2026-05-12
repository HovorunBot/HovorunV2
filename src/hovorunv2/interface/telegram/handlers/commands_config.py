"""Handlers for managing chat command configuration."""

from typing import Any, Final, cast

from aiogram import Bot, Router
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.application.services.access_service import CommandPolicy
from hovorunv2.application.services.cleanup_service import CleanupService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.callbacks import CmdConfigCallback

from .base import BaseCommand

logger = get_logger(__name__)

# Config window TTL in seconds (30 seconds of inactivity)
CONFIG_WINDOW_TTL: Final = 30


class ConfigCommandsCommand(BaseCommand):
    """Handler for /config_cmds command using interactive keyboard."""

    def __init__(self, command_service: CommandService, cleanup_service: CleanupService, settings: Settings) -> None:
        """Initialize command with its dependencies."""
        self._command_service = command_service
        self._cleanup_service = cleanup_service
        self._settings = settings

    @property
    def name(self) -> str:
        """Command name."""
        return "config_cmds"

    @property
    def policy(self) -> CommandPolicy:
        """Group admin only, bypasses whitelist."""
        return CommandPolicy(
            requires_admin=False,
            requires_group_admin=True,
            requires_whitelist=False,
            is_toggleable=False,
        )

    async def is_triggered(self, message: Message) -> bool:
        """Check if message starts with /config_cmds."""
        return bool(message.text and message.text.startswith("/config_cmds"))

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Open command configuration keyboard."""
        chat_id = message.chat.id
        allowed = await self._command_service.get_allowed_commands(chat_id)

        builder = self._build_keyboard(allowed)
        config_msg = await message.answer(
            "🛠 <b>Bot Command Configuration</b>\n\nToggle features for this chat:\n"
            f"<i>This window will expire after {CONFIG_WINDOW_TTL} seconds of inactivity.</i>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )

        await self._cleanup_service.register_message(bot, chat_id, config_msg.message_id, CONFIG_WINDOW_TTL)

    def _build_keyboard(self, allowed: set[str]) -> InlineKeyboardBuilder:
        """Construct the interactive command grid."""
        builder = InlineKeyboardBuilder()
        for cmd in self._command_service.TOGGLEABLE_COMMANDS:
            is_enabled = cmd in allowed
            label = f"{'✅' if is_enabled else '❌'} {cmd.capitalize()}"
            builder.button(text=label, callback_data=CmdConfigCallback(cmd_name=cmd).pack())
        builder.adjust(2)  # 2 buttons per row
        return builder

    async def handle_callback(self, query: Any, callback_data: CmdConfigCallback) -> None:  # noqa: ANN401
        """Process command toggle from callback query."""
        query = cast(CallbackQuery, query)
        if not query.message or not isinstance(query.message, Message):
            return

        chat_id = query.message.chat.id
        cmd_name = callback_data.cmd_name

        # Reset inactivity timer
        await self._cleanup_service.reset_ttl(chat_id, query.message.message_id, CONFIG_WINDOW_TTL)

        allowed = await self._command_service.get_allowed_commands(chat_id)
        if cmd_name in allowed:
            await self._command_service.disable_command(chat_id, cmd_name)
            allowed.remove(cmd_name)
            action = "disabled"
        else:
            await self._command_service.enable_command(chat_id, cmd_name)
            allowed.add(cmd_name)
            action = "enabled"

        logger.info("Command '%s' %s in chat %d by user %d", cmd_name, action, chat_id, query.from_user.id)

        builder = self._build_keyboard(allowed)
        await query.message.edit_reply_markup(reply_markup=builder.as_markup())
        await query.answer(f"Command {cmd_name} {action}.")

    def register_callbacks(self, router: Router, flags: dict[str, Any]) -> None:
        """Register callback handlers with policy flags."""
        router.callback_query.register(self.handle_callback, CmdConfigCallback.filter(), flags=flags)
