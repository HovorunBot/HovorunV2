"""Application service for Instagram media extraction using yt-dlp, embed and browser fallbacks."""

import logging
from collections.abc import Callable
from functools import partial
from typing import Self

from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.jobs.http_ports import SyncBrowserPort, SyncHTTPPort
from hovorunv2.application.media.actions import ActionOutcome, ExtractionAction, ExtractionStage
from hovorunv2.application.media.browser import BrowserTab
from hovorunv2.application.media.deadlines import EMBED_ALLOWANCE_SECONDS, ExtractionDeadline
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.instagram.browser_extraction import extract_browser_post
from hovorunv2.application.media.instagram.ports import InstagramEmbedPort
from hovorunv2.application.media.instagram.results import INCOMPLETE_MEDIA_TEXT, InstagramPayload, InstagramPost
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.application.media.provider_config import INSTAGRAM_CONFIG, BrowserFallbackConfig, ProviderConfig
from hovorunv2.application.media.providers.base import BaseMediaClient

__all__ = ["INSTAGRAM_CONFIG", "InstagramService"]

logger = logging.getLogger(__name__)


def _post_url(url: str) -> str:
    """Drop comment subpaths while preserving ordinary post links."""
    try:
        post = InstagramPost.from_url(url)
    except MediaExtractionError:
        return url
    return post.canonical_url if "/c/" in url else url


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

    def _extract_from_browser_tab(
        self, tab: BrowserTab, url: str, b_cfg: BrowserFallbackConfig
    ) -> RichMediaPayload | None:
        """Extract the full ordered carousel using structured data and bounded traversal."""
        self._dismiss_gate_buttons(tab, b_cfg)
        post = InstagramPost.from_url(url)
        return extract_browser_post(tab, post).to_payload()

    def extract_browser_outcome_sync(
        self, url: str, http: SyncHTTPPort, browser: SyncBrowserPort, *, deadline: ExtractionDeadline
    ) -> ActionOutcome:
        """Keep browser completeness explicit and reject metadata-only Instagram output.

        Args:
            url: Canonical requested Instagram post URL.
            http: Existing worker HTTP transport, retained for the provider interface.
            browser: Child-owned browser transport.
            deadline: Shared cutoff for navigation, traversal and downloads.

        Returns:
            Usable browser media with explicit completeness evidence.

        Raises:
            MediaExtractionError: If no usable requested-post media remains.
        """
        payload = self._extract_browser_sync(_post_url(url), browser, deadline)
        if payload is None or not payload.media_items:
            msg = "Instagram browser extraction has no usable media."
            raise MediaExtractionError(msg)
        complete = isinstance(payload, InstagramPayload) and payload.complete
        if not complete:
            payload.footer_text = INCOMPLETE_MEDIA_TEXT
        return ActionOutcome(payload, complete=complete)

    def extract_browser_media_sync(
        self,
        url: str,
        http: SyncHTTPPort,
        browser: SyncBrowserPort,
        *,
        deadline: ExtractionDeadline | None = None,
    ) -> RichMediaPayload:
        """Return browser media without using the generic OpenGraph success policy.

        Args:
            url: Requested Instagram post URL.
            http: Existing worker HTTP transport.
            browser: Child-owned browser transport.
            deadline: Stage cutoff, or the standalone default browser allowance.

        Returns:
            Usable browser media retaining any partial warning. Extraction failures propagate.
        """
        return self.extract_browser_outcome_sync(
            url, http, browser, deadline=deadline or ExtractionDeadline.after(self.DEFAULT_TIMEOUT_SECONDS)
        ).payload

    def _embed_outcome(self, url: str, deadline: ExtractionDeadline) -> ActionOutcome:
        """Run the bounded public embed action with its completeness evidence."""
        result = self._embed.extract(InstagramPost.from_url(url), deadline.narrow(EMBED_ALLOWANCE_SECONDS))
        return ActionOutcome(result.to_payload(), complete=result.complete)

    def _media_outcome(self, strategy: Callable[[], RichMediaPayload | None]) -> ActionOutcome | None:
        """Accept a media-bearing generic result; text-only/metadata-only output continues."""
        payload = strategy()
        if payload is None or not payload.media_items:
            return None
        return ActionOutcome(payload, complete=True)

    def _media_actions(
        self,
        url: str,
        http: SyncHTTPPort,
        extractor: NativeMediaExtractor,
        deadline: ExtractionDeadline | None = None,
    ) -> tuple[ExtractionAction, ...]:
        """Declare Instagram's ordered media-stage actions."""
        deadline = deadline or ExtractionDeadline.after(self.DEFAULT_TIMEOUT_SECONDS)
        target_url = _post_url(url)
        actions = [
            ExtractionAction(
                "direct extraction",
                ExtractionStage.MEDIA,
                partial(self._terminal_outcome, partial(self.extract_sync, target_url, http)),
            ),
            ExtractionAction(
                "generic extraction",
                ExtractionStage.MEDIA,
                partial(
                    self._media_outcome,
                    partial(extractor.extract, target_url, cookies=self.get_ytdlp_cookies(), deadline=deadline),
                ),
            ),
            ExtractionAction(
                "embed fallback", ExtractionStage.MEDIA, partial(self._embed_outcome, target_url, deadline)
            ),
            ExtractionAction("browser extraction", ExtractionStage.BROWSER, lambda: None),
        ]
        return tuple(actions)
