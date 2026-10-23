"""Base classes and unified rich media command handler."""

import html
import re
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

import aiohttp
from aiogram import Bot, F
from aiogram.exceptions import TelegramBadRequest, TelegramEntityTooLarge
from aiogram.filters import Filter
from aiogram.types import BufferedInputFile, InputMediaPhoto, InputMediaVideo, LinkPreviewOptions, MediaUnion, Message
from aiogram.utils.magic_filter import MagicFilter

from hovorunv2.application.clients.base import BaseMediaClient
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.application.media.extractor import MediaExtractor
from hovorunv2.application.services.access_service import CommandPolicy
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.domain.enums import PROVIDER_SPLIT_COMMANDS, CommandName
from hovorunv2.infrastructure.logger import get_logger

if TYPE_CHECKING:
    from aiogram import Router
logger = get_logger(__name__)


@runtime_checkable
class BaseCommand(Protocol):
    """Protocol for all bot commands."""

    @property
    def name(self) -> str:
        """Command name for configuration purposes."""
        ...

    @property
    def policy(self) -> CommandPolicy:
        """Access policy for this command."""
        ...

    @property
    def trigger(self) -> Filter | MagicFilter | Callable[..., Any]:
        """Trigger filter for this command."""
        ...

    async def is_triggered(self, message: Message) -> bool:
        """Check if the command should be triggered by the given message."""
        ...

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:
        """Handle the triggered command."""
        ...

    def register_callbacks(self, router: Router, flags: dict[str, Any]) -> None:
        """Register any callback query handlers associated with this command."""
        return


class RichMediaCommand:
    """Unified command handler for all rich media providers."""

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

    def __init__(
        self,
        primary_client: BaseMediaClient,
        media_downloader: MediaDownloader,
        media_extractor: MediaExtractor,
        session: aiohttp.ClientSession,
        command_service: CommandService,
    ) -> None:
        """Initialize command with its provider client and shared media services."""
        self._primary_client = primary_client
        self._media_downloader = media_downloader
        self._media_extractor = media_extractor
        self._session = session
        self._command_service = command_service

    @property
    def name(self) -> str:
        """Command name derived from provider client."""
        return str(self._primary_client.name)

    @property
    def video_command_name(self) -> str:
        """Sub-command name for video capture configuration."""
        cmd_enum = CommandName(self.name)
        return str(PROVIDER_SPLIT_COMMANDS[cmd_enum][0])

    @property
    def post_command_name(self) -> str:
        """Sub-command name for image/text post capture configuration."""
        cmd_enum = CommandName(self.name)
        return str(PROVIDER_SPLIT_COMMANDS[cmd_enum][1])

    @property
    def policy(self) -> CommandPolicy:
        """Media commands require access and are toggleable by default."""
        return CommandPolicy(requires_admin=False, requires_approval=True, is_toggleable=True, auto_enable=True)

    @property
    def pattern(self) -> re.Pattern[str]:
        """Regex pattern to match links handled by this provider."""
        return self._primary_client.pattern

    @property
    def platform_name(self) -> str:
        """Human-readable platform name."""
        return self._primary_client.platform_label

    @property
    def trigger(self) -> MagicFilter:
        """Trigger filter for rich media links."""
        return F.text.regexp(self.pattern) & ~F.from_user.is_bot

    async def is_triggered(self, message: Message) -> bool:
        """Check if message contains links matching the command's pattern."""
        if not message.text or not message.from_user or message.from_user.is_bot:
            return False
        return bool(self.pattern.search(message.text))

    async def handle(
        self,
        message: Message,
        bot: Bot,
        **kwargs: Any,
    ) -> None:
        """Standardized handling of rich media links."""
        if not message.text or not message.from_user:
            return

        matches = list(self.pattern.finditer(message.text))
        if not matches:
            return

        logger.info("Processing %d link(s) for %s from user %s", len(matches), self.name, message.from_user.id)

        for match in matches:
            try:
                payload = await self._extract_payload(self._session, match, message.chat.id, "telegram")
                if not payload:
                    logger.warning("Extraction returned empty payload for %s (%s)", match.group(0), self.name)
                    continue

                if not await self._is_payload_mode_allowed(message.chat.id, payload):
                    logger.info(
                        "Skipping %s link %s in chat %d (capture mode disabled for is_video=%s)",
                        self.name,
                        match.group(0),
                        message.chat.id,
                        payload.is_video,
                    )
                    continue

                await self._send_rich_media(bot, message, payload, self._session)
            except Exception:
                logger.exception("Failed to process link: %s", match.group(0))

    async def _is_payload_mode_allowed(self, chat_id: int, payload: RichMediaPayload) -> bool:
        """Check whether the extracted payload's media mode (video vs image/text) is enabled."""
        allowed_commands = await self._command_service.get_allowed_commands(chat_id)
        if not allowed_commands:
            return True

        target_cmd = self.video_command_name if payload.is_video else self.post_command_name
        return await self._command_service.is_command_allowed(chat_id, target_cmd)

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match[str], chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Generalized 4-tier extraction pipeline: Direct API -> yt-dlp -> Browser Fallback -> Metadata."""
        url = match.group(0).rstrip(".,")

        # 1. Direct API (if implemented by provider)
        if payload := await self._primary_client.extract_payload(session, url, chat_id, platform):
            return payload

        # 2. Generic MediaExtractor (yt-dlp) - skip for explicit post/text URLs
        is_post_url = bool(
            self._primary_client.config.post_url_pattern and self._primary_client.config.post_url_pattern.search(url)
        )
        if not is_post_url:
            logger.info("%s direct API returned None for %s, trying yt-dlp", self.name, url)
            if payload := await self._media_extractor.extract_payload(
                session, url, chat_id, platform, cookies=self._primary_client.get_ytdlp_cookies()
            ):
                return payload

        # 3. Browser Fallback (if configured on provider)
        if payload := await self._primary_client.extract_fallback(url, chat_id, platform, session):
            return payload

        # 4. Metadata / OpenGraph fallback (if configured on provider)
        return await self._primary_client.extract_metadata_payload(session, url, chat_id, platform)

    def register_callbacks(self, router: Router, flags: dict[str, Any]) -> None:
        """Rich media commands do not have callbacks by default."""

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

    def _build_input_media(
        self, media: str | BufferedInputFile, *, is_video: bool, caption: str | None
    ) -> InputMediaVideo | InputMediaPhoto:
        """Create InputMediaVideo or InputMediaPhoto with optional HTML caption."""
        parse_mode = "HTML" if caption else None
        if is_video:
            return InputMediaVideo(media=media, caption=caption, parse_mode=parse_mode)
        return InputMediaPhoto(media=media, caption=caption, parse_mode=parse_mode)

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

        # 0. Pre-downloaded bytes (e.g., from headless browser session)
        if payload.downloaded_bytes:
            await self._send_predownloaded_bytes(bot, message, payload, caption)
            return

        # 1. Optimistic URL approach
        if await self._try_url_delivery(bot, message, payload, caption):
            return

        # 2. Fallback RAM approach
        await self._perform_ram_delivery(bot, message, payload, caption, session)

    async def _send_predownloaded_bytes(
        self, bot: Bot, message: Message, payload: RichMediaPayload, caption: str
    ) -> None:
        """Send media items using pre-downloaded byte buffers."""
        if not payload.downloaded_bytes:
            return

        final_group: list[Any] = []
        for i, content in enumerate(payload.downloaded_bytes):
            if i >= len(payload.media_items):
                break
            item_meta = payload.media_items[i]
            ext = "mp4" if item_meta.is_video else "jpg"
            file = BufferedInputFile(content, filename=f"browser_{i}.{ext}")
            final_group.append(
                self._build_input_media(file, is_video=item_meta.is_video, caption=caption if not final_group else None)
            )

        if final_group:
            await bot.send_media_group(
                chat_id=message.chat.id,
                media=final_group,
                reply_to_message_id=message.message_id,
            )

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

        header_text = self.HEADER_TEMPLATE.format(
            tg_user_name=html.escape(tg_user_name),
            author_name=payload.author_name,
            author_handle=payload.author_handle,
            author_url=payload.author_url,
        )
        footer_text = self.FOOTER_TEMPLATE.format(url=payload.original_url, platform=self.platform_name)
        caption = f"{header_text}{footer_text}".strip()

        return footer_text if len(caption) > self.CAPTION_LIMIT else caption

    async def _try_url_delivery(self, bot: Bot, message: Message, payload: RichMediaPayload, caption: str) -> bool:
        """Attempt delivery using optimistic URL approach."""
        media_group: list[MediaUnion] = [
            self._build_input_media(item.url, is_video=item.is_video, caption=caption if i == 0 else None)
            for i, item in enumerate(payload.media_items)
        ]
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

        final_group: list[Any] = [
            self._build_input_media(
                file, is_video=payload.media_items[i].is_video, caption=caption if idx == 0 else None
            )
            for idx, (i, file) in enumerate((i, f) for i, f in enumerate(downloaded_files) if f is not None)
        ]

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
