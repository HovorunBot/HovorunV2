"""Application service for Bluesky media extraction."""

import html
import re
from http import HTTPStatus
from typing import Any

import aiohttp

from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.services.translation_service import TranslationService
from hovorunv2.application.utils import format_number
from hovorunv2.infrastructure.logger import get_logger

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

    async def _resolve_url(self, session: aiohttp.ClientSession, url: str) -> str | None:
        """Resolve short URLs like go.bsky.app to the actual post URL."""
        if "go.bsky.app" not in url:
            return url
        try:
            async with session.get(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return str(resp.url)
        except Exception:
            logger.exception("Failed to follow Bluesky redirect for %s", url)
            return None

    async def _resolve_did(self, session: aiohttp.ClientSession, handle: str) -> str | None:
        """Resolve handle to DID if needed."""
        if handle.startswith("did:"):
            return handle
        profile = await self._get_profile(session, handle)
        return profile.get("did") if profile else None

    def _get_record_view(self, embed: dict[str, Any]) -> dict[str, Any] | None:
        """Retrieve the record view from the embed if present."""
        match embed.get("$type"):
            case "app.bsky.embed.record#view":
                return embed.get("record")
            case "app.bsky.embed.recordWithMedia#view":
                return embed.get("record", {}).get("record")
            case _:
                return None

    async def _handle_quote(
        self,
        session: aiohttp.ClientSession,
        embed: dict[str, Any],
        chat_id: int,
        platform: str,
    ) -> tuple[RichMediaPayload | None, list[MediaItem], str]:
        """Process quoted post if present in the embed."""
        record_view = self._get_record_view(embed)

        if not record_view:
            return None, [], ""

        if record_view.get("$type") != "app.bsky.feed.post#view":
            return None, [], ""

        quote_author = record_view.get("author", {})
        quote_record = record_view.get("record", {})
        quote_text = html.escape(quote_record.get("text", ""))

        quote_trans = await self._translation_service.translate_if_needed(quote_text, chat_id, platform, session)
        if quote_trans:
            quote_text += f"\n\n{quote_trans.flag} <b>Translated:</b>\n{html.escape(quote_trans.text)}"

        quoted_media = self._extract_media_from_embed(record_view.get("embed", {}))
        info_note = "\n\nℹ️️ <i>Post includes quoted media</i>" if quoted_media else ""  # noqa: RUF001

        payload = RichMediaPayload(
            author_name=html.escape(quote_author.get("displayName") or quote_author.get("handle", "Unknown")),
            author_handle=html.escape(quote_author.get("handle", "unknown")),
            author_url=f"https://bsky.app/profile/{quote_author.get('handle', 'unknown')}",
            content=quote_text,
            media_items=quoted_media,
        )
        return payload, quoted_media, info_note

    async def extract_payload(
        self, session: aiohttp.ClientSession, url: str, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch Bluesky data and construct a RichMediaPayload."""
        at_uri = await self._resolve_post_at_uri(session, url)
        if not at_uri:
            return None

        thread_data = await self._get_post_thread(session, at_uri)
        if not thread_data or "thread" not in thread_data:
            logger.error("Failed to fetch Bluesky post thread for %s", at_uri)
            return None

        post = thread_data["thread"]["post"]
        record = post.get("record", {})
        author = post.get("author", {})

        content = html.escape(record.get("text", ""))
        trans_res = await self._translation_service.translate_if_needed(content, chat_id, platform, session)
        if trans_res:
            content += f"\n\n{trans_res.flag} <b>Translated:</b>\n{html.escape(trans_res.text)}"

        embed = post.get("embed", {})
        media_items = self._extract_media_from_embed(embed)

        quoted_payload, quoted_media, info_note = await self._handle_quote(session, embed, chat_id, platform)
        media_items.extend(quoted_media)
        content += info_note

        author_name = author.get("displayName") or author.get("handle") or "Unknown"
        author_handle = author.get("handle") or "unknown"

        return RichMediaPayload(
            author_name=html.escape(author_name),
            author_handle=html.escape(author_handle),
            author_url=f"https://bsky.app/profile/{author_handle}",
            content=content,
            footer_text=self._build_footer(post),
            original_url=url,
            media_items=media_items,
            quoted_payload=quoted_payload,
        )

    async def _resolve_post_at_uri(self, session: aiohttp.ClientSession, url: str) -> str | None:
        """Resolve handle and rkey from URL to an AT URI."""
        actual_url = await self._resolve_url(session, url)
        if not actual_url:
            logger.error("Failed to resolve Bluesky URL: %s", url)
            return None

        match = self.PATTERN.search(actual_url)
        if not match or not match.group("handle"):
            logger.error("Failed to parse Bluesky URL components from %s", actual_url)
            return None

        handle = match.group("handle")
        rkey = match.group("rkey")

        did = await self._resolve_did(session, handle)
        if not did:
            logger.error("Failed to resolve Bluesky DID for handle %s", handle)
            return None

        return f"at://{did}/app.bsky.feed.post/{rkey}"

    def _build_footer(self, post: dict[str, Any]) -> str:
        """Build footer string with metrics."""
        likes = post.get("likeCount", 0)
        reposts = post.get("repostCount", 0)
        replies = post.get("replyCount", 0)
        return f"💬 {format_number(replies)} | 🔄 {format_number(reposts)} | ❤️ {format_number(likes)}"

    def _extract_media_from_embed(self, embed: dict[str, Any]) -> list[MediaItem]:
        """Extract media items from a Bluesky embed."""
        items = []
        match embed.get("$type"):
            case "app.bsky.embed.recordWithMedia#view":
                items.extend(self._extract_media_from_embed(embed.get("media", {})))
            case "app.bsky.embed.images#view":
                items.extend(MediaItem(url=i.get("fullsize"), is_video=False) for i in embed.get("images", []))
            case "app.bsky.embed.video#view" if playlist := embed.get("playlist"):
                assert isinstance(playlist, str), "Playlist URL should be a string"
                items.append(MediaItem(url=playlist, is_video=True))
            case "app.bsky.embed.record#view" | None:
                logger.info("No media found in Bluesky embed")
            case unknown_type:
                logger.info("Unknown or unhandled Bluesky embed type: %s", unknown_type)

        return items

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
