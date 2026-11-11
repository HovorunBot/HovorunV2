"""Shared yt-dlp extraction and pure parsing for native synchronous workers."""

import logging
import math
from contextlib import nullcontext
from http.cookiejar import Cookie
from time import monotonic
from urllib.parse import urlsplit

import yt_dlp
from yt_dlp.extractor import gen_extractor_classes
from yt_dlp.networking import Request, Response

from hovorunv2.application.media.backend_logging import MediaBackendLogger
from hovorunv2.application.media.deadlines import YTDLP_ALLOWANCE_SECONDS, ExtractionDeadline
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.limits import MAX_PLAYLIST_ITEMS
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.utils.formatting import format_number
from hovorunv2.utils.operation_timeout import OperationTimeout
from hovorunv2.utils.text import clean_caption_text

logger = logging.getLogger(__name__)
MIN_DESCRIPTION_LENGTH = 10
EXTRACTION_TIMEOUT_SECONDS = 30


class BoundedYoutubeDL(yt_dlp.YoutubeDL):
    """Share one request budget across the backend's sequential extraction requests."""

    def __init__(self, options: dict[str, object], timeout_seconds: float = EXTRACTION_TIMEOUT_SECONDS) -> None:
        super().__init__(options)
        self._request_deadline = monotonic() + timeout_seconds

    def urlopen(self, req: Request | str) -> Response:
        """Bound each request by the time remaining for this extraction.

        Args:
            req: Backend HTTP request or URL.

        Returns:
            Backend HTTP response.

        Raises:
            MediaExtractionError: If the extraction request budget is exhausted.
        """
        remaining = self._request_deadline - monotonic()
        if remaining <= 0:
            msg = "Media extraction request budget expired."
            raise MediaExtractionError(msg)
        request = Request(req) if isinstance(req, str) else req
        configured = request.extensions.get("timeout")
        request.extensions["timeout"] = min(remaining, configured) if isinstance(configured, int | float) else remaining
        return super().urlopen(request)


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


def _format_rank(value: dict[str, object]) -> tuple[bool, int]:
    return value.get("protocol") in {"http", "https"}, _count(value, "height")


def _media_items(info: dict[str, object], source_url: str) -> list[MediaItem]:
    entries = info.get("entries")
    if isinstance(entries, list | tuple):
        items = [item for entry in entries[:MAX_PLAYLIST_ITEMS] for item in _media_items(_mapping(entry), source_url)]
        if items:
            return items
    selected = info.get("requested_formats")
    if isinstance(selected, list | tuple) and len(selected) > 1:
        return [MediaItem(_text(info, "webpage_url") or source_url, is_video=True, requires_download=True)]
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
            media_url = _text(max(suitable, key=_format_rank), "url")
            is_video = True
        elif formats:
            media_url = _text(formats[-1], "url")
            is_video = formats[-1].get("vcodec") != "none"
    return [MediaItem(media_url, is_video)] if media_url else []


def process_extraction_info(raw: object, url: str) -> RichMediaPayload:
    """Parse provider-independent yt-dlp facts without any network or bot state.

    Args:
        raw: Raw extraction result from the backend.
        url: Original media URL.

    Returns:
        Parsed provider payload.

    Raises:
        MediaExtractionError: If the raw result lacks usable metadata.
    """
    info = _mapping(raw)
    if not info:
        msg = "Media extraction returned unusable metadata."
        raise MediaExtractionError(msg)
    title = _text(info, "title", "No Title")
    clean_desc = clean_caption_text(_text(info, "description"), strip_hashtags=True)
    likes, views = _count(info, "like_count"), _count(info, "view_count")
    stats = []
    if likes:
        stats.append(f"❤️ {format_number(likes)}")
    if views:
        stats.append(f"👁️ {format_number(views)}")
    items = _media_items(info, url)
    if not items and not (_text(info, "title") or _text(info, "description")):
        msg = "Media extraction returned unusable metadata."
        raise MediaExtractionError(msg)
    return RichMediaPayload(
        author_name=_text(info, "uploader") or _text(info, "channel", "Unknown"),
        author_handle=_text(info, "uploader_id", "unknown").lstrip("@") or "unknown",
        author_url=_text(info, "uploader_url"),
        content=clean_desc if len(clean_desc) > MIN_DESCRIPTION_LENGTH else title,
        footer_text=" | ".join(stats),
        original_url=url,
        media_items=items,
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


def _load_selected_extractor(backend: BoundedYoutubeDL, url: str) -> str | None:
    """Resolve backend modules before arming an interrupt that could leave imports half initialized."""
    for extractor_class in gen_extractor_classes():
        if extractor_class.suitable(url):
            key = extractor_class.ie_key()
            backend.get_info_extractor(key)
            return key
    return None


def extract_info(
    url: str, cookies: dict[str, str] | None = None, *, deadline: ExtractionDeadline | None = None
) -> object:
    """Run bounded yt-dlp I/O without an event loop or payload-bearing diagnostics.

    Args:
        url: Media URL to extract.
        cookies: Optional provider cookies for the backend.
        deadline: Optional shared stage deadline for the total backend operation.

    Returns:
        Raw extraction result from yt-dlp.

    Raises:
        MediaExtractionError: If the backend fails or returns unusable data.
    """
    options: dict[str, object] = {
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
    allowance = deadline.narrow(YTDLP_ALLOWANCE_SECONDS) if deadline is not None else None
    timeout = allowance.remaining() if allowance is not None else EXTRACTION_TIMEOUT_SECONDS
    try:
        with BoundedYoutubeDL(options, timeout) as ydl:
            ie_key = _load_selected_extractor(ydl, url)
            if cookies:
                _add_cookies(ydl, url, cookies)
            guard = OperationTimeout(allowance.expires_at) if allowance is not None else nullcontext()
            with guard:
                return ydl.extract_info(url, download=False, ie_key=ie_key)
    except yt_dlp.utils.YoutubeDLError, OSError, ValueError:
        logger.warning("Media extraction failed")
        msg = "Media extraction failed."
        raise MediaExtractionError(msg) from None


class NativeMediaExtractor:
    """Synchronous worker entrypoint sharing the same extraction and parsing as the bot."""

    def extract(
        self, url: str, cookies: dict[str, str] | None = None, *, deadline: ExtractionDeadline | None = None
    ) -> RichMediaPayload:
        """Return provider facts without crossing an asynchronous execution boundary.

        Args:
            url: Media URL to extract.
            cookies: Optional provider cookies for the backend.
            deadline: Optional shared stage deadline for the total backend operation.

        Returns:
            Parsed provider payload.
        """
        return process_extraction_info(extract_info(url, cookies, deadline=deadline), url)

    extract_payload = extract
