"""Shared yt-dlp extraction and pure parsing for async bot and synchronous workers."""

import asyncio
import logging
import math
import re
from http.cookiejar import Cookie
from urllib.parse import urlsplit

import yt_dlp

from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.media.backend_logging import MediaBackendLogger
from hovorunv2.application.text import clean_caption_text, format_number

logger = logging.getLogger(__name__)
MIN_DESCRIPTION_LENGTH = 10
EXTRACTION_TIMEOUT_SECONDS = 30
MAX_PLAYLIST_ITEMS = 100


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _text(info: dict[str, object], key: str, default: str = "") -> str:
    value = info.get(key)
    return value if isinstance(value, str) else default


def _count(info: dict[str, object], key: str) -> int:
    value = info.get(key)
    return int(value) if isinstance(value, int | float) and math.isfinite(value) and value > 0 else 0


def _format_height(value: dict[str, object]) -> int:
    return _count(value, "height")


def _media_items(info: dict[str, object]) -> list[MediaItem]:
    entries = info.get("entries")
    if isinstance(entries, list | tuple):
        items = [item for entry in entries[:MAX_PLAYLIST_ITEMS] for item in _media_items(_mapping(entry))]
        if items:
            return items
    media_url = _text(info, "url")
    is_video = info.get("vcodec") != "none"
    raw_formats = info.get("formats")
    if not media_url and isinstance(raw_formats, list | tuple):
        formats = [_mapping(item) for item in raw_formats]
        suitable = [
            item
            for item in formats
            if item.get("vcodec") != "none" and item.get("acodec") != "none" and item.get("ext") == "mp4"
        ]
        if suitable:
            media_url = _text(max(suitable, key=_format_height), "url")
            is_video = True
        elif formats:
            media_url = _text(formats[-1], "url")
            is_video = formats[-1].get("vcodec") != "none"
    return [MediaItem(media_url, is_video)] if media_url else []


def process_extraction_info(raw: object, url: str) -> RichMediaPayload | None:
    """Parse provider-independent yt-dlp facts without any network, Telegram object or worker state."""
    info = _mapping(raw)
    if not info:
        return None
    title = _text(info, "title", "No Title")
    clean_desc = clean_caption_text(_text(info, "description"), strip_hashtags=True)
    likes, views = _count(info, "like_count"), _count(info, "view_count")
    stats = []
    if likes:
        stats.append(f"❤️ {format_number(likes)}")
    if views:
        stats.append(f"👁️ {format_number(views)}")
    return RichMediaPayload(
        author_name=_text(info, "uploader") or _text(info, "channel", "Unknown"),
        author_handle=_text(info, "uploader_id", "unknown"),
        author_url=_text(info, "uploader_url"),
        content=clean_desc if len(clean_desc) > MIN_DESCRIPTION_LENGTH else title,
        footer_text=" | ".join(stats),
        original_url=url,
        media_items=_media_items(info),
    )


def _add_cookies(ydl: yt_dlp.YoutubeDL, url: str, cookies: dict[str, str]) -> None:
    host = urlsplit(url).hostname
    if not host:
        return
    for name, value in cookies.items():
        ydl.cookiejar.set_cookie(
            Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=host,
                domain_specified=False,
                domain_initial_dot=False,
                path="/",
                path_specified=True,
                secure=urlsplit(url).scheme == "https",
                expires=None,
                discard=True,
                comment=None,
                comment_url=None,
                rest={},
            )
        )


def extract_info(url: str, cookies: dict[str, str] | None = None) -> object:
    """Run ordinary bounded yt-dlp I/O without an event loop or payload-bearing diagnostics."""
    options = {
        "quiet": True,
        "logger": MediaBackendLogger(),
        "no_warnings": True,
        "skip_download": True,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "socket_timeout": EXTRACTION_TIMEOUT_SECONDS,
        "retries": 0,
        "extractor_retries": 0,
        "playlistend": MAX_PLAYLIST_ITEMS,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        if cookies:
            _add_cookies(ydl, url, cookies)
        return ydl.extract_info(url, download=False)


class NativeMediaExtractor:
    """Synchronous worker entrypoint sharing the same extraction and parsing as the bot."""

    def extract(self, url: str, cookies: dict[str, str] | None = None) -> RichMediaPayload | None:
        """Return provider facts without crossing an asynchronous execution boundary."""
        try:
            return process_extraction_info(extract_info(url, cookies), url)
        except Exception:
            logger.warning("Media extraction failed")
            return None

    extract_payload = extract


class MediaExtractor:
    """Keep existing async bot callers while sharing the native yt-dlp operation."""

    MIN_DESCRIPTION_LENGTH = MIN_DESCRIPTION_LENGTH
    YT_SHORTS_PATTERN = re.compile(r"https?://(?:www\.)?youtube\.com/shorts/(?P<id>[\w-]+)")

    async def extract(self, url: str, cookies: dict[str, str] | None = None) -> RichMediaPayload | None:
        """Offload the blocking backend only for the async bot path."""
        try:
            info = await asyncio.to_thread(self._extract_info, url, cookies)
            return process_extraction_info(info, url)
        except Exception:
            logger.warning("Media extraction failed")
            return None

    extract_payload = extract

    def _extract_info(self, url: str, cookies: dict[str, str] | None = None) -> object:
        return extract_info(url, cookies)

    def _process_info(self, info: object, url: str) -> RichMediaPayload | None:
        return process_extraction_info(info, url)

    def _extract_media_items(self, info: object) -> list[MediaItem]:
        return _media_items(_mapping(info))
