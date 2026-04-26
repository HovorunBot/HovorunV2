"""Application service for Bluesky media extraction."""

import html
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.utils import format_number
from hovorunv2.infrastructure.logger import get_logger

if TYPE_CHECKING:
    import aiohttp

    from hovorunv2.application.services.translation_service import TranslationService

logger = get_logger(__name__)


class BlueskyService:
    """Service to interact with Bluesky and process posts."""

    API_BASE_URL = "https://public.api.bsky.app/xrpc"

    # Pattern to catch Bluesky post links
    # https://bsky.app/profile/handle/post/rkey or https://go.bsky.app/announcement
    PATTERN = re.compile(r"https?://(?:www\.|go\.)?bsky\.app/(?:profile/(?P<handle>[\w.-]+)/post/)?(?P<rkey>[\w.-]+)")

    def __init__(self, translation_service: TranslationService) -> None:
        """Initialize with required services."""
        self._translation_service = translation_service

    async def extract_payload(
        self, session: aiohttp.ClientSession, url: str, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch Bluesky data and construct a RichMediaPayload."""
        actual_url = url
        if "go.bsky.app" in url:
            try:
                # Follow redirect to get the actual post URL
                async with session.get(url, allow_redirects=True, timeout=10) as resp:
                    actual_url = str(resp.url)
            except Exception:
                logger.exception("Failed to follow Bluesky redirect for %s", url)
                return None

        match = self.PATTERN.search(actual_url)
        if not match or not match.group("handle"):
            # If still no handle (e.g. invalid URL after redirect), abort
            return None

        handle = match.group("handle")
        rkey = match.group("rkey")

        # 1. Resolve handle to DID (if handle is not a DID)
        did = handle
        if not handle.startswith("did:"):
            profile = await self._get_profile(session, handle)
            if not profile:
                return None
            did = profile.get("did")
            if not did:
                return None
        else:
            profile = await self._get_profile(session, handle)

        # 2. Get the post thread
        at_uri = f"at://{did}/app.bsky.feed.post/{rkey}"
        thread_data = await self._get_post_thread(session, at_uri)
        if not thread_data or "thread" not in thread_data:
            return None

        post = thread_data["thread"]["post"]
        record = post.get("record", {})
        author = post.get("author", {})

        # 3. Extract content
        raw_text = record.get("text", "")
        content = html.escape(raw_text)

        # Translation
        trans_res = await self._translation_service.translate_if_needed(content, chat_id, platform, session)
        if trans_res:
            content += f"\n\n{trans_res.flag} <b>Translated:</b>\n{html.escape(trans_res.text)}"

        # 4. Handle Media
        media_items = []
        embed = post.get("embed", {})

        # Images
        if embed.get("$type") == "app.bsky.embed.images#view":
            media_items.extend(MediaItem(url=i.get("fullsize"), is_video=False) for i in embed.get("images", []))

        # Video
        elif embed.get("$type") == "app.bsky.embed.video#view":
            # Bluesky videos might need specific handling or direct playlist URLs
            playlist = embed.get("playlist")
            if playlist:
                media_items.append(MediaItem(url=playlist, is_video=True))

        # Handle external links or other embeds if needed

        # 5. Stats and Footer
        likes = post.get("likeCount", 0)
        reposts = post.get("repostCount", 0)
        replies = post.get("replyCount", 0)

        author_name = author.get("displayName") or author.get("handle") or "Unknown"
        author_handle = author.get("handle") or "unknown"
        author_url = f"https://bsky.app/profile/{author_handle}"

        footer = (
            f"\n\n💬 {format_number(replies)} | 🔄 {format_number(reposts)} | ❤️ {format_number(likes)}\n"
            f'🔗 <a href="{url}">Original post on Bluesky</a>'
        )

        return RichMediaPayload(
            author_name=html.escape(author_name),
            author_handle=html.escape(author_handle),
            author_url=author_url,
            content=content,
            footer_text=footer,
            original_url=url,
            media_items=media_items,
        )

    async def _get_profile(self, session: aiohttp.ClientSession, actor: str) -> dict[str, Any] | None:
        """Fetch actor profile from Bluesky."""
        url = f"{self.API_BASE_URL}/app.bsky.actor.getProfile"
        params = {"actor": actor}
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == HTTPStatus.OK:
                    return await resp.json()
                logger.warning("Failed to get Bluesky profile for %s: %d", actor, resp.status)
        except Exception:
            logger.exception("Error getting Bluesky profile")
        return None

    async def _get_post_thread(self, session: aiohttp.ClientSession, uri: str) -> dict[str, Any] | None:
        """Fetch post thread from Bluesky."""
        url = f"{self.API_BASE_URL}/app.bsky.feed.getPostThread"
        params = {"uri": uri, "depth": 0}
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == HTTPStatus.OK:
                    return await resp.json()
                logger.warning("Failed to get Bluesky post thread for %s: %d", uri, resp.status)
        except Exception:
            logger.exception("Error getting Bluesky post thread")
        return None
