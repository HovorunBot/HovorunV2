"""Module for handling TikTok video links using TikTokService."""

import re

import aiohttp

from hovorunv2.application.clients.tiktok import TikTokService
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.infrastructure.logger import get_logger

from .base import RichMediaCommand

logger = get_logger(__name__)


class TikTokCommand(RichMediaCommand):
    """Command for interacting with TikTok and processing video links."""

    def __init__(self, tiktok_service: TikTokService, media_downloader: MediaDownloader) -> None:
        """Initialize command with its dependencies."""
        super().__init__(media_downloader)
        self._tiktok_service = tiktok_service

    @property
    def pattern(self) -> re.Pattern:
        """Regex pattern to match TikTok links."""
        return self._tiktok_service.PATTERN

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch TikTok data using TikTokService."""
        return await self._tiktok_service.extract_payload(session, match.group(0), chat_id, platform)
