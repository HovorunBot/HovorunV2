"""Base classes and registration utilities for bot commands."""

import html
import re
from abc import ABC, abstractmethod
from contextlib import suppress
from typing import Any, ClassVar, Protocol, runtime_checkable

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramEntityTooLarge
from aiogram.types import InputMediaPhoto, InputMediaVideo, LinkPreviewOptions, Message

from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


@runtime_checkable
class BaseCommand(Protocol):
    """Protocol for all bot commands."""

    @property
    def name(self) -> str:
        """Command name for configuration purposes."""
        ...

    BYPASS_WHITELIST: ClassVar[bool] = False
    AUTO_ALLOW: ClassVar[bool] = False

    async def is_triggered(self, message: Message) -> bool:
        """Check if the command should be triggered by the given message."""
        ...

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:  # noqa: ANN401
        """Handle the triggered command."""
        ...


class RichMediaCommand(ABC):
    """Base class for commands that send rich media with standardized formatting."""

    def __init__(self, media_downloader: MediaDownloader, session: aiohttp.ClientSession) -> None:
        """Initialize command with its dependencies."""
        self._media_downloader = media_downloader
        self._session = session

    @property
    def name(self) -> str:
        """Command name for configuration purposes."""
        name = self.__class__.__name__.lower()
        return name.removesuffix("command")

    BYPASS_WHITELIST: ClassVar[bool] = False
    AUTO_ALLOW: ClassVar[bool] = True

    # Standard templates for all media responses
    HEADER_TEMPLATE: ClassVar[str] = (
        "🗣 <b>{tg_user_name}</b> shared a post by <b>{author_name}</b> "
        '(<a href="{author_url}">@{author_handle}</a>)\n\n'
    )

    QUOTE_TEMPLATE: ClassVar[str] = (
        '<blockquote>🔄 <b>{author_name}</b> (<a href="{author_url}">@{author_handle}</a>)\n{content}</blockquote>\n'
    )

    METRICS_TEMPLATE: ClassVar[str] = "{metrics}\n"

    FOOTER_TEMPLATE: ClassVar[str] = '🔗 <a href="{url}">Open on {platform}</a>'

    API_TIMEOUT_SECONDS: int = 30
    CAPTION_LIMIT: int = 1024

    @property
    @abstractmethod
    def pattern(self) -> re.Pattern:
        """Regex pattern to match links handled by this command."""

    @property
    def platform_name(self) -> str:
        """Human-readable platform name."""
        return self.name.capitalize()

    async def is_triggered(self, message: Message) -> bool:
        """Check if message contains links matching the command's pattern."""
        if not message.text or not message.from_user or message.from_user.is_bot:
            return False
        return bool(self.pattern.search(message.text))

    async def handle(
        self,
        message: Message,
        bot: Bot,
        **kwargs: Any,  # noqa: ARG002,ANN401
    ) -> None:
        """Standardized handling of rich media links."""
        if not message.text or not message.from_user:
            return

        matches = list(self.pattern.finditer(message.text))
        if not matches:
            return

        logger.info(
            "Processing %d link(s) for %s from user %s", len(matches), self.__class__.__name__, message.from_user.id
        )

        for match in matches:
            try:
                payload = await self._extract_payload(self._session, match, message.chat.id, "telegram")
                if payload:
                    await self._send_rich_media(bot, message, payload, self._session)
                else:
                    logger.warning(
                        "Extraction returned empty payload for %s (%s)", match.group(0), self.__class__.__name__
                    )
            except Exception:
                logger.exception("Failed to process link: %s", match.group(0))

    @abstractmethod
    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Extract rich media payload from a regex match."""

    def _build_caption(self, payload: RichMediaPayload, tg_user_name: str) -> str:
        """Build caption from payload using standardized format."""
        header = self.HEADER_TEMPLATE.format(
            tg_user_name=html.escape(tg_user_name),
            author_name=payload.author_name,
            author_handle=payload.author_handle,
            author_url=payload.author_url,
        )

        content = payload.content + "\n\n" if payload.content else ""

        quote = ""
        if payload.quoted_payload:
            quote = self.QUOTE_TEMPLATE.format(
                author_name=payload.quoted_payload.author_name,
                author_handle=payload.quoted_payload.author_handle,
                author_url=payload.quoted_payload.author_url,
                content=payload.quoted_payload.content,
            )

        metrics = ""
        if payload.footer_text and payload.footer_text.strip():
            metrics = self.METRICS_TEMPLATE.format(metrics=payload.footer_text.strip().replace("\n", " "))

        footer = self.FOOTER_TEMPLATE.format(url=payload.original_url, platform=self.platform_name)

        return f"{header}{content}{quote}{metrics}{footer}".strip()

    async def _send_rich_media(
        self, bot: Bot, message: Message, payload: RichMediaPayload, session: aiohttp.ClientSession
    ) -> None:
        """Execute the standardized media delivery lifecycle."""
        if not message.from_user:
            return

        tg_user_name = message.from_user.full_name
        total_text = self._build_caption(payload, tg_user_name)

        if not payload.media_items:
            await message.answer(
                total_text,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            return

        caption = await self._handle_caption_limits(message, payload, total_text, tg_user_name)

        # 1. Optimistic URL approach
        if await self._try_url_delivery(bot, message, payload, caption):
            return

        # 2. Fallback RAM approach
        await self._perform_ram_delivery(bot, message, payload, caption, session)

    async def _handle_caption_limits(
        self, message: Message, payload: RichMediaPayload, total_text: str, tg_user_name: str
    ) -> str:
        """Send long captions separately if needed and return appropriate caption for media."""
        if len(total_text) <= self.CAPTION_LIMIT:
            return total_text

        await message.answer(
            total_text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

        # Short caption for media
        header_text = self.HEADER_TEMPLATE.format(
            tg_user_name=html.escape(tg_user_name),
            author_name=payload.author_name,
            author_handle=payload.author_handle,
            author_url=payload.author_url,
        )
        footer_text = self.FOOTER_TEMPLATE.format(url=payload.original_url, platform=self.platform_name)
        caption = f"{header_text}{footer_text}".strip()

        # If even header too long, just link
        if len(caption) > self.CAPTION_LIMIT:
            return footer_text

        return caption

    async def _try_url_delivery(self, bot: Bot, message: Message, payload: RichMediaPayload, caption: str) -> bool:
        """Attempt delivery using optimistic URL approach."""
        media_group = self._prepare_media_group(payload, caption)
        try:
            await bot.send_media_group(
                chat_id=message.chat.id, media=media_group, reply_to_message_id=message.message_id
            )
        except TelegramEntityTooLarge:
            logger.warning("Media too large even for URL delivery")
            await message.answer(
                text=caption + "\n\n⚠️ <i>Media is too large, view on original resource.</i>",
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            return True
        except TelegramBadRequest as e:
            err_msg = str(e).upper()
            if not any(item in err_msg for item in ("WEBPAGE_CURL_FAILED", "FAILED TO GET")):
                logger.exception("Unexpected Telegram API error during optimistic URL fetch")
                raise
        else:
            return True
        return False

    async def _perform_ram_delivery(
        self, bot: Bot, message: Message, payload: RichMediaPayload, caption: str, session: aiohttp.ClientSession
    ) -> None:
        """Perform fallback delivery by downloading media to RAM first."""
        placeholder_msg = await message.answer(
            text=caption + "\n\n⏳ <i>Downloading media...</i>",
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

        downloaded_files = await self._media_downloader.download_batch(
            payload.media_items,
            prefix="media",
            session=session,
        )

        final_group = []
        for i, file in enumerate(downloaded_files):
            if file is None:
                continue

            item_meta = payload.media_items[i]
            item = InputMediaVideo(media=file) if item_meta.is_video else InputMediaPhoto(media=file)
            if not final_group:
                item.caption = caption
                item.parse_mode = "HTML"
            final_group.append(item)

        if not final_group:
            await self._edit_placeholder_error(bot, message.chat.id, placeholder_msg.message_id, caption, "download")
            return

        try:
            await bot.send_media_group(
                chat_id=message.chat.id, media=final_group, reply_to_message_id=message.message_id
            )
            with suppress(TelegramBadRequest):
                await placeholder_msg.delete()
        except TelegramEntityTooLarge:
            logger.warning("Media too large for Telegram upload")
            await self._edit_placeholder_error(bot, message.chat.id, placeholder_msg.message_id, caption, "too_large")
        except Exception:
            logger.exception("Failed to upload media from RAM")
            await self._edit_placeholder_error(bot, message.chat.id, placeholder_msg.message_id, caption, "upload")

    async def _edit_placeholder_error(
        self, bot: Bot, chat_id: int, message_id: int, caption: str, error_type: str
    ) -> None:
        """Update placeholder message with specific error text."""
        error_texts = {
            "download": "⚠️ <i>Failed to download media.</i>",
            "too_large": "⚠️ <i>Media is too large, view on original resource.</i>",
            "upload": "⚠️ <i>Failed to upload media.</i>",
        }
        text = f"{caption}\n\n{error_texts.get(error_type, error_texts['upload'])}"
        with suppress(TelegramBadRequest):
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )

    def _prepare_media_group(self, payload: RichMediaPayload, caption: str) -> list[Any]:
        """Construct InputMedia objects using raw URLs."""
        media_group = []
        for i, item_meta in enumerate(payload.media_items):
            item = InputMediaVideo(media=item_meta.url) if item_meta.is_video else InputMediaPhoto(media=item_meta.url)

            if i == 0:
                item.caption = caption
                item.parse_mode = "HTML"
            media_group.append(item)
        return media_group
