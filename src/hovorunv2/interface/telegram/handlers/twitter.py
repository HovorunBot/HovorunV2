"""Module for handling Twitter/X post links using TwitterService."""

import re

import aiohttp

from hovorunv2.application.clients.twitter import TwitterService
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.infrastructure.logger import get_logger

from .base import RichMediaCommand

logger = get_logger(__name__)


class TwitterCommand(RichMediaCommand):
    """Command for interacting with Twitter and processing tweet links."""

    def __init__(self, twitter_service: TwitterService, media_downloader: MediaDownloader) -> None:
        """Initialize command with its dependencies."""
        super().__init__(media_downloader)
        self._twitter_service = twitter_service

    @property
    def pattern(self) -> re.Pattern:
        """Regex pattern to match Twitter links."""
        return self._twitter_service.PATTERN

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch tweet data using TwitterService."""
        return await self._twitter_service.extract_payload(session, match.group(0), chat_id, platform)
