"""Define mandatory public-embed extraction independently of native transport adapters."""

from typing import Protocol

from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.instagram.results import InstagramPost, InstagramResult


class InstagramEmbedPort(Protocol):
    """Return usable public media within a shared deadline or raise MediaExtractionError."""

    def extract(self, post: InstagramPost, deadline: ExtractionDeadline) -> InstagramResult:
        """Fetch only canonical public post facts without Instagram or Telegram credentials."""
        ...
