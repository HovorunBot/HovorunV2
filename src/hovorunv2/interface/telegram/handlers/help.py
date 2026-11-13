"""Handler for the /help command and landing page for non-admins."""

from html import escape
from typing import ClassVar

from aiogram import Bot
from aiogram.filters import Command
from aiogram.types import Message

from hovorunv2.application.access.access_service import AccessService, CommandPolicy
from hovorunv2.application.configuration import AppSettings
from hovorunv2.domain.enums import CommandName
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.handlers.base import BaseCommand
from hovorunv2.interface.telegram.i18n import get_message

logger = get_logger(__name__)


class HelpCommand(BaseCommand):
    """Handle the /help command.

    Provide bot information and contact details.
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
        """Return whether the message targets this command."""
        return self._is_slash_command_triggered(message, CommandName.HELP, CommandName.START)

    async def handle(self, message: Message, bot: Bot, **kwargs: object) -> None:
        """Handle help command.

        Args:
            message: Telegram message triggering the command.
            bot: Telegram bot bound to the handler.
            **kwargs: Additional handler context, unused.
        """
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
            features_header = get_message("help-available-features")
            text += f"\n\n<b>{features_header}</b>\n" + "\n".join(visible)
        await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

    def get_help_text(self, locale: str | None = None) -> str:
        """Generate formatted help text in the requested or active locale.

        Args:
            locale: Optional explicit locale code ('uk' or 'en').

        Returns:
            HTML help text with project and contact information.
        """
        return get_message(
            "help-full-text",
            locale,
            description=escape(self._settings.project_description, quote=True),
            github_url=escape(str(self._settings.github_url), quote=True),
            issue_tracker_url=escape(str(self._settings.issue_tracker_url), quote=True),
            contact_email=escape(self._settings.contact_email, quote=True),
        )
