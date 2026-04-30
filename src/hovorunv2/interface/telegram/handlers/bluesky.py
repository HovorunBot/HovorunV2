"""Module for handling Bluesky media using BlueskyService."""

import re

import aiohttp

from hovorunv2.application.clients.bluesky import BlueskyService
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.application.media.extractor import MediaExtractor
from hovorunv2.infrastructure.logger import get_logger

from .base import RichMediaCommand

logger = get_logger(__name__)


class BlueskyCommand(RichMediaCommand):
    """Command for interacting with Bluesky and processing links."""

    def __init__(
        self,
        bluesky_service: BlueskyService,
        media_downloader: MediaDownloader,
        media_extractor: MediaExtractor,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize command with its dependencies."""
        super().__init__(media_downloader, session)
        self._bluesky_service = bluesky_service
        self._media_extractor = media_extractor

    @property
    def pattern(self) -> re.Pattern:
        """Regex pattern to match Bluesky links."""
        return self._bluesky_service.PATTERN

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch Bluesky data using BlueskyService with yt-dlp fallback."""
        url = match.group(0)
        payload = await self._bluesky_service.extract_payload(session, url, chat_id, platform)
        if not payload:
            logger.info("BlueskyService failed for %s, falling back to MediaExtractor", url)
            payload = await self._media_extractor.extract_payload(session, url, chat_id, platform)
        return payload
