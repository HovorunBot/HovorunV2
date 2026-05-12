"""Handler for the /help command and landing page for non-admins."""

from typing import Any, ClassVar

from aiogram import Bot
from aiogram.types import Message

from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.handlers.base import BaseCommand

logger = get_logger(__name__)


class HelpCommand(BaseCommand):
    """Handler for the /help command.

    Provides bot information and contact details.
    """

    BYPASS_WHITELIST: ClassVar[bool] = True

    @property
    def name(self) -> str:
        """Command name."""
        return "help"

    async def is_triggered(self, message: Message) -> bool:
        """Check if message starts with /help or /start."""
        if not message.text:
            return False
        return message.text.strip().startswith(("/help", "/start"))

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
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
            "<b>Project Links:</b>\n"
            f"• <a href='{settings.github_url}'>GitHub Repository</a>\n"
            f"• <a href='{settings.issue_tracker_url}'>Issue Tracker</a>\n\n"
            "<b>Contact:</b>\n"
            f"• Email: {settings.contact_email}\n\n"
            "<i>I am currently in <b>Alpha</b> stage. If I'm added to a group, "
            "make sure it's whitelisted by my administrators!</i>"
        )
