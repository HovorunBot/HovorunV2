"""Handler for the /help command and landing page for non-admins."""

from typing import Any, ClassVar

from aiogram import Bot
from aiogram.filters import Command
from aiogram.types import Message

from hovorunv2.application.ports import AppSettings
from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.domain.enums import CommandName
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.handlers.base import BaseCommand

logger = get_logger(__name__)


class HelpCommand(BaseCommand):
    """Handler for the /help command.

    Provides bot information and contact details.
    """

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(requires_admin=False, requires_approval=False, is_toggleable=False)

    def __init__(self, settings: AppSettings, access_service: AccessService) -> None:
        """Initialize help command."""
        self._settings = settings
        self._access = access_service

    @property
    def name(self) -> str:
        """Command name."""
        return CommandName.HELP

    @property
    def trigger(self) -> Command:
        """Trigger filter for help command."""
        return Command(CommandName.HELP, CommandName.START)

    async def is_triggered(self, message: Message) -> bool:
        """Check if message triggers /help or /start."""
        if not message.text:
            return False
        parts = message.text.strip().split()
        if not parts:
            return False
        cmd_part = parts[0].lstrip("/").split("@")[0].lower()
        return cmd_part in (CommandName.HELP, CommandName.START)

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:
        """Handle help command."""
        logger.info("Providing help info to user %s", message.from_user.id if message.from_user else "unknown")
        visible: list[str] = []
        actor_id = message.from_user.id if message.from_user and not message.sender_chat else None
        for definition in self._access.features.registry.all():
            for trigger in definition.triggers:
                decision = await self._access.features.evaluate(
                    definition.key, message.chat.id, actor_id, trigger=trigger
                )
                if decision.visible:
                    visible.append(definition.display_name)
                    break
        text = self.get_help_text()
        if visible:
            text += "\n\n<b>Available Features</b>\n" + "\n".join(visible)
        await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

    def get_help_text(self) -> str:
        """Generate formatted help text."""
        return (
            f"<b>🦉 HovorunV2 — Your Telegram Media Companion</b>\n\n"
            f"{self._settings.project_description}\n\n"
            "I level up your group conversations with smart utilities, dynamic "
            "translations, and seamless media integrations.\n\n"
            "<b>🛡 Access Approval</b>\n"
            "Bot only works in approved chats. Group admins can request access:\n"
            "• /allow_chat — Request bot approval from owners.\n\n"
            "<b>💬 Support & Feedback</b>\n"
            "• /feedback &lt;text&gt; — Send feedback directly to bot developers.\n\n"
            "<b>⚙️ Configuration (Group Admins only)</b>\n"
            "• /settings — Interactive menu for Modules and Languages.\n"
            "• <i>Configuration windows self-destruct after 30s of inactivity.</i>\n\n"
            "<b>Project Links:</b>\n"
            f"• <a href='{self._settings.github_url}'>GitHub Repository</a>\n"
            f"• <a href='{self._settings.issue_tracker_url}'>Issue Tracker</a>\n\n"
            "<b>Contact:</b>\n"
            f"• Email: {self._settings.contact_email}\n\n"
            "<i>I am currently in <b>Alpha</b> stage.</i>"
        )
