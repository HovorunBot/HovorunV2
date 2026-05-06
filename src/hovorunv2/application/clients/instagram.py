"""Application service for Instagram media extraction using instaloader."""

import asyncio
import html
import re

import aiohttp
import instaloader

from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.services.translation_service import TranslationService
from hovorunv2.application.utils import format_number
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class InstagramService:
    """Service to interact with Instagram using instaloader."""

    MIN_DESCRIPTION_LENGTH: int = 5
    STATS_VIEWS_INDEX: int = 1

    PATTERN = re.compile(
        r"https?://(?:www\.)?instagram\.com/(?:[^/]+/)?(?:reels?|p|tv)/(?P<id>[A-Za-z0-9-_]+?)(?=[/?#\s]|$)"
    )

    def __init__(self, translation_service: TranslationService) -> None:
        """Initialize with required services."""
        self._translation_service = translation_service
        self._loader = instaloader.Instaloader(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        self._session_initialized = False

    async def _ensure_session(self, sessionid: str | None) -> None:
        """Ensure instaloader session is initialized with cookie if provided."""
        if self._session_initialized or not sessionid:
            return

        logger.info("Initializing Instaloader with sessionid cookie")
        try:
            # Instaloader handles cookies via context.load_session_from_file
            # or manual context._session.cookies update
            await asyncio.to_thread(
                self._loader.context._session.cookies.set,  # noqa: SLF001
                "sessionid",
                sessionid,
                domain=".instagram.com",
            )
            self._session_initialized = True
        except Exception:
            logger.exception("Failed to set Instaloader session cookie")

    async def extract_payload(
        self, session: aiohttp.ClientSession, url: str, chat_id: int, platform: str, sessionid: str | None = None
    ) -> RichMediaPayload | None:
        """Fetch Instagram data and construct a RichMediaPayload."""
        match = self.PATTERN.search(url)
        if not match:
            return None

        shortcode = match.group("id")

        if sessionid:
            await self._ensure_session(sessionid)

        try:
            post = await asyncio.to_thread(instaloader.Post.from_shortcode, self._loader.context, shortcode)
        except Exception as e:
            logger.warning("Instaloader failed for %s (%s)", shortcode, str(e))
            return None

        content = await self._build_content(post, chat_id, platform, session)

        return RichMediaPayload(
            author_name=html.escape(post.owner_profile.full_name or post.owner_username),
            author_handle=html.escape(post.owner_username),
            author_url=f"https://www.instagram.com/{post.owner_username}/",
            content=content,
            footer_text=self._build_footer(post),
            original_url=url,
            media_items=self._extract_media_items(post),
        )

    async def _build_content(
        self, post: instaloader.Post, chat_id: int, platform: str, session: aiohttp.ClientSession
    ) -> str:
        """Construct and translate post caption."""
        raw_text = post.caption or ""
        clean_desc = re.sub(r"#\w+", "", raw_text)
        clean_desc = re.sub(r"\s+", " ", clean_desc).strip()

        display_text = (
            clean_desc
            if len(clean_desc) >= self.MIN_DESCRIPTION_LENGTH
            else (raw_text or f"Instagram {post.typename.replace('Graph', '').lower()}")
        )
        content = html.escape(display_text)

        trans_res = await self._translation_service.translate_if_needed(content, chat_id, platform, session)
        if trans_res:
            content += f"\n\n{trans_res.flag} <b>Translated:</b>\n{html.escape(trans_res.text)}"
        return content

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

    def _build_footer(self, post: instaloader.Post) -> str:
        """Format post statistics into a footer string."""
        views = post.video_view_count if post.is_video else None
        plays = getattr(post, "video_play_count", None) if post.is_video else None

        stats = [f"❤️ {format_number(post.likes)}", f"💬 {format_number(post.comments)}"]
        if views is not None:
            stats.append(f"👁️ {format_number(views)}")
        if plays is not None:
            stats.append(f"▶️ {format_number(plays)}")

        return f"📊 {' | '.join(stats)}"
