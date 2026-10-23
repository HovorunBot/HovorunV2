"""Application service for extracting media using yt-dlp."""

import asyncio
import html
import logging
import re
from typing import Any

import aiohttp
import yt_dlp
from yt_dlp.utils import DownloadError

from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.services.translation_service import TranslationService
from hovorunv2.application.text import clean_caption_text, format_number

logger = logging.getLogger(__name__)


class MediaExtractor:
    """Service to extract media info from various platforms using yt-dlp."""

    MIN_DESCRIPTION_LENGTH: int = 10

    # Patterns for supported platforms
    YT_SHORTS_PATTERN = re.compile(r"https?://(?:www\.)?youtube\.com/shorts/(?P<id>[\w-]+)")

    def __init__(self, translation_service: TranslationService) -> None:
        """Initialize with required translation service."""
        self._translation_service = translation_service

    async def extract_payload(
        self,
        session: aiohttp.ClientSession,
        url: str,
        chat_id: int,
        platform: str,
        cookies: dict[str, str] | None = None,
    ) -> RichMediaPayload | None:
        """Extract media payload from URL."""
        try:
            # yt-dlp is blocking, run in thread to keep event loop free
            info = await asyncio.to_thread(self._extract_info, url, cookies)
            if not info:
                logger.error("yt-dlp failed to extract info for %s (returned None)", url)
                return None
            return await self._process_info(info, session, url, chat_id, platform)
        except DownloadError as exc:
            logger.warning("yt-dlp failed to extract media from %s: %s", url, exc)
            return None
        except Exception:
            logger.exception("Failed to extract media from %s using yt-dlp", url)
            return None

    def _extract_info(self, url: str, cookies: dict[str, str] | None = None) -> dict[str, Any] | None:
        """Sync extraction using yt-dlp."""
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        }

        if cookies:
            logger.info("Providing %d cookies to yt-dlp", len(cookies))

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)

    async def _process_info(
        self, info: dict[str, Any], session: aiohttp.ClientSession, url: str, chat_id: int, platform: str
    ) -> RichMediaPayload:
        """Process yt-dlp info into RichMediaPayload."""
        title = info.get("title", "No Title")
        description = info.get("description") or ""
        author_name = info.get("uploader") or info.get("channel") or "Unknown"
        author_handle = info.get("uploader_id") or "unknown"
        author_url = info.get("uploader_url") or ""

        clean_desc = clean_caption_text(description, strip_hashtags=True)
        display_text = clean_desc if len(clean_desc) > self.MIN_DESCRIPTION_LENGTH else title
        content = await self._translation_service.escape_and_translate(display_text, chat_id, platform, session)

        media_items = self._extract_media_items(info)
        logger.info("Extracted %d media item(s) for %s", len(media_items), url)
        if media_items:
            logger.debug("Media URL: %s", media_items[0].url)

        likes = info.get("like_count") or 0
        views = info.get("view_count") or 0

        footer = ""
        if likes or views:
            stats = []
            if likes:
                stats.append(f"❤️ {format_number(likes)}")
            if views:
                stats.append(f"👁️ {format_number(views)}")
            footer = " | ".join(stats)

        return RichMediaPayload(
            author_name=html.escape(author_name),
            author_handle=html.escape(author_handle),
            author_url=author_url,
            content=content,
            footer_text=footer,
            original_url=url,
            media_items=media_items,
        )

    def _extract_media_items(self, info: dict[str, Any]) -> list[MediaItem]:
        """Extract media items from yt-dlp info dict."""
        if entries := info.get("entries"):
            items: list[MediaItem] = []
            for entry in entries:
                if isinstance(entry, dict):
                    items.extend(self._extract_media_items(entry))
            if items:
                return items

        media_url = info.get("url")
        is_video = info.get("vcodec") != "none"

        if not media_url and "formats" in info:
            suitable_formats = [
                f
                for f in info["formats"]
                if f.get("vcodec") != "none" and f.get("acodec") != "none" and f.get("ext") == "mp4"
            ]
            if suitable_formats:
                suitable_formats.sort(key=lambda x: x.get("height") or 0, reverse=True)
                media_url = suitable_formats[0].get("url")
                is_video = True
            elif info["formats"]:
                last_format = info["formats"][-1]
                media_url = last_format.get("url")
                is_video = last_format.get("vcodec") != "none"

        if media_url:
            return [MediaItem(url=str(media_url), is_video=is_video)]
        return []
