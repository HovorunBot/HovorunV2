"""Application service for media downloading."""

import asyncio
import html
import logging
import tempfile
from collections.abc import Mapping
from functools import partial
from http import HTTPStatus
from pathlib import Path

import aiohttp
import yt_dlp
from aiogram.types import BufferedInputFile

from hovorunv2.application.dtos import MediaItem
from hovorunv2.application.media.backend_logging import MediaBackendLogger
from hovorunv2.application.worker_io import SyncHTTPPort

logger = logging.getLogger(__name__)


MAX_MEDIA_BYTES = 50 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 64 * 1024
HLS_MANIFEST_SIZE_THRESHOLD = 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
DEFAULT_DOWNLOAD_HEADERS: Mapping[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def is_hls_manifest(content: bytes) -> bool:
    """Share content detection between direct async and native downloads."""
    return len(content) < HLS_MANIFEST_SIZE_THRESHOLD and "#EXTM3U" in content.decode("utf-8", errors="ignore")


def _enforce_download_limit(event: dict[str, object], *, max_bytes: int) -> None:
    downloaded = event.get("downloaded_bytes")
    if isinstance(downloaded, int) and downloaded > max_bytes:
        msg = "Media download exceeds the configured byte bound."
        raise yt_dlp.utils.DownloadError(msg)


def download_ytdlp_bytes(url: str, *, max_bytes: int = MAX_MEDIA_BYTES) -> bytes | None:
    """Own yt-dlp temporary output synchronously, count bytes and delete every temporary artifact."""
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            options = {
                "format": "bestvideo+bestaudio/best",
                "outtmpl": str(root / "downloaded.%(ext)s"),
                "quiet": True,
                "logger": MediaBackendLogger(),
                "no_warnings": True,
                "socket_timeout": DOWNLOAD_TIMEOUT_SECONDS,
                "retries": 0,
                "extractor_retries": 0,
                "fragment_retries": 0,
                "max_filesize": max_bytes,
                "progress_hooks": [partial(_enforce_download_limit, max_bytes=max_bytes)],
            }
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    return None
                output = Path(ydl.prepare_filename(info))
                if not output.resolve().is_relative_to(root.resolve()) or not output.is_file():
                    return None
                if output.stat().st_size > max_bytes:
                    return None
                with output.open("rb") as stream:
                    content = stream.read(max_bytes + 1)
                return content if 0 < len(content) <= max_bytes else None
    except Exception:
        logger.warning("Media download failed")
        return None


class NativeMediaDownloader:
    """Synchronous worker downloads sharing HLS detection and bounded yt-dlp fallback."""

    def __init__(self, http: SyncHTTPPort) -> None:
        self._http = http

    def download(self, url: str, *, is_video: bool = False) -> bytes | None:
        """Try direct bounded HTTP before the existing video fallback."""
        clean_url = html.unescape(url)
        if is_video and (".m3u8" in clean_url or "blob:" in clean_url):
            return download_ytdlp_bytes(clean_url)
        content = self._http.download(clean_url, headers=DEFAULT_DOWNLOAD_HEADERS)
        if content is not None and (not is_video or not is_hls_manifest(content)):
            return content
        return download_ytdlp_bytes(clean_url) if is_video else None

    def download_batch(self, items: list[MediaItem]) -> list[bytes | None]:
        """Preserve item order and in-memory bytes without parallel worker execution slots."""
        return [
            item.data if item.data is not None else self.download(item.url, is_video=item.is_video) for item in items
        ]


class MediaDownloader:
    """Service to handle downloading media from URLs into RAM."""

    DEFAULT_TIMEOUT_SECONDS = DOWNLOAD_TIMEOUT_SECONDS
    HLS_MANIFEST_SIZE_THRESHOLD = HLS_MANIFEST_SIZE_THRESHOLD
    DEFAULT_HEADERS = DEFAULT_DOWNLOAD_HEADERS

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize service with mandatory session."""
        self._session = session

    async def download_as_buffered_file(
        self, url: str, filename: str, *, is_video: bool = False, session: aiohttp.ClientSession | None = None
    ) -> BufferedInputFile | None:
        """Download a single URL and return a BufferedInputFile."""
        # Fix HTML entities in URLs (common in scraped meta tags)
        clean_url = html.unescape(url)
        actual_session = session or self._session
        return await self._perform_download(actual_session, clean_url, filename, is_video=is_video)

    async def download_batch(
        self, items: list[MediaItem], prefix: str = "media", session: aiohttp.ClientSession | None = None
    ) -> list[BufferedInputFile | None]:
        """Download multiple media items concurrently.

        Args:
            items: List of MediaItem to download.
            prefix: Filename prefix.
            session: Optional aiohttp session.

        Returns:
            List of BufferedInputFile objects or None for failed downloads.

        """
        tasks = []
        for i, item in enumerate(items):
            ext = "mp4" if item.is_video else "jpg"
            filename = f"{prefix}_{i}.{ext}"
            tasks.append(self.download_as_buffered_file(item.url, filename, is_video=item.is_video, session=session))

        return list(await asyncio.gather(*tasks))

    async def _perform_download(
        self, session: aiohttp.ClientSession, url: str, filename: str, *, is_video: bool = False
    ) -> BufferedInputFile | None:
        """Perform the actual network request with yt-dlp fallback for videos."""
        # 1. Direct HLS/M3U8 detection
        if is_video and (".m3u8" in url or "blob:" in url):
            return await self._download_with_ytdlp(url, filename)

        # 2. Try direct aiohttp download
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=self.DEFAULT_TIMEOUT_SECONDS), headers=self.DEFAULT_HEADERS
            ) as resp:
                if resp.status != HTTPStatus.OK:
                    logger.error("Media download failed: HTTP %d", resp.status)
                    return await self._handle_video_fallback(url, filename, is_video=is_video)

                chunks = []
                size = 0
                async for chunk in resp.content.iter_chunked(DOWNLOAD_CHUNK_BYTES):
                    size += len(chunk)
                    if size > MAX_MEDIA_BYTES:
                        logger.warning("Media download exceeds the configured byte bound")
                        return None
                    chunks.append(chunk)
                content = b"".join(chunks)
                if is_video and self._is_hls_manifest(content):
                    logger.info("Detected HLS manifest via aiohttp, falling back to yt-dlp")
                    return await self._download_with_ytdlp(url, filename)

                return BufferedInputFile(content, filename=filename)
        except Exception:
            logger.exception("Exception during media download")

        return await self._handle_video_fallback(url, filename, is_video=is_video)

    def _is_hls_manifest(self, content: bytes) -> bool:
        """Check if content looks like an HLS manifest."""
        return is_hls_manifest(content)

    async def _handle_video_fallback(self, url: str, filename: str, *, is_video: bool) -> BufferedInputFile | None:
        """Fallback to yt-dlp for video downloads."""
        if not is_video:
            return None
        logger.info("Falling back to yt-dlp for video download")
        return await self._download_with_ytdlp(url, filename)

    async def _download_with_ytdlp(self, url: str, filename: str) -> BufferedInputFile | None:
        """Download video using yt-dlp to a temporary file and return as buffer."""
        content = await asyncio.to_thread(download_ytdlp_bytes, url)
        return BufferedInputFile(content, filename=filename) if content else None
