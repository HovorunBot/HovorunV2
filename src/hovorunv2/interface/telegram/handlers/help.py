"""Handler for the /help command and landing page for non-admins."""

from typing import Any

from aiogram import Bot
from aiogram.types import Message

from hovorunv2.application.services.access_service import CommandPolicy
from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.handlers.base import BaseCommand

logger = get_logger(__name__)


class HelpCommand(BaseCommand):
    """Handler for the /help command.

    Provides bot information and contact details.
    """

    @property
    def name(self) -> str:
        """Command name."""
        return "help"

    @property
    def policy(self) -> CommandPolicy:
        """Help is always available to everyone."""
        return CommandPolicy(requires_admin=False, requires_whitelist=False, is_toggleable=False)

    async def is_triggered(self, message: Message) -> bool:
        """Check if message starts with /help or /start."""
        if not message.text:
            return False
        return message.text.strip().startswith(("/help", "/start"))

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:  # noqa: ARG002
        """Handle help command."""
        logger.info("Providing help info to user %s", message.from_user.id if message.from_user else "unknown")
        await message.answer(self.get_help_text(), parse_mode="HTML", disable_web_page_preview=True)

    def get_help_text(self) -> str:
        """Generate formatted help text."""
        return (
            f"<b>🦉 HovorunV2 — Your Telegram Media Companion</b>\n\n"
            f"{settings.project_description}\n\n"
            "I level up your group conversations with smart utilities, dynamic "
            "translations, and seamless media integrations.\n\n"
            "<b>🛡 Whitelisting</b>\n"
            "Bot only works in whitelisted chats. Group admins can request access:\n"
            "• /allow_chat — Request bot approval from owners.\n\n"
            "<b>⚙️ Configuration (Group Admins only)</b>\n"
            "• /config_cmds — Interactive feature toggle.\n"
            "• /config_lang — Interactive translation setup.\n"
            "• /set_lang &lt;code&gt; [ignores] — Manual language setup.\n\n"
            "<b>🚀 Supported Platforms</b>\n"
            "Send links from: Twitter/X, Instagram, TikTok, Threads, Bluesky, Facebook, YouTube Shorts.\n\n"
            "<b>Project Links:</b>\n"
            f"• <a href='{settings.github_url}'>GitHub Repository</a>\n"
            f"• <a href='{settings.issue_tracker_url}'>Issue Tracker</a>\n\n"
            "<b>Contact:</b>\n"
            f"• Email: {settings.contact_email}\n\n"
            "<i>I am currently in <b>Alpha</b> stage.</i>"
        )
