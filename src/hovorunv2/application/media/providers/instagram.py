"""Application service for Instagram media extraction using instaloader and browser fallback."""

import logging
import re
from collections.abc import Callable
from functools import partial
from time import monotonic
from typing import ClassVar, Self

import instaloader
import requests

from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.jobs.http_ports import SyncHTTPPort
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.extractor import EXTRACTION_TIMEOUT_SECONDS
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.application.media.provider_config import INSTAGRAM_CONFIG, ProviderConfig
from hovorunv2.application.media.providers.base import BaseMediaClient
from hovorunv2.utils.text import clean_caption_text

__all__ = ["INSTAGRAM_CONFIG", "InstagramService"]

logger = logging.getLogger(__name__)


def _bounded_request(
    context: PrivateInstaloaderContext,
    request: Callable[..., requests.Response],
    *args: object,
    **kwargs: object,
) -> requests.Response:
    """Cap one instaloader request by the context's remaining extraction budget.

    Args:
        context: Instaloader context owning the shared request deadline.
        request: Original session request callable.
        *args: Positional request arguments forwarded unchanged.
        **kwargs: Keyword request arguments with the timeout narrowed to the remaining budget.

    Returns:
        The backend response.

    Raises:
        instaloader.exceptions.ConnectionException: When the extraction budget is exhausted.
    """
    remaining = context.remaining_request_budget()
    if remaining <= 0:
        msg = "Instagram extraction request budget expired."
        raise instaloader.exceptions.ConnectionException(msg)
    kwargs["timeout"] = min(remaining, context.request_timeout)
    return request(*args, **kwargs)


class FailFastRateController(instaloader.RateController):
    """Leave rate-limited extraction to the next strategy instead of sleeping in a worker."""

    def sleep(self, secs: float) -> None:
        """Reject a backend wait that would consume the worker execution budget.

        Raises:
            instaloader.exceptions.ConnectionException: When Instagram requires a wait.
        """
        if secs > 0:
            msg = "Instagram extraction requires a rate-limit wait."
            raise instaloader.exceptions.ConnectionException(msg)


class PrivateInstaloaderContext(instaloader.InstaloaderContext):
    """Keep backend retry diagnostics out of stderr and retained error strings."""

    def __init__(self, *, quiet: bool = True, user_agent: str | None = None) -> None:
        self._request_deadline: float | None = None
        super().__init__(
            quiet=quiet,
            user_agent=user_agent,
            sleep=False,
            max_connection_attempts=1,
            request_timeout=EXTRACTION_TIMEOUT_SECONDS,
            rate_controller=FailFastRateController,
        )

    def error(self, msg: str, repeat_at_end: bool = True) -> None:  # noqa: FBT001, FBT002
        """Emit a stable operation identifier without the backend payload."""
        logger.warning("Instagram backend request failed")

    def arm_request_budget(self, budget_seconds: float) -> None:
        """Bound this context's cumulative request time to one extraction budget."""
        self._request_deadline = monotonic() + budget_seconds

    def remaining_request_budget(self) -> float:
        """Return remaining request time, or the per-request timeout before arming."""
        if self._request_deadline is None:
            return self.request_timeout
        return self._request_deadline - monotonic()

    def get_anonymous_session(self) -> requests.Session:
        """Return the default session with every request sharing one extraction deadline."""
        session = super().get_anonymous_session()
        session.request = partial(_bounded_request, self, session.request)
        return session


class InstagramService(BaseMediaClient):
    """Service to interact with Instagram using instaloader and config-driven browser fallback."""

    MIN_DESCRIPTION_LENGTH: ClassVar[int] = 5

    def __init__(
        self,
        config: ProviderConfig = INSTAGRAM_CONFIG,
        settings: AppSettings | None = None,
    ) -> None:
        """Initialize with required services."""
        super().__init__(config=config)
        self._settings: AppSettings | None = settings
        self._context = PrivateInstaloaderContext(quiet=True, user_agent=self.DEFAULT_HEADERS["User-Agent"])
        self._session_initialized = False

    @classmethod
    def sync(cls, settings: AppSettings | None = None) -> Self:
        """Create a client instance for synchronous worker extraction."""
        return cls(INSTAGRAM_CONFIG, settings)

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

    def _ensure_session_sync(self, sessionid: str | None) -> None:
        """Ensure instaloader session is initialized with cookie synchronously."""
        if self._session_initialized or not sessionid:
            return

        try:
            self._context._session.cookies.set(  # noqa: SLF001
                "sessionid",
                sessionid,
                domain=".instagram.com",
            )
            self._session_initialized = True
        except ValueError, TypeError:
            msg = "Provider browser session is unavailable."
            raise MediaExtractionError(msg) from None

    def _extract_non_browser_fallback_sync(self, url: str, http: SyncHTTPPort) -> RichMediaPayload | None:
        """Run synchronous instaloader extraction directly in the media stage without an event loop."""
        if match := self._config.pattern.search(url):
            return self._extract_via_instaloader_sync(url, match)
        return None

    def _extract_via_instaloader_sync(
        self,
        url: str,
        match: re.Match[str],
    ) -> RichMediaPayload:
        """Fetch Instagram data using instaloader synchronously."""
        shortcode = match.group("id")
        if self._settings and self._settings.instagram_sessionid:
            self._ensure_session_sync(self._settings.instagram_sessionid)

        self._context.arm_request_budget(EXTRACTION_TIMEOUT_SECONDS)
        try:
            post = instaloader.Post.from_shortcode(self._context, shortcode)
        except instaloader.exceptions.InstaloaderException, OSError, ValueError:
            logger.exception("Provider backend extraction failed")
            msg = "Provider backend extraction failed."
            raise MediaExtractionError(msg) from None

        return self._build_instaloader_payload(post, url)

    def _post_caption(self, post: instaloader.Post) -> str:
        """Preserve usable cleaned captions and original short-caption/type fallbacks."""
        raw_text = post.caption or ""
        clean_desc = clean_caption_text(raw_text, strip_hashtags=True)
        if len(clean_desc) >= self.MIN_DESCRIPTION_LENGTH:
            return clean_desc
        if raw_text:
            return raw_text
        return f"Instagram {post.typename.replace('Graph', '').lower()}"

    def _build_instaloader_payload(self, post: instaloader.Post, url: str) -> RichMediaPayload:
        """Map fetched post metadata after the backend request succeeds."""
        display_text = self._post_caption(post)
        return self._require_usable_payload(
            RichMediaPayload(
                author_name=post.owner_profile.full_name or post.owner_username,
                author_handle=post.owner_username,
                author_url=f"https://www.instagram.com/{post.owner_username}/",
                content=display_text,
                footer_text=self._build_metrics_footer(
                    ("❤️", post.likes),
                    ("💬", post.comments),
                    ("👁️", post.video_view_count if post.is_video else None),
                    ("▶️", getattr(post, "video_play_count", None) if post.is_video else None),
                    prefix="📊 ",
                    include_zero=True,
                ),
                original_url=url,
                media_items=self._extract_media_items(post),
            )
        )

    def _extract_media_items(self, post: instaloader.Post) -> list[MediaItem]:
        """Extract media items from post, handling carousels."""
        if post.typename == "GraphSidecar":
            return [
                MediaItem(url=node.video_url or "", is_video=True)
                if node.is_video
                else MediaItem(url=node.display_url, is_video=False)
                for node in post.get_sidecar_nodes()
            ]
        if post.is_video:
            return [MediaItem(url=post.video_url or "", is_video=True)]
        return [MediaItem(url=post.url, is_video=False)]
