"""Module for handling YouTube Shorts using MediaExtractor."""

import re

import aiohttp

from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.application.media.extractor import MediaExtractor
from hovorunv2.infrastructure.logger import get_logger

from .base import RichMediaCommand

logger = get_logger(__name__)


class YoutubeShortsCommand(RichMediaCommand):
    """Command for interacting with YouTube and processing Shorts links."""

    def __init__(
        self,
        media_extractor: MediaExtractor,
        media_downloader: MediaDownloader,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize command with its dependencies."""
        super().__init__(media_downloader, session)
        self._media_extractor = media_extractor

    @property
    def name(self) -> str:
        """Command name."""
        return "youtube"

    @property
    def pattern(self) -> re.Pattern:
        """Regex pattern to match YouTube Shorts links."""
        return self._media_extractor.YT_SHORTS_PATTERN

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch YouTube Shorts data using MediaExtractor."""
        return await self._media_extractor.extract_payload(session, match.group(0), chat_id, platform)
