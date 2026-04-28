"""Module for handling Instagram media using InstagramService."""

import re

import aiohttp

from hovorunv2.application.clients.instagram import InstagramService
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.infrastructure.logger import get_logger

from .base import RichMediaCommand

logger = get_logger(__name__)


class InstagramCommand(RichMediaCommand):
    """Command for interacting with Instagram and processing links."""

    def __init__(self, instagram_service: InstagramService, media_downloader: MediaDownloader) -> None:
        """Initialize command with its dependencies."""
        super().__init__(media_downloader)
        self._instagram_service = instagram_service

    @property
    def pattern(self) -> re.Pattern:
        """Regex pattern to match Instagram links."""
        return self._instagram_service.PATTERN

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch Instagram data using InstagramService."""
        return await self._instagram_service.extract_payload(session, match.group(0), chat_id, platform)
