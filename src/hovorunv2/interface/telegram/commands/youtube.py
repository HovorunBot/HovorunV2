"""Module for handling YouTube Shorts using MediaExtractorService."""

from typing import TYPE_CHECKING

from hovorunv2.infrastructure.container import container
from hovorunv2.infrastructure.logger import get_logger

from .base import RichMediaCommand, register_command

if TYPE_CHECKING:
    import re

    import aiohttp

    from hovorunv2.application.dtos import RichMediaPayload

logger = get_logger(__name__)


@register_command
class YoutubeShortsCommand(RichMediaCommand):
    """Command for interacting with YouTube and processing Shorts links."""

    @property
    def pattern(self) -> re.Pattern:
        """Regex pattern to match YouTube Shorts links."""
        return container.media_extractor_service.YT_SHORTS_PATTERN

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch YouTube Shorts data using MediaExtractorService."""
        return await container.media_extractor_service.extract_payload(session, match.group(0), chat_id, platform)
