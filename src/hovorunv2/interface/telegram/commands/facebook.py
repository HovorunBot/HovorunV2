"""Module for handling Facebook media using MediaExtractorService."""

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
class FacebookCommand(RichMediaCommand):
    """Command for interacting with Facebook and processing links."""

    @property
    def pattern(self) -> re.Pattern:
        """Regex pattern to match Facebook links."""
        return container.media_extractor_service.FACEBOOK_PATTERN

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch Facebook data using MediaExtractorService."""
        return await container.media_extractor_service.extract_payload(session, match.group(0), chat_id, platform)
