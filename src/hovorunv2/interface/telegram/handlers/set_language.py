"""Command to configure translation settings for a chat."""

import re
import typing
from typing import Any, Final, cast

from aiogram import Bot, Router
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.application.services.access_service import CommandPolicy
from hovorunv2.application.services.cleanup_service import CleanupService
from hovorunv2.application.services.language_service import LanguageService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.callbacks import LangConfigCallback

from .base import BaseCommand

logger = get_logger(__name__)

# Config window TTL in seconds (30 seconds of inactivity)
CONFIG_WINDOW_TTL: Final = 30


class SetLanguageCommand(BaseCommand):
    """Command for admins to set translation target and ignored languages."""

    def __init__(self, language_service: LanguageService, cleanup_service: CleanupService, settings: Settings) -> None:
        """Initialize command with its dependencies."""
        self._language_service = language_service
        self._cleanup_service = cleanup_service
        self._settings = settings

    @property
    def name(self) -> str:
        """Command name."""
        return "config_lang"

    @property
    def policy(self) -> CommandPolicy:
        """Group admin only, bypasses whitelist."""
        return CommandPolicy(
            requires_admin=False,
            requires_group_admin=True,
            requires_whitelist=False,
            is_toggleable=False,
        )

    # Manual command pattern: /set_lang <target_lang> [ignored_langs_comma_separated]
    SET_LANG_PATTERN = re.compile(r"^/set_lang\s+(?P<target>\w+)(?:\s+(?P<ignored>[\w,]+))?$")

    # Common languages for the interactive picker
    COMMON_LANGUAGES: typing.ClassVar[dict[str, str]] = {
        "uk": "🇺🇦 Ukrainian",
        "en": "🇺🇸 English",
        "pl": "🇵🇱 Polish",
        "de": "🇩🇪 German",
        "fr": "🇫🇷 French",
        "es": "🇪🇸 Spanish",
        "it": "🇮🇹 Italian",
        "tr": "🇹🇷 Turkish",
        "ja": "🇯🇵 Japanese",
        "zh": "🇨🇳 Chinese",
    }

    async def is_triggered(self, message: Message) -> bool:
        """Check if message is /config_lang or /set_lang."""
        if not message.text or not message.from_user or message.from_user.is_bot:
            return False
        text = message.text.strip()
        return text.startswith(("/config_lang", "/set_lang"))

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:  # noqa: ARG002
        """Handle the configuration command."""
        if not message.text:
            return

        text = message.text.strip()
        if text.startswith("/set_lang"):
            await self._handle_manual(message)
            return

        await self._show_config_keyboard(message, bot)

    async def _handle_manual(self, message: Message) -> None:
        """Handle manual /set_lang text command."""
        if not message.text:
            return

        match = self.SET_LANG_PATTERN.match(message.text.strip())
        if not match:
            await message.answer(
                "❌ Invalid format. Use: <code>/set_lang <target_lang> [ignored_langs_comma_separated]</code>\n"
                "Example: <code>/set_lang uk en,ru,es</code>\n"
                "Or use <code>/config_lang</code> for interactive setup.",
                parse_mode="HTML",
            )
            return

        target = match.group("target")
        ignored_raw = match.group("ignored")
        ignored_list = [lang.strip() for lang in ignored_raw.split(",")] if ignored_raw else []

        if "und" not in ignored_list:
            ignored_list.append("und")

        await self._language_service.update_settings(
            chat_id=message.chat.id, target_lang=target, ignored_langs=ignored_list, platform="telegram"
        )

        await message.answer(
            f"✅ Translation settings updated!\n"
            f"🎯 <b>Target:</b> {target}\n"
            f"🚫 <b>Ignored:</b> {', '.join(ignored_list)}",
            parse_mode="HTML",
        )

    async def _show_config_keyboard(self, message: Message, bot: Bot) -> None:
        """Show interactive language configuration keyboard."""
        chat_id = message.chat.id
        target, ignored = await self._language_service.get_chat_settings(chat_id)

        builder = self._build_keyboard(target, set(ignored))
        config_msg = await message.answer(
            "🌐 <b>Language Configuration</b>\n\n"
            "Select target language (🎯) or toggle ignored languages (🚫).\n"
            "<i>Ignored languages won't be translated.</i>\n\n"
            f"<i>This window will expire after {CONFIG_WINDOW_TTL} seconds of inactivity.</i>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )

        await self._cleanup_service.register_message(bot, chat_id, config_msg.message_id, CONFIG_WINDOW_TTL)

    def _build_keyboard(self, target: str, ignored: set[str]) -> InlineKeyboardBuilder:
        """Build the language selection grid."""
        builder = InlineKeyboardBuilder()

        # Add buttons for each common language
        for code, name in self.COMMON_LANGUAGES.items():
            # Target button
            target_label = f"{'🎯' if target == code else '▫️'} {name}"
            builder.button(text=target_label, callback_data=LangConfigCallback(action="target", lang_code=code).pack())
            # Ignore button
            ignore_label = f"{'🚫' if code in ignored else '✅'}"
            builder.button(text=ignore_label, callback_data=LangConfigCallback(action="ignore", lang_code=code).pack())

        builder.adjust(2)  # Name+Target on left, Toggle Ignore on right
        return builder

    async def handle_callback(self, query: Any, callback_data: LangConfigCallback) -> None:
        """Process language configuration changes."""
        query = cast(CallbackQuery, query)
        if not query.message or not isinstance(query.message, Message):
            return

        chat_id = query.message.chat.id
        target, ignored_list = await self._language_service.get_chat_settings(chat_id)
        ignored = set(ignored_list)

        # Reset inactivity timer
        await self._cleanup_service.reset_ttl(chat_id, query.message.message_id, CONFIG_WINDOW_TTL)

        if callback_data.action == "target":
            target = callback_data.lang_code
        elif callback_data.action == "ignore":
            code = callback_data.lang_code
            if code in ignored:
                ignored.remove(code)
            else:
                ignored.add(code)

        # Ensure 'und' is always ignored
        ignored.add("und")

        await self._language_service.update_settings(
            chat_id=chat_id, target_lang=target, ignored_langs=list(ignored), platform="telegram"
        )

        logger.info(
            "Language settings updated in chat %d by user %d: target=%s, ignored=%s",
            chat_id,
            query.from_user.id,
            target,
            list(ignored),
        )

        builder = self._build_keyboard(target, ignored)
        await query.message.edit_reply_markup(reply_markup=builder.as_markup())

        status = "target" if callback_data.action == "target" else "ignored state"
        await query.answer(f"Updated: {callback_data.lang_code} {status}")

    def register_callbacks(self, router: Router, flags: dict[str, Any]) -> None:
        """Register callback handlers with policy flags."""
        router.callback_query.register(self.handle_callback, LangConfigCallback.filter(), flags=flags)
