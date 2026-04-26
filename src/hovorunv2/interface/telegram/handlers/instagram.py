"""Module for handling Instagram media using InstagramService."""

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
class InstagramCommand(RichMediaCommand):
    """Command for interacting with Instagram and processing links."""

    @property
    def pattern(self) -> re.Pattern:
        """Regex pattern to match Instagram links."""
        return container.instagram_service.PATTERN

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch Instagram data using InstagramService."""
        return await container.instagram_service.extract_payload(session, match.group(0), chat_id, platform)
