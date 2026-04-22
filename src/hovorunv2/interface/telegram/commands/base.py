"""Base classes and registration utilities for bot commands."""

import html
from abc import ABC, abstractmethod
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import aiohttp
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputMediaPhoto, InputMediaVideo, LinkPreviewOptions, Message

from hovorunv2.infrastructure.container import container
from hovorunv2.infrastructure.logger import get_logger

if TYPE_CHECKING:
    import re

    from aiogram import Bot

logger = get_logger(__name__)


@dataclass
class RichMediaPayload:
    """Standard payload for rich media responses."""

    author_name: str
    author_handle: str
    author_url: str
    content: str
    footer_text: str = ""
    original_url: str = ""
    media_urls: list[str] = field(default_factory=list)
    is_video: bool = False


class BaseCommand(ABC):
    """Abstract base class for all bot commands."""

    @abstractmethod
    async def is_triggered(self, message: Message) -> bool:
        """Check if the command should be triggered by the given message."""

    @abstractmethod
    async def handle(self, message: Message, bot: Bot) -> None:
        """Handle the triggered command."""


class RichMediaCommand(BaseCommand, ABC):
    """Base class for commands that send rich media with standardized formatting."""

    HEADER_TEMPLATE = (
        '🗣 Shared by <a href="tg://user?id={tg_user_id}">{tg_user_name}</a>\n'
        '👤 <b>{author_name}</b> (<a href="{author_url}">@{author_handle}</a>)\n\n'
    )

    FOOTER_TEMPLATE = '<a href="{original_url}">‎</a>'  # Hidden link for preview

    API_TIMEOUT_SECONDS: int = 30

    @property
    @abstractmethod
    def pattern(self) -> re.Pattern:
        """Regex pattern to match links handled by this command."""

    async def is_triggered(self, message: Message) -> bool:
        """Check if message contains links matching the command's pattern."""
        if not message.text or not message.from_user or message.from_user.is_bot:
            return False
        return bool(self.pattern.search(message.text))

    async def handle(self, message: Message, bot: Bot) -> None:
        """Standardized handling of rich media links."""
        if not message.text or not message.from_user:
            return

        matches = list(self.pattern.finditer(message.text))
        if not matches:
            return

        logger.info(
            "Processing %d link(s) for %s from user %s", len(matches), self.__class__.__name__, message.from_user.id
        )

        timeout = aiohttp.ClientTimeout(total=self.API_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for match in matches:
                try:
                    payload = await self._extract_payload(session, match, message.chat.id, "telegram")
                    if payload:
                        await self._send_rich_media(bot, message, payload, session)
                except Exception:
                    logger.exception("Failed to process link: %s", match.group(0))

    @abstractmethod
    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Extract rich media payload from a regex match."""

    async def _send_rich_media(
        self, bot: Bot, message: Message, payload: RichMediaPayload, session: aiohttp.ClientSession
    ) -> None:
        """Execute the standardized media delivery lifecycle."""
        if not message.from_user:
            return

        header = self.HEADER_TEMPLATE.format(
            tg_user_id=message.from_user.id,
            tg_user_name=html.escape(message.from_user.full_name),
            author_name=payload.author_name,
            author_handle=payload.author_handle,
            author_url=payload.author_url,
        )

        total_text = (
            header
            + payload.content
            + payload.footer_text
            + self.FOOTER_TEMPLATE.format(original_url=payload.original_url)
        )

        if not payload.media_urls:
            await message.answer(
                total_text, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
            return

        # 1. Optimistic URL approach
        media_group = self._prepare_media_group(payload, total_text)
        try:
            await bot.send_media_group(
                chat_id=message.chat.id, media=media_group, reply_to_message_id=message.message_id
            )
        except TelegramBadRequest as e:
            if "WEBPAGE_MEDIA_EMPTY" not in str(e) and "failed to get" not in str(e).lower():
                logger.exception("Unexpected Telegram API error during optimistic URL fetch")
                raise
        else:
            return

        # 2. Fallback RAM approach
        placeholder_msg = await message.answer(
            text=total_text + "\n\n⏳ <i>Downloading media...</i>",
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

        if not container.media_service:
            logger.error("MediaService not available in container")
            return

        downloaded_files = await container.media_service.download_batch(
            payload.media_urls,
            prefix="media",
            ext="mp4" if payload.is_video else "jpg",
            session=session,
        )

        if not downloaded_files:
            with suppress(TelegramBadRequest):
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=placeholder_msg.message_id,
                    text=total_text + "\n\n⚠️ <i>Failed to download media.</i>",
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            return

        # 3. Final delivery from RAM
        final_group = []
        for i, file in enumerate(downloaded_files):
            item = InputMediaVideo(media=file) if payload.is_video else InputMediaPhoto(media=file)

            if i == 0:
                item.caption = total_text
                item.parse_mode = "HTML"
            final_group.append(item)

        try:
            await bot.send_media_group(
                chat_id=message.chat.id, media=final_group, reply_to_message_id=message.message_id
            )
            with suppress(TelegramBadRequest):
                await placeholder_msg.delete()
        except Exception:
            logger.exception("Failed to upload media from RAM")
            with suppress(TelegramBadRequest):
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=placeholder_msg.message_id,
                    text=total_text + "\n\n⚠️ <i>Failed to upload media.</i>",
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )

    def _format_number(self, num: int) -> str:
        """Format large numbers into readable text (e.g. 1.2K, 3.4M)."""
        billion_threshold = 1_000_000_000
        million_threshold = 1_000_000
        thousand_threshold = 1_000

        if num >= billion_threshold:
            return f"{num / billion_threshold:.1f}B".replace(".0B", "B")
        if num >= million_threshold:
            return f"{num / million_threshold:.1f}M".replace(".0M", "M")
        if num >= thousand_threshold:
            return f"{num / thousand_threshold:.1f}K".replace(".0K", "K")
        return str(num)

    def _prepare_media_group(self, payload: RichMediaPayload, caption: str) -> list[Any]:
        """Construct InputMedia objects using raw URLs."""
        media_group = []
        for i, url in enumerate(payload.media_urls):
            item = InputMediaVideo(media=url) if payload.is_video else InputMediaPhoto(media=url)

            if i == 0:
                item.caption = caption
                item.parse_mode = "HTML"
            media_group.append(item)
        return media_group


_COMMANDS: dict[str, BaseCommand] = {}


def register_command[TBaseCommand: BaseCommand](command_class: type[TBaseCommand]) -> type[TBaseCommand]:
    """Decorator to register a command class.

    Args:
        command_class: The command class to register.

    Returns:
        The registered command class.
    """
    logger.info("Registering command: %s", command_class.__name__)
    _COMMANDS[command_class.__name__] = command_class()
    return command_class


def get_commands() -> dict[str, BaseCommand]:
    """Return all registered command instances."""
    return _COMMANDS
