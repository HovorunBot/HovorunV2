"""Application service for Instagram media extraction using yt-dlp, embed and browser fallbacks."""

import logging
from functools import partial
from typing import Self

from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.jobs.http_ports import SyncHTTPPort
from hovorunv2.application.media.actions import ExtractionAction, ExtractionStage
from hovorunv2.application.media.browser import BrowserTab
from hovorunv2.application.media.deadlines import EMBED_ALLOWANCE_SECONDS, ExtractionDeadline
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.instagram.browser_extraction import extract_browser_post
from hovorunv2.application.media.instagram.ports import InstagramEmbedPort
from hovorunv2.application.media.instagram.results import InstagramPost
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.application.media.provider_config import INSTAGRAM_CONFIG, BrowserFallbackConfig, ProviderConfig
from hovorunv2.application.media.providers.base import BaseMediaClient

__all__ = ["INSTAGRAM_CONFIG", "InstagramService"]

logger = logging.getLogger(__name__)


class InstagramService(BaseMediaClient):
    """Service to interact with Instagram using yt-dlp, embed and browser fallbacks."""

    def __init__(
        self,
        embed: InstagramEmbedPort,
        config: ProviderConfig = INSTAGRAM_CONFIG,
        settings: AppSettings | None = None,
    ) -> None:
        """Initialize with required services."""
        super().__init__(config=config)
        self._settings: AppSettings | None = settings
        self._embed = embed

    @classmethod
    def sync(cls, settings: AppSettings | None = None, *, embed: InstagramEmbedPort) -> Self:
        """Create a client instance for synchronous worker extraction."""
        return cls(embed, INSTAGRAM_CONFIG, settings)

    def get_ytdlp_cookies(self) -> dict[str, str] | None:
        """Provide sessionid cookie to yt-dlp when configured."""
        if self._settings and self._settings.instagram_sessionid:
            return {"sessionid": self._settings.instagram_sessionid}
        return None

    def get_browser_cookies(self) -> list[dict[str, object]]:
        """Provide sessionid cookie to headless browser fallback when configured."""
        if not self._settings or not self._settings.instagram_sessionid:
            return []
        return [
            {
                "name": "sessionid",
                "value": self._settings.instagram_sessionid,
                "domain": ".instagram.com",
                "path": "/",
            }
        ]

    def _extract_embed_fallback_sync(self, url: str) -> RichMediaPayload | None:
        """Run the bounded public embed fallback from validated Instagram facts."""
        post = InstagramPost.from_url(url)
        deadline = ExtractionDeadline.after(EMBED_ALLOWANCE_SECONDS)
        return self._embed.extract(post, deadline).to_payload()

    def _extract_from_browser_tab(
        self, tab: BrowserTab, url: str, b_cfg: BrowserFallbackConfig
    ) -> RichMediaPayload | None:
        """Extract the full ordered carousel using structured data and bounded traversal."""
        self._dismiss_gate_buttons(tab, b_cfg)
        post = InstagramPost.from_url(url)
        return extract_browser_post(tab, post).to_payload()

    def _media_actions(
        self, url: str, http: SyncHTTPPort, extractor: NativeMediaExtractor
    ) -> tuple[ExtractionAction, ...]:
        """Declare Instagram's ordered media-stage actions."""
        actions = [
            ExtractionAction("direct extraction", ExtractionStage.MEDIA, partial(self.extract_sync, url, http)),
            ExtractionAction(
                "generic extraction",
                ExtractionStage.MEDIA,
                partial(extractor.extract, url, cookies=self.get_ytdlp_cookies()),
            ),
            ExtractionAction("embed fallback", ExtractionStage.MEDIA, partial(self._extract_embed_fallback_sync, url)),
            ExtractionAction("browser extraction", ExtractionStage.BROWSER, lambda: None),
        ]
        return tuple(actions)
