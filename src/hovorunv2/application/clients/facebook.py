"""Application service for Facebook media extraction."""

import html
import re
from http import HTTPStatus
from typing import TYPE_CHECKING

from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.utils import extract_og_metadata
from hovorunv2.infrastructure.logger import get_logger

if TYPE_CHECKING:
    import aiohttp

    from hovorunv2.application.media.extractor import MediaExtractor
    from hovorunv2.application.services.translation_service import TranslationService

logger = get_logger(__name__)


class FacebookService:
    """Service to interact with Facebook and process posts."""

    # Broad pattern to catch various Facebook link formats
    PATTERN = re.compile(
        r"https?://(?:[\w-]+\.)?facebook\.com/(?:[^/]+/posts/\d+|groups/[^/]+/posts/\d+|"
        r"permalink\.php|photo\.php|reel/[\w-]+|watch/|videos/[\w-]+|share/[vp]/[\w-]+|story\.php|(?P<id>\w+))"
    )

    def __init__(self, translation_service: TranslationService, media_extractor: MediaExtractor) -> None:
        """Initialize with required services."""
        self._translation_service = translation_service
        self._media_extractor = media_extractor

    async def extract_payload(
        self, session: aiohttp.ClientSession, url: str, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch Facebook data and construct a RichMediaPayload."""
        # 1. Try OG tags first (fast and covers text/photos)
        headers = {"User-Agent": "facebookexternalhit/1.1"}
        try:
            async with session.get(url, headers=headers, timeout=10) as response:
                if response.status == HTTPStatus.OK:
                    html_content = await response.text()
                    metadata = extract_og_metadata(html_content)
                else:
                    logger.warning("Facebook fetch error: HTTP %d for %s", response.status, url)
                    metadata = {}
        except Exception:
            logger.exception("Failed to fetch Facebook URL %s", url)
            metadata = {}

        if not metadata:
            # Fallback to yt-dlp if OG fails
            return await self._media_extractor.extract_payload(session, url, chat_id, platform)

        # 2. Extract info from metadata
        is_video = metadata.get("type") == "video" or "video" in metadata
        content = metadata.get("description") or metadata.get("title") or ""
        content = html.escape(content)

        # Translation
        trans_res = await self._translation_service.translate_if_needed(content, chat_id, platform, session)
        if trans_res:
            content += f"\n\n{trans_res.flag} <b>Translated:</b>\n{html.escape(trans_res.text)}"

        # 3. Handle Media
        media_items = []
        if is_video:
            # If it's a video, OG tags often provide a player URL, not a direct file.
            # Use yt-dlp to get the actual video file if possible.
            ytdlp_payload = await self._media_extractor.extract_payload(session, url, chat_id, platform)
            if ytdlp_payload:
                return ytdlp_payload

            # If yt-dlp fails, use OG video if it looks like a direct link
            video_url = metadata.get("video:secure_url") or metadata.get("video")
            if video_url:
                media_items.append(MediaItem(url=video_url, is_video=True))

        if not media_items and "image" in metadata:
            media_items.append(MediaItem(url=metadata["image"], is_video=False))

        # 4. Author and Footer
        title = metadata.get("title", "")
        author_name = "Facebook User"
        if " - " in title:
            author_name = title.split(" - ")[0]

        site_name = metadata.get("site_name", "Facebook")
        is_reel = "/reel" in url
        post_type_label = "reel" if is_reel else "post"

        footer = f'\n\n🔗 <a href="{url}">Original {post_type_label} on {site_name}</a>'

        return RichMediaPayload(
            author_name=html.escape(author_name),
            author_handle="facebook",
            author_url="https://www.facebook.com",
            content=content,
            footer_text=footer,
            original_url=url,
            media_items=media_items,
        )
