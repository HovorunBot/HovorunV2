"""Module for handling Bluesky media using BlueskyService."""

import re

import aiohttp

from hovorunv2.application.clients.bluesky import BlueskyService
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.infrastructure.logger import get_logger

from .base import RichMediaCommand

logger = get_logger(__name__)


class BlueskyCommand(RichMediaCommand):
    """Command for interacting with Bluesky and processing links."""

    def __init__(self, bluesky_service: BlueskyService, media_downloader: MediaDownloader) -> None:
        """Initialize command with its dependencies."""
        super().__init__(media_downloader)
        self._bluesky_service = bluesky_service

    @property
    def pattern(self) -> re.Pattern:
        """Regex pattern to match Bluesky links."""
        return self._bluesky_service.PATTERN

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch Bluesky data using BlueskyService."""
        return await self._bluesky_service.extract_payload(session, match.group(0), chat_id, platform)
