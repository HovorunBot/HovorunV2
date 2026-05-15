"""Handler for image recognition command."""

import io
from typing import Any

from aiogram import Bot, Router
from aiogram.types import Message

from hovorunv2.application.data.constants import CommandName
from hovorunv2.application.services.access_service import CommandPolicy
from hovorunv2.application.services.vision_service import VisionService
from hovorunv2.infrastructure.logger import get_logger

from .base import BaseCommand

logger = get_logger(__name__)


class VisionCommand(BaseCommand):
    """Command to recognize and describe images using AI."""

    def __init__(self, vision_service: VisionService) -> None:
        """Initialize command."""
        self._vision_service = vision_service

    @property
    def name(self) -> str:
        """Command name."""
        return CommandName.VISION

    @property
    def policy(self) -> CommandPolicy:
        """Opt-in, hidden, requires approval."""
        return CommandPolicy(
            requires_admin=False,
            requires_group_admin=False,
            requires_approval=True,
            is_toggleable=True,
            auto_enable=False,
        )

    async def is_triggered(self, message: Message) -> bool:
        """Check if message is /vision command (text or caption)."""
        text = message.text or message.caption
        if not text:
            return False

        return text.strip().lower().startswith(f"/{self.name}")

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:  # noqa: ARG002
        """Process image from message or reply."""
        # 1. Find photo in message or reply
        photo = None
        if message.photo:
            photo = message.photo[-1]
        elif message.document and message.document.mime_type and message.document.mime_type.startswith("image/"):
            photo = message.document
        elif message.reply_to_message:
            reply = message.reply_to_message
            if reply.photo:
                photo = reply.photo[-1]
            elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("image/"):
                photo = reply.document

        if not photo:
            await message.reply("📸 Please send an image or reply to one with /vision")
            return

        # 2. Download and process
        status_msg = await message.reply("🤖 <i>Processing...</i>", parse_mode="HTML")
        
        try:
            buffer = io.BytesIO()
            await bot.download(photo.file_id, destination=buffer)
            description = await self._vision_service.describe_image(buffer.getvalue())
            await status_msg.edit_text(description)
        except Exception:
            logger.exception("Vision failed")
            await status_msg.edit_text("❌ Failed to process image.")

    def register(self, router: Router) -> None:
        """Register command with router."""
        router.message.register(self.handle, self.is_triggered)
