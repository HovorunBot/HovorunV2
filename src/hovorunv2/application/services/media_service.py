"""Application service for media downloading."""

import asyncio
from http import HTTPStatus
from typing import TYPE_CHECKING

import aiohttp
from aiogram.types import BufferedInputFile

from hovorunv2.infrastructure.logger import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = get_logger(__name__)


class MediaService:
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
        self, url: str, filename: str, session: aiohttp.ClientSession | None = None
    ) -> BufferedInputFile | None:
        """Download a single URL and return a BufferedInputFile.

        Args:
            url: The URL to download.
            filename: The filename to assign to the buffer.
            session: Optional aiohttp session to override the default one.

        Returns:
            BufferedInputFile or None if download fails.

        """
        actual_session = session or self._session
        return await self._perform_download(actual_session, url, filename)

    async def download_batch(
        self, urls: list[str], prefix: str = "media", ext: str = "jpg", session: aiohttp.ClientSession | None = None
    ) -> list[BufferedInputFile]:
        """Download multiple URLs concurrently.

        Args:
            urls: List of URLs to download.
            prefix: Filename prefix.
            ext: Default extension.
            session: Optional aiohttp session.

        Returns:
            List of successfully downloaded BufferedInputFile objects.

        """
        tasks = []
        for i, url in enumerate(urls):
            filename = f"{prefix}_{i}.{ext}"
            tasks.append(self.download_as_buffered_file(url, filename, session))

        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

    async def _perform_download(
        self, session: aiohttp.ClientSession, url: str, filename: str
    ) -> BufferedInputFile | None:
        """Perform the actual network request."""
        try:
            async with session.get(url, timeout=self.DEFAULT_TIMEOUT_SECONDS) as resp:
                if resp.status == HTTPStatus.OK:
                    content = await resp.read()
                    return BufferedInputFile(content, filename=filename)
                logger.error("Failed to download %s: HTTP %d", url, resp.status)
        except Exception:
            logger.exception("Exception during media download: %s", url)

        return None
