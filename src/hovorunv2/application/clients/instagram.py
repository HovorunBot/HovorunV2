"""Application service for Instagram media extraction using instaloader."""

import asyncio
import html
import re
from typing import TYPE_CHECKING

import instaloader

from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.utils import format_number
from hovorunv2.infrastructure.logger import get_logger

if TYPE_CHECKING:
    import aiohttp

    from hovorunv2.application.services.translation_service import TranslationService

logger = get_logger(__name__)


class InstagramService:
    """Service to interact with Instagram using instaloader."""

    PATTERN = re.compile(r"https?://(?:www\.)?instagram\.com/(?:reels?|p|tv)/(?P<id>[\w-]+)")

    def __init__(self, translation_service: TranslationService) -> None:
        """Initialize with required translation service and instaloader."""
        self._translation_service = translation_service
        self._loader = instaloader.Instaloader(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

    async def extract_payload(
        self, session: aiohttp.ClientSession, url: str, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch Instagram data and construct a RichMediaPayload."""
        match = self.PATTERN.search(url)
        if not match:
            return None

        shortcode = match.group("id")

        try:
            post = await asyncio.to_thread(instaloader.Post.from_shortcode, self._loader.context, shortcode)
        except Exception:
            logger.exception("Failed to fetch Instagram post: %s", shortcode)
            return None

        # Metadata
        author_name = post.owner_username
        author_handle = post.owner_username
        author_url = f"https://www.instagram.com/{author_name}/"

        raw_text = post.caption or ""
        # Remove hashtags for cleaner display if they are too many
        clean_desc = re.sub(r"#\w+", "", raw_text)
        clean_desc = re.sub(r"\s+", " ", clean_desc).strip()
        content = html.escape(clean_desc)

        trans_res = await self._translation_service.translate_if_needed(content, chat_id, platform, session)
        if trans_res:
            content += f"\n\n{trans_res.flag} <b>Translated:</b>\n{html.escape(trans_res.text)}"

        # Media items
        media_items = []
        if post.typename == "GraphSidecar":
            # Carousel
            for node in post.get_sidecar_nodes():
                if node.is_video:
                    media_items.append(MediaItem(url=node.video_url, is_video=True))
                else:
                    media_items.append(MediaItem(url=node.display_url, is_video=False))
        elif post.is_video:
            media_items.append(MediaItem(url=post.video_url, is_video=True))
        else:
            media_items.append(MediaItem(url=post.display_url, is_video=False))

        likes = post.likes
        comments = post.comments
        views = post.video_view_count if post.is_video else None

        is_reel = "/reel" in url
        post_type_label = "reel" if is_reel else "post"

        stats = [f"❤️ {format_number(likes)}", f"💬 {format_number(comments)}"]
        if views is not None:
            stats.insert(1, f"👁️ {format_number(views)}")

        footer = f"\n\n{' | '.join(stats)}\n"
        footer += f'🔗 <a href="{url}">Original {post_type_label}</a>'

        return RichMediaPayload(
            author_name=html.escape(author_name),
            author_handle=html.escape(author_handle),
            author_url=author_url,
            content=content,
            footer_text=footer,
            original_url=url,
            media_items=media_items,
        )
