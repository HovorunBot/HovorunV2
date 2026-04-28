"""Module for handling Facebook media using FacebookService."""

import re

import aiohttp

from hovorunv2.application.clients.facebook import FacebookService
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.infrastructure.logger import get_logger

from .base import RichMediaCommand

logger = get_logger(__name__)


class FacebookCommand(RichMediaCommand):
    """Command for interacting with Facebook and processing links."""

    def __init__(self, facebook_service: FacebookService, media_downloader: MediaDownloader) -> None:
        """Initialize command with its dependencies."""
        super().__init__(media_downloader)
        self._facebook_service = facebook_service

    @property
    def pattern(self) -> re.Pattern:
        """Regex pattern to match Facebook links."""
        return self._facebook_service.PATTERN

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch Facebook data using FacebookService."""
        return await self._facebook_service.extract_payload(session, match.group(0), chat_id, platform)
