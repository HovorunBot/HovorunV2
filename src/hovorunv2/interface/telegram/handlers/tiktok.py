"""Module for handling TikTok video links using TikTokService."""

import re

import aiohttp

from hovorunv2.application.clients.tiktok import TikTokService
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.application.media.extractor import MediaExtractor
from hovorunv2.infrastructure.logger import get_logger

from .base import RichMediaCommand

logger = get_logger(__name__)


class TikTokCommand(RichMediaCommand):
    """Command for interacting with TikTok and processing video links."""

    def __init__(
        self,
        tiktok_service: TikTokService,
        media_downloader: MediaDownloader,
        media_extractor: MediaExtractor,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize command with its dependencies."""
        super().__init__(media_downloader, session)
        self._tiktok_service = tiktok_service
        self._media_extractor = media_extractor

    @property
    def pattern(self) -> re.Pattern:
        """Regex pattern to match TikTok links."""
        return self._tiktok_service.PATTERN

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch TikTok data using TikTokService with yt-dlp fallback."""
        url = match.group(0)
        payload = await self._tiktok_service.extract_payload(session, url, chat_id, platform)
        if not payload:
            logger.info("TikTokService failed for %s, falling back to MediaExtractor", url)
            payload = await self._media_extractor.extract_payload(session, url, chat_id, platform)
        return payload
