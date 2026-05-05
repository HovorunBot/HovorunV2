"""Application service for media downloading."""

import asyncio
import tempfile
from collections.abc import Mapping
from http import HTTPStatus
from pathlib import Path

import aiohttp
import yt_dlp
from aiogram.types import BufferedInputFile

from hovorunv2.application.dtos import MediaItem
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class MediaDownloader:
    """Service to handle downloading media from URLs into RAM."""

    DEFAULT_TIMEOUT_SECONDS: int = 30
    DEFAULT_HEADERS: Mapping[str, str] = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize service with mandatory session."""
        self._session = session

    async def download_as_buffered_file(
        self, url: str, filename: str, *, is_video: bool = False, session: aiohttp.ClientSession | None = None
    ) -> BufferedInputFile | None:
        """Download a single URL and return a BufferedInputFile.

        Args:
            url: The URL to download.
            filename: The filename to assign to the buffer.
            is_video: Whether the media is a video (triggers HLS/yt-dlp fallback).
            session: Optional aiohttp session to override the default one.

        Returns:
            BufferedInputFile or None if download fails.

        """
        actual_session = session or self._session
        return await self._perform_download(actual_session, url, filename, is_video=is_video)

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
            async with session.get(url, timeout=self.DEFAULT_TIMEOUT_SECONDS, headers=self.DEFAULT_HEADERS) as resp:
                if resp.status == HTTPStatus.OK:
                    content = await resp.read()
                    # If it's supposed to be a video but we got a tiny response, it might be an HLS manifest
                    if is_video and len(content) < 1024:  # noqa: PLR2004
                        text_content = content.decode("utf-8", errors="ignore")
                        if "#EXTM3U" in text_content:
                            logger.info("Detected HLS manifest via aiohttp, falling back to yt-dlp")
                            return await self._download_with_ytdlp(url, filename)

                    return BufferedInputFile(content, filename=filename)

                logger.error("Failed to download %s: HTTP %d", url, resp.status)
        except Exception:
            logger.exception("Exception during media download: %s", url)

        # 3. Fallback to yt-dlp for failed video downloads
        if is_video:
            logger.info("Falling back to yt-dlp for video download: %s", url)
            return await self._download_with_ytdlp(url, filename)

        return None

    async def _download_with_ytdlp(self, url: str, filename: str) -> BufferedInputFile | None:
        """Download video using yt-dlp to a temporary file and return as buffer."""
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                output_template = str(tmp_path / "downloaded.%(ext)s")
                ydl_opts = {
                    "format": "bestvideo+bestaudio/best",
                    "outtmpl": output_template,
                    "quiet": True,
                    "no_warnings": True,
                }

                def _download() -> bytes | None:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        if not info:
                            return None
                        file_path = Path(ydl.prepare_filename(info))
                        if file_path.exists():
                            return file_path.read_bytes()
                    return None

                content = await asyncio.to_thread(_download)
                if content:
                    return BufferedInputFile(content, filename=filename)

                logger.error("yt-dlp failed to download file from %s", url)
        except Exception:
            logger.exception("Exception during yt-dlp download: %s", url)

        return None
