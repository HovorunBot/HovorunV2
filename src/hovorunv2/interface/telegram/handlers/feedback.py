"""Handler for the /feedback command."""

from typing import Any, ClassVar

from aiogram import Bot
from aiogram.filters import Command
from aiogram.types import Message

from hovorunv2.application.access.access_service import CommandPolicy
from hovorunv2.application.services.feedback_service import FeedbackService
from hovorunv2.domain.enums import CommandName
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.handlers.base import BaseCommand

logger = get_logger(__name__)


class FeedbackCommand(BaseCommand):
    """Handler for the /feedback command.

    Delegates user feedback submission to FeedbackService.
    """

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(requires_admin=False, requires_approval=False, is_toggleable=False)

    def __init__(self, feedback_service: FeedbackService) -> None:
        """Initialize command with its application service."""
        self._feedback_service = feedback_service

    @property
    def name(self) -> str:
        """Command name."""
        return CommandName.FEEDBACK

    @property
    def trigger(self) -> Command:
        """Trigger filter for feedback command."""
        return Command(CommandName.FEEDBACK)

    async def is_triggered(self, message: Message) -> bool:
        """Check if message starts with /feedback."""
        if not message.text:
            return False
        parts = message.text.strip().split()
        if not parts:
            return False
        cmd_part = parts[0].lstrip("/").split("@")[0].lower()
        return cmd_part == CommandName.FEEDBACK

    def _extract_feedback_content(self, message: Message) -> str:
        """Extract feedback body from command message."""
        text = (message.text or "").strip()
        parts = text.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    async def _send_instruction(self, message: Message) -> None:
        """Reply with feedback command usage guidance."""
        await message.reply(
            "💬 <b>How to send feedback:</b>\n\n"
            "Please provide your message after <code>/feedback</code>:\n"
            "<code>/feedback Your suggestion, bug report, or question here</code>",
            parse_mode="HTML",
        )

    async def _send_delivery_status(self, message: Message, *, delivered: bool) -> None:
        """Reply with feedback delivery outcome."""
        if delivered:
            await message.reply(
                "✅ <b>Thank you!</b> Your feedback has been sent directly to the development team.",
                parse_mode="HTML",
            )
            return
        await message.reply(
            "⚠️ <b>Notice:</b> Developers are currently unreachable. Please try again later or reach out via GitHub.",
            parse_mode="HTML",
        )

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:
        """Process user feedback via FeedbackService."""
        feedback_content = self._extract_feedback_content(message)
        if not feedback_content:
            await self._send_instruction(message)
            return

        user = message.from_user
        user_name = user.full_name if user else "Anonymous"
        user_id = user.id if user else 0
        username = user.username if user else None
        chat = message.chat
        chat_title = chat.title or "Private"

        delivered = await self._feedback_service.submit_feedback(
            bot,
            user_id=user_id,
            user_name=user_name,
            username=username,
            chat_id=chat.id,
            chat_title=chat_title,
            chat_type=chat.type,
            message_text=feedback_content,
        )
        await self._send_delivery_status(message, delivered=delivered)
