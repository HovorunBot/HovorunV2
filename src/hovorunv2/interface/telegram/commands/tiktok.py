"""Module for handling TikTok video links using TikWM API."""

import html
import re
from contextlib import suppress
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import aiohttp
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
    Message,
)

from hovorunv2.infrastructure.container import container
from hovorunv2.infrastructure.logger import get_logger

from .base import BaseCommand, register_command

if TYPE_CHECKING:
    from aiogram import Bot


@dataclass
class MediaItem:
    """Represents a single media item (photo or video) from TikTok."""

    type: str
    url: str


@dataclass
class TikTokData:
    """Consolidated data for a TikTok video."""

    author_name: str
    author_handle: str
    video_title: str
    description: str
    music_info: str
    original_url: str
    views: int = 0
    likes: int = 0
    media: list[MediaItem] = field(default_factory=list)


@register_command
class TikTokCommand(BaseCommand):
    """Command for interacting with TikTok and processing video links."""

    # --- Configuration Constants ---
    API_URL: str = "https://www.tikwm.com/api/"
    API_TIMEOUT_SECONDS: int = 30
    API_SUCCESS_CODE: int = 0

    # Telegram limit for media captions is 1024. We use 1000 for safety margin.
    CAPTION_MAX_LENGTH: int = 1000
    MAX_TEXT_CHUNK_LENGTH: int = 3500

    # Number formatting thresholds
    ONE_BILLION: int = 1_000_000_000
    ONE_MILLION: int = 1_000_000
    ONE_THOUSAND: int = 1_000

    TIKTOK_URL_PATTERN = re.compile(
        r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/(?:v/|t/|(?P<username>[@\w.-]+)/)?(?:video|photo)?/(?P<video_id>\d+)|"
        r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/(?P<short_id>\w+)",
    )

    HEADER_TEMPLATE = (
        '🗣 Shared by <a href="tg://user?id={tg_user_id}">{tg_user_name}</a>\n'
        '🎬 <b>{author_name}</b> (<a href="https://www.tiktok.com/@{author_handle}">@{author_handle}</a>)\n\n'
    )

    CONTENT_TEMPLATE = "<b>{title}</b>\n<i>{description}</i>{music_section}"
    FOOTER_TEMPLATE = '\n\n❤️ {likes} | 👁️ {views}\n🔗 <a href="{original_url}">Original video</a>'

    def __init__(self) -> None:
        """Initialize TikTokCommand and its logger."""
        self.logger = get_logger(__name__)

    async def is_triggered(self, message: Message) -> bool:
        """Check if message contains a valid TikTok link."""
        if not message.text or not message.from_user or message.from_user.is_bot:
            return False
        return bool(re.search(self.TIKTOK_URL_PATTERN, message.text))

    async def handle(self, message: Message, bot: Bot) -> None:
        """Process unique TikTok links found in the message."""
        if not message.text or not message.from_user:
            return

        urls = {link.group(0) for link in re.finditer(self.TIKTOK_URL_PATTERN, message.text)}

        if urls:
            self.logger.info("Processing %d unique TikTok link(s) from user %s", len(urls), message.from_user.id)

        timeout = aiohttp.ClientTimeout(total=self.API_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for url in urls:
                try:
                    tiktok_data = await self._get_tiktok_data(session, url)
                    if tiktok_data:
                        await self._send_telegram_response(bot, message, tiktok_data, session)
                except Exception:
                    self.logger.exception("Failed to process TikTok link %s", url)

    async def _get_tiktok_data(self, session: aiohttp.ClientSession, url: str) -> TikTokData | None:
        """Fetch, parse TikTok data, and apply translations."""
        api_data = await self._fetch_api_data(session, url)
        if not api_data:
            return None

        tiktok = self._parse_api_response(api_data, url)

        if container.translation_service:
            translated = await container.translation_service.translate_if_needed(tiktok.description, session)
            if translated:
                tiktok.description += f"\n\n🇺🇦 <b>Translated:</b>\n{html.escape(translated)}"

        return tiktok

    async def _fetch_api_data(self, session: aiohttp.ClientSession, url: str) -> dict[str, Any] | None:
        """Fetch data from TikWM API."""
        async with session.get(self.API_URL, params={"url": url}) as resp:
            if resp.status != HTTPStatus.OK:
                self.logger.error("Tikwm API returned HTTP %s", resp.status)
                return None
            data = await resp.json()
            if data.get("code") != self.API_SUCCESS_CODE:
                self.logger.error("Tikwm API error: %s", data.get("msg"))
                return None
            return data["data"]

    def _parse_api_response(self, video_data: dict[str, Any], url: str) -> TikTokData:
        """Parse TikWM API response into a TikTokData object."""
        music_dict = video_data.get("music_info", {})
        music_title = music_dict.get("title", "Original Sound")
        music_author = music_dict.get("author", "")
        music_url = music_dict.get("play", "")

        raw_music_text = f"{music_title} - {music_author}" if music_author else music_title
        is_original_sound = "original sound" in music_title.lower() or "оригінальний звук" in music_title.lower()

        music_info = (
            "" if (is_original_sound or not music_url) else f'<a href="{music_url}">{html.escape(raw_music_text)}</a>'
        )

        raw_description = video_data.get("title", "")
        clean_desc = re.sub(r"#\w+", "", raw_description)
        clean_desc = re.sub(r"\s+", " ", clean_desc).strip()

        tiktok = TikTokData(
            author_name=html.escape(video_data.get("author", {}).get("nickname", "Unknown")),
            author_handle=html.escape(video_data.get("author", {}).get("unique_id", "unknown")),
            video_title=html.escape(video_data.get("title", "No Title").split("#")[0].strip()),
            description=html.escape(clean_desc),
            music_info=music_info,
            original_url=url,
            views=video_data.get("play_count", 0),
            likes=video_data.get("digg_count", 0),
        )

        images = video_data.get("images", [])
        if images:
            tiktok.media = [MediaItem(type="photo", url=img_url) for img_url in images]
        elif video_data.get("play"):
            tiktok.media = [MediaItem(type="video", url=video_data.get("play"))]

        return tiktok

    async def _download_media(self, session: aiohttp.ClientSession, media_list: list[MediaItem]) -> list:
        """Download media into RAM."""
        input_media = []
        for idx, m in enumerate(media_list):
            try:
                async with session.get(m.url) as response:
                    if response.status != HTTPStatus.OK:
                        continue
                    content = await response.read()
                    file = BufferedInputFile(content, filename=f"media_{idx}.{'mp4' if m.type == 'video' else 'jpg'}")
                    input_media.append(
                        InputMediaVideo(media=file) if m.type == "video" else InputMediaPhoto(media=file)
                    )
            except Exception:
                self.logger.exception("Failed to download TikTok media to memory (%s)", m.url)
        return input_media

    async def _send_telegram_response(
        self, bot: Bot, message: Message, tiktok: TikTokData, session: aiohttp.ClientSession
    ) -> None:
        """Construct and send the Telegram response."""
        if not message.from_user:
            return

        header = self.HEADER_TEMPLATE.format(
            tg_user_id=message.from_user.id,
            tg_user_name=html.escape(message.from_user.full_name),
            author_name=tiktok.author_name,
            author_handle=tiktok.author_handle,
        )
        footer = self.FOOTER_TEMPLATE.format(
            likes=self._format_number(tiktok.likes),
            views=self._format_number(tiktok.views),
            original_url=html.escape(tiktok.original_url),
        )
        music_section = f"\n\n🎵 {tiktok.music_info}" if tiktok.music_info else ""
        content = self.CONTENT_TEMPLATE.format(
            title=tiktok.video_title, description=tiktok.description, music_section=music_section
        )

        total_text = header + content + footer

        if not tiktok.media:
            await bot.send_message(
                message.chat.id,
                total_text,
                parse_mode="HTML",
                reply_to_message_id=message.message_id,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            return

        if len(total_text) <= self.CAPTION_MAX_LENGTH:
            await self._send_media_group_with_fallback(
                bot, message.chat.id, message.message_id, tiktok.media, total_text, session
            )
        else:
            text_msg = await bot.send_message(
                message.chat.id,
                total_text,
                parse_mode="HTML",
                reply_to_message_id=message.message_id,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            await self._send_media_group_with_fallback(
                bot, message.chat.id, text_msg.message_id, tiktok.media, "📎 <b>Attached media.</b>", session
            )

    async def _send_media_group_with_fallback(
        self,
        bot: Bot,
        chat_id: int,
        reply_to_id: int,
        media_items: list[MediaItem],
        caption: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Send media group using URL, falling back to RAM if necessary."""
        url_media_group = [
            InputMediaVideo(media=m.url) if m.type == "video" else InputMediaPhoto(media=m.url) for m in media_items
        ]
        url_media_group[0].caption = caption
        url_media_group[0].parse_mode = "HTML"

        try:
            await bot.send_media_group(chat_id=chat_id, media=url_media_group, reply_to_message_id=reply_to_id)
        except TelegramBadRequest as e:
            if "WEBPAGE_MEDIA_EMPTY" not in str(e) and "failed to get" not in str(e).lower():
                self.logger.exception("Unexpected Telegram API error during media URL fetch")
                raise
        else:
            return

        placeholder_msg = await bot.send_message(
            chat_id=chat_id,
            text=f"{caption}\n\n⏳ <i>Downloading media...</i>",
            parse_mode="HTML",
            reply_to_message_id=reply_to_id,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

        downloaded_media = await self._download_media(session, media_items)

        if not downloaded_media:
            with suppress(TelegramBadRequest):
                await bot.edit_message_text(
                    text=f"{caption}\n\n⚠️ <i>Failed to load media.</i>",
                    chat_id=chat_id,
                    message_id=placeholder_msg.message_id,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            return

        downloaded_media[0].caption = caption
        downloaded_media[0].parse_mode = "HTML"

        try:
            await bot.send_media_group(chat_id=chat_id, media=downloaded_media, reply_to_message_id=reply_to_id)
            with suppress(TelegramBadRequest):
                await placeholder_msg.delete()
        except Exception:
            self.logger.exception("Failed to upload RAM media to Telegram")
            with suppress(TelegramBadRequest):
                await bot.edit_message_text(
                    text=f"{caption}\n\n⚠️ <i>Failed to upload media.</i>",
                    chat_id=chat_id,
                    message_id=placeholder_msg.message_id,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )

    def _format_number(self, num: int) -> str:
        """Format large numbers into readable text."""
        if num >= self.ONE_BILLION:
            return f"{num / self.ONE_BILLION:.1f}B".replace(".0B", "B")
        if num >= self.ONE_MILLION:
            return f"{num / self.ONE_MILLION:.1f}M".replace(".0M", "M")
        if num >= self.ONE_THOUSAND:
            return f"{num / self.ONE_THOUSAND:.1f}K".replace(".0K", "K")
        return str(num)

    async def _send_chunked_text(
        self, bot: Bot, chat_id: int, reply_to_id: int, content: str, header: str, footer: str
    ) -> list[Message]:
        """Send long text in multiple chunks."""
        chunks = self._chunk_text(content, max_length=self.MAX_TEXT_CHUNK_LENGTH)
        link_preview = LinkPreviewOptions(is_disabled=True)
        sent_messages = []

        first_bot_msg = await bot.send_message(
            chat_id=chat_id,
            text=header + chunks[0] + (footer if len(chunks) == 1 else ""),
            parse_mode="HTML",
            reply_to_message_id=reply_to_id,
            link_preview_options=link_preview,
        )
        sent_messages.append(first_bot_msg)

        last_bot_msg = first_bot_msg
        for i, chunk in enumerate(chunks[1:]):
            is_last_chunk = i == len(chunks) - 2
            last_bot_msg = await bot.send_message(
                chat_id=chat_id,
                text=chunk + (footer if is_last_chunk else ""),
                parse_mode="HTML",
                reply_to_message_id=last_bot_msg.message_id,
                link_preview_options=link_preview,
            )
            sent_messages.append(last_bot_msg)

        return sent_messages

    def _chunk_text(self, text: str, max_length: int) -> list[str]:
        """Split text into chunks that fit within Telegram's message limit."""
        if len(text) <= max_length:
            return [text]

        chunks = []
        while text:
            if len(text) <= max_length:
                chunks.append(text)
                break

            split_at = text.rfind("\n", 0, max_length)
            if split_at == -1:
                split_at = text.rfind(" ", 0, max_length)
            if split_at == -1:
                split_at = max_length

            chunks.append(text[:split_at].strip())
            text = text[split_at:].strip()

        return chunks
