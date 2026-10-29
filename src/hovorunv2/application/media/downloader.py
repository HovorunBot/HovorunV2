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
from hovorunv2.application.media.errors import MediaDownloadError, MediaFailure
from hovorunv2.application.worker_io import HTTPFailure, HTTPTransportError, SyncHTTPPort

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


def _require_content(content: bytes, max_bytes: int = MAX_MEDIA_BYTES) -> bytes:
    if len(content) > max_bytes:
        raise MediaDownloadError(MediaFailure.TOO_LARGE)
    if not content:
        raise MediaDownloadError(MediaFailure.UNUSABLE)
    return content


def _read_downloaded_file(output: Path, root: Path, max_bytes: int) -> bytes:
    if not output.resolve().is_relative_to(root.resolve()) or not output.is_file():
        raise MediaDownloadError(MediaFailure.UNUSABLE)
    if output.stat().st_size > max_bytes:
        raise MediaDownloadError(MediaFailure.TOO_LARGE)
    with output.open("rb") as stream:
        content = stream.read(max_bytes + 1)
    return _require_content(content, max_bytes)


def _download_options(root: Path, max_bytes: int) -> dict[str, object]:
    return {
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


def _download_to_file(url: str, root: Path, max_bytes: int) -> Path:
    with yt_dlp.YoutubeDL(_download_options(root, max_bytes)) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            raise MediaDownloadError(MediaFailure.UNUSABLE)
        filename = ydl.prepare_filename(info)
        if not isinstance(filename, str):
            raise MediaDownloadError(MediaFailure.UNUSABLE)
        return Path(filename)


def download_ytdlp_bytes(url: str, *, max_bytes: int = MAX_MEDIA_BYTES) -> bytes:
    """Require bounded output while owning and cleaning every temporary backend artifact."""
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            return _read_downloaded_file(_download_to_file(url, root, max_bytes), root, max_bytes)
    except MediaDownloadError:
        raise
    except yt_dlp.utils.YoutubeDLError, OSError, ValueError:
        logger.warning("Media download failed")
        raise MediaDownloadError from None


class NativeMediaDownloader:
    """Synchronous worker downloads sharing HLS detection and bounded yt-dlp fallback."""

    def __init__(self, http: SyncHTTPPort) -> None:
        self._http = http

    def _direct_download(self, url: str) -> bytes:
        try:
            content = self._http.download_bytes(url, headers=DEFAULT_DOWNLOAD_HEADERS)
        except HTTPTransportError as error:
            code = MediaFailure.TOO_LARGE if error.code == HTTPFailure.TOO_LARGE else MediaFailure.UNAVAILABLE
            raise MediaDownloadError(code) from None
        return _require_content(content)

    def download(self, url: str, *, is_video: bool = False) -> bytes:
        """Require direct bytes or use the existing video-only fallback strategy."""
        clean_url = html.unescape(url)
        if is_video and (".m3u8" in clean_url or "blob:" in clean_url):
            return download_ytdlp_bytes(clean_url)
        try:
            content = self._direct_download(clean_url)
        except MediaDownloadError:
            if not is_video:
                raise
            return download_ytdlp_bytes(clean_url)
        if is_video and is_hls_manifest(content):
            return download_ytdlp_bytes(clean_url)
        return content

    def _download_item(self, item: MediaItem) -> bytes | MediaFailure:
        try:
            if item.data is not None:
                return _require_content(item.data)
            return self.download(item.url, is_video=item.is_video)
        except MediaDownloadError as error:
            return error.code

    def download_batch(self, items: list[MediaItem]) -> list[bytes | MediaFailure]:
        """Preserve ordered partial results with explicit failures, including existing in-memory bytes."""
        return [self._download_item(item) for item in items]


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
    ) -> BufferedInputFile:
        """Require one bounded download or raise MediaDownloadError."""
        # Fix HTML entities in URLs (common in scraped meta tags)
        clean_url = html.unescape(url)
        actual_session = session or self._session
        return await self._perform_download(actual_session, clean_url, filename, is_video=is_video)

    async def _download_item(
        self, item: MediaItem, filename: str, session: aiohttp.ClientSession
    ) -> BufferedInputFile | MediaFailure:
        try:
            return await self._perform_download(session, html.unescape(item.url), filename, is_video=item.is_video)
        except MediaDownloadError as error:
            return error.code

    async def download_batch(
        self, items: list[MediaItem], prefix: str = "media", session: aiohttp.ClientSession | None = None
    ) -> list[BufferedInputFile | MediaFailure]:
        """Download concurrently, retaining input order and explicit per-item failures."""
        actual_session = session or self._session
        tasks = []
        for index, item in enumerate(items):
            extension = "mp4" if item.is_video else "jpg"
            tasks.append(self._download_item(item, f"{prefix}_{index}.{extension}", actual_session))
        return list(await asyncio.gather(*tasks))

    async def _read_response(self, response: aiohttp.ClientResponse) -> bytes:
        if response.status != HTTPStatus.OK:
            raise MediaDownloadError
        if response.content_length is not None and response.content_length > MAX_MEDIA_BYTES:
            raise MediaDownloadError(MediaFailure.TOO_LARGE)
        chunks = []
        size = 0
        async for chunk in response.content.iter_chunked(DOWNLOAD_CHUNK_BYTES):
            size += len(chunk)
            if size > MAX_MEDIA_BYTES:
                raise MediaDownloadError(MediaFailure.TOO_LARGE)
            chunks.append(chunk)
        return _require_content(b"".join(chunks))

    async def _direct_download(self, session: aiohttp.ClientSession, url: str) -> bytes:
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=self.DEFAULT_TIMEOUT_SECONDS), headers=self.DEFAULT_HEADERS
            ) as response:
                return await self._read_response(response)
        except MediaDownloadError:
            raise
        except aiohttp.ClientError, OSError, TimeoutError, ValueError:
            logger.warning("Media download failed")
            raise MediaDownloadError from None

    async def _perform_download(
        self, session: aiohttp.ClientSession, url: str, filename: str, *, is_video: bool = False
    ) -> BufferedInputFile:
        """Require a direct response or a successful video strategy without swallowing failures."""
        # 1. Direct HLS/M3U8 detection
        if is_video and (".m3u8" in url or "blob:" in url):
            return await self._download_with_ytdlp(url, filename)

        # 2. Try direct aiohttp download
        try:
            content = await self._direct_download(session, url)
        except MediaDownloadError as error:
            if not is_video or error.code == MediaFailure.TOO_LARGE:
                raise
            return await self._download_with_ytdlp(url, filename)
        if is_video and self._is_hls_manifest(content):
            logger.info("Detected HLS manifest via aiohttp, falling back to yt-dlp")
            return await self._download_with_ytdlp(url, filename)
        return BufferedInputFile(content, filename=filename)

    def _is_hls_manifest(self, content: bytes) -> bool:
        """Check if content looks like an HLS manifest."""
        return is_hls_manifest(content)

    async def _download_with_ytdlp(self, url: str, filename: str) -> BufferedInputFile:
        """Download video using yt-dlp to a temporary file and return as buffer."""
        content = await asyncio.to_thread(download_ytdlp_bytes, url)
        return BufferedInputFile(content, filename=filename)
