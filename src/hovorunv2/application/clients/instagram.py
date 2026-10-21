"""Application service for Instagram media extraction using instaloader and browser fallback."""

import asyncio
import re
from typing import Any, ClassVar

import aiohttp
import instaloader

from hovorunv2.application.clients.base import BaseMediaClient, BrowserFallbackConfig, OGConfig, ProviderConfig
from hovorunv2.application.data.constants import CommandName
from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.services.translation_service import TranslationService
from hovorunv2.application.utils import clean_caption_text
from hovorunv2.infrastructure.browser import BrowserService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)

INSTAGRAM_JS_EXTRACTOR = """
() => {
    const getMeta = (prop) => {
        const el = document.querySelector(`meta[property="${prop}"], meta[name="${prop}"]`);
        return el ? el.content : null;
    };

    let data = {
        video: getMeta('og:video') || getMeta('og:video:secure_url'),
        image: getMeta('og:image'),
        title: getMeta('og:title') || document.title,
        description: getMeta('og:description') || "",
        likes: null,
        views: null,
        comments: null
    };

    const findData = (obj) => {
        if (obj && obj.items && obj.items[0]) return obj.items[0];
        if (obj && obj.graphql && obj.graphql.shortcode_media) return obj.graphql.shortcode_media;
        return null;
    };

    const post = findData(window.__additionalDataLoaded && window.__additionalDataLoaded.feed) ||
                 findData(window._sharedData && window._sharedData.entry_data &&
                          window._sharedData.entry_data.PostPage &&
                          window._sharedData.entry_data.PostPage[0]);

    if (post) {
        data.title = data.title || (post.caption ? (post.caption.text || post.caption) : "");
        data.video = data.video || (post.video_url ||
                    (post.video_versions ? post.video_versions[0].url : null));
        data.image = data.image || (post.display_url ||
                    (post.image_versions2 ? post.image_versions2.candidates[0].url : null));
        data.likes = data.likes || (post.like_count ||
                    (post.edge_media_preview_like ? post.edge_media_preview_like.count : null));
        data.comments = data.comments || (post.comment_count ||
                    (post.edge_media_to_comment ? post.edge_media_to_comment.count : null));
        data.views = data.views || (post.video_view_count || post.view_count || null);
    }
    return data;
}
"""

INSTAGRAM_CONFIG = ProviderConfig(
    name=CommandName.INSTAGRAM,
    pattern=re.compile(
        r"https?://(?:www\.)?instagram\.com/(?:[^/]+/)?(?:reels?|p|tv)/(?P<id>[A-Za-z0-9-_]+?)(?=[/?#\s]|$)"
    ),
    browser_config=BrowserFallbackConfig(
        wait_selector="meta[property='og:image'], article, main",
        gate_buttons=("See Post", "Verify Age", "Переглянути допис", "Підтвердити вік"),
        js_extractor=INSTAGRAM_JS_EXTRACTOR,
        default_author="Instagram User",
        author_handle="instagram",
        author_url="https://www.instagram.com/",
        default_title="Instagram Post",
        default_footer="📷 Instagram",
        description_clean_pattern=re.compile(r'^.*?on Instagram:.*?"'),
    ),
    og_config=OGConfig(
        default_author="Instagram User",
        author_handle="instagram",
        author_url="https://www.instagram.com/",
        title_separator=" on Instagram: ",
    ),
)


class InstagramService(BaseMediaClient):
    """Service to interact with Instagram using instaloader and config-driven browser fallback."""

    CONFIG: ClassVar[ProviderConfig] = INSTAGRAM_CONFIG
    MIN_DESCRIPTION_LENGTH: ClassVar[int] = 5

    def __init__(
        self,
        translation_service: TranslationService,
        browser_service: BrowserService,
        settings: Settings,
    ) -> None:
        """Initialize with required services."""
        super().__init__(translation_service)
        self._browser_service = browser_service
        self._settings = settings
        self._loader = instaloader.Instaloader(user_agent=self.DEFAULT_HEADERS["User-Agent"])
        self._session_initialized = False

    def get_ytdlp_cookies(self) -> dict[str, str] | None:
        """Provide sessionid cookie to yt-dlp when configured."""
        return {"sessionid": self._settings.instagram_sessionid} if self._settings.instagram_sessionid else None

    def get_browser_cookies(self) -> list[dict[str, Any]]:
        """Provide sessionid cookie to headless browser fallback when configured."""
        if not self._settings.instagram_sessionid:
            return []
        return [
            {
                "name": "sessionid",
                "value": self._settings.instagram_sessionid,
                "domain": ".instagram.com",
                "path": "/",
            }
        ]

    async def _ensure_session(self, sessionid: str | None) -> None:
        """Ensure instaloader session is initialized with cookie if provided."""
        if self._session_initialized or not sessionid:
            return

        try:
            await asyncio.to_thread(
                self._loader.context._session.cookies.set,  # noqa: SLF001
                "sessionid",
                sessionid,
                domain=".instagram.com",
            )
            self._session_initialized = True
        except Exception:
            logger.exception("Failed to set Instaloader session cookie")

    async def extract_fallback(
        self,
        url: str,
        chat_id: int = 0,
        platform: str = "telegram",
        session: aiohttp.ClientSession | None = None,
    ) -> RichMediaPayload | None:
        """Try instaloader after yt-dlp fails, then fall back to headless browser."""
        if session is not None and (match := self._config.pattern.search(url)):
            raw_payload = await self._extract_via_instaloader(url, match)
            if raw_payload is not None:
                return await self._finalize_payload(raw_payload, url, chat_id, platform, session)

        return await super().extract_fallback(url, chat_id, platform, session)

    async def _extract_via_instaloader(
        self,
        url: str,
        match: re.Match[str],
    ) -> RichMediaPayload | None:
        """Fetch Instagram data using instaloader and construct raw RichMediaPayload."""
        shortcode = match.group("id")
        if self._settings.instagram_sessionid:
            await self._ensure_session(self._settings.instagram_sessionid)

        try:
            post = await asyncio.to_thread(instaloader.Post.from_shortcode, self._loader.context, shortcode)
        except Exception as e:
            logger.warning("Instaloader failed for %s (%s)", shortcode, str(e))
            return None

        raw_text = post.caption or ""
        clean_desc = clean_caption_text(raw_text, strip_hashtags=True)
        display_text = (
            clean_desc
            if len(clean_desc) >= self.MIN_DESCRIPTION_LENGTH
            else (raw_text or f"Instagram {post.typename.replace('Graph', '').lower()}")
        )

        return RichMediaPayload(
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
