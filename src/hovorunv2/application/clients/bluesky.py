"""Application service for Bluesky media extraction."""

import re
from typing import Any, ClassVar

import aiohttp

from hovorunv2.application.clients.base import BaseMediaClient, ProviderConfig
from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.ports import BrowserPort
from hovorunv2.domain.enums import CommandName

BLUESKY_CONFIG = ProviderConfig(
    name=CommandName.BLUESKY,
    redirect_pattern=re.compile(r"go\.bsky\.app"),
    pattern=re.compile(r"https?://(?:www\.|go\.)?bsky\.app/(?:profile/(?P<handle>[\w.-]+)/post/)?(?P<rkey>[\w.-]+)"),
)


class BlueskyService(BaseMediaClient):
    """Service to interact with Bluesky and process posts."""

    API_BASE_URL: ClassVar[str] = "https://public.api.bsky.app/xrpc"

    def __init__(self, http: aiohttp.ClientSession, browser: BrowserPort) -> None:
        """Initialize with HTTP session and browser adapter."""
        super().__init__(config=BLUESKY_CONFIG, http=http, browser=browser)

    async def _extract_raw_payload(self, url: str, match: re.Match[str]) -> RichMediaPayload | None:
        """Fetch Bluesky thread data and construct raw RichMediaPayload."""
        handle = match.group("handle")
        if not handle:
            return None

        did = handle if handle.startswith("did:") else await self._resolve_did(handle)
        if not did:
            return None

        at_uri = f"at://{did}/app.bsky.feed.post/{match.group('rkey')}"
        thread_data = await self._fetch_json(
            f"{self.API_BASE_URL}/app.bsky.feed.getPostThread", params={"uri": at_uri, "depth": 0}
        )
        if not isinstance(thread_data, dict) or "thread" not in thread_data:
            return None

        post = thread_data["thread"]["post"]
        record = post.get("record", {})
        author = post.get("author", {})
        embed = post.get("embed", {})

        author_name = author.get("displayName") or author.get("handle") or "Unknown"
        author_handle = author.get("handle") or "unknown"

        return RichMediaPayload(
            author_name=author_name,
            author_handle=author_handle,
            author_url=f"https://bsky.app/profile/{author_handle}",
            content=record.get("text", ""),
            footer_text=self._build_metrics_footer(
                ("💬", post.get("replyCount", 0)),
                ("🔄", post.get("repostCount", 0)),
                ("❤️", post.get("likeCount", 0)),
                include_zero=True,
            ),
            original_url=url,
            media_items=self._extract_media_from_embed(embed),
            quoted_payload=self._extract_quote(embed),
        )

    async def _resolve_did(self, handle: str) -> str | None:
        """Resolve Bluesky handle to DID."""
        profile = await self._fetch_json(f"{self.API_BASE_URL}/app.bsky.actor.getProfile", params={"actor": handle})
        return profile.get("did") if isinstance(profile, dict) else None

    def _get_record_view(self, embed: dict[str, Any]) -> dict[str, Any] | None:
        """Retrieve the record view from the embed if present."""
        match embed.get("$type"):
            case "app.bsky.embed.record#view":
                return embed.get("record")
            case "app.bsky.embed.recordWithMedia#view":
                return embed.get("record", {}).get("record")
            case _:
                return None

    def _extract_quote(self, embed: dict[str, Any]) -> RichMediaPayload | None:
        """Extract quoted post payload if present in the embed."""
        record_view = self._get_record_view(embed)
        if not record_view or record_view.get("$type") not in (
            "app.bsky.feed.post#view",
            "app.bsky.embed.record#viewRecord",
        ):
            return None

        quote_author = record_view.get("author", {})
        quote_record = record_view.get("record") or record_view.get("value") or {}
        quoted_media = self._extract_media_from_embed(record_view.get("embed", {}))
        if not quoted_media and "embeds" in record_view:
            for emb in record_view["embeds"]:
                quoted_media.extend(self._extract_media_from_embed(emb))

        handle = quote_author.get("handle", "unknown")
        return RichMediaPayload(
            author_name=quote_author.get("displayName") or handle,
            author_handle=handle,
            author_url=f"https://bsky.app/profile/{handle}",
            content=quote_record.get("text", ""),
            media_items=quoted_media,
        )

    def _extract_media_from_embed(self, embed: dict[str, Any]) -> list[MediaItem]:
        """Extract media items from a Bluesky embed."""
        items: list[MediaItem] = []
        match embed.get("$type"):
            case "app.bsky.embed.recordWithMedia#view":
                items.extend(self._extract_media_from_embed(embed.get("media", {})))
            case "app.bsky.embed.images#view":
                items.extend(MediaItem(url=i.get("fullsize"), is_video=False) for i in embed.get("images", []))
            case "app.bsky.embed.video#view" if playlist := embed.get("playlist"):
                items.append(MediaItem(url=str(playlist), is_video=True))
            case _:
                pass
        return items
