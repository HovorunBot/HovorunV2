"""Command to configure translation settings for a chat."""

import re
from typing import Any

from aiogram import Bot
from aiogram.types import Message

from hovorunv2.application.services.language_service import LanguageService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.logger import get_logger

from .base import BaseCommand

logger = get_logger(__name__)


class SetLanguageCommand(BaseCommand):
    """Command for admins to set translation target and ignored languages."""

    def __init__(self, language_service: LanguageService, settings: Settings) -> None:
        """Initialize command with its dependencies."""
        self._language_service = language_service
        self._settings = settings

    @property
    def name(self) -> str:
        """Command name."""
        return "set_lang"

    # Pattern: /set_lang <target_lang> [ignored_langs_comma_separated]
    # Example: /set_lang uk en,ru,es
    SET_LANG_PATTERN = re.compile(r"^/set_lang\s+(?P<target>\w+)(?:\s+(?P<ignored>[\w,]+))?$")

    async def is_triggered(self, message: Message) -> bool:
        """Check if message is a /set_lang command."""
        if not message.text or not message.from_user or message.from_user.is_bot:
            return False
        return message.text.strip().startswith("/set_lang")

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Handle the set_language command."""
        user_id = message.from_user.id if message.from_user else None
        if user_id not in self._settings.admin_ids:
            logger.warning("Unauthorized /set_lang attempt by user %s", user_id)
            return

        assert message.text is not None, "Must be provided"
        match = self.SET_LANG_PATTERN.match(message.text.strip())
        if not match:
            await message.answer(
                "❌ Invalid format. Use: <code>/set_lang <target_lang> [ignored_langs_comma_separated]</code>\n"
                "Example: <code>/set_lang uk en,ru,es</code>",
                parse_mode="HTML",
            )
            return

        target = match.group("target")
        ignored_raw = match.group("ignored")
        ignored_list = [lang.strip() for lang in ignored_raw.split(",")] if ignored_raw else []

        # Always include mandatory ignores
        if "und" not in ignored_list:
            ignored_list.append("und")

        chat_id = message.chat.id
        platform = "telegram"

        # Update chat settings in database
        await self._language_service.update_settings(
            chat_id=chat_id, target_lang=target, ignored_langs=ignored_list, platform=platform
        )

        logger.info("Translation settings updated for chat %d: target=%s, ignored=%s", chat_id, target, ignored_list)
        await message.answer(
            f"✅ Translation settings updated!\n"
            f"🎯 <b>Target:</b> {target}\n"
            f"🚫 <b>Ignored:</b> {', '.join(ignored_list)}",
            parse_mode="HTML",
        )
