"""Module for handling Threads post links using ThreadsService."""

import re

import aiohttp

from hovorunv2.application.clients.threads import ThreadsService
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.infrastructure.logger import get_logger

from .base import RichMediaCommand

logger = get_logger(__name__)


class ThreadsCommand(RichMediaCommand):
    """Command for interacting with Threads and processing thread links."""

    def __init__(self, threads_service: ThreadsService, media_downloader: MediaDownloader) -> None:
        """Initialize command with its dependencies."""
        super().__init__(media_downloader)
        self._threads_service = threads_service

    @property
    def pattern(self) -> re.Pattern:
        """Regex pattern to match Threads links."""
        return self._threads_service.PATTERN

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch Threads data using ThreadsService."""
        return await self._threads_service.extract_payload(session, match.group(0), chat_id, platform)
