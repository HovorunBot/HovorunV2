"""Application service for Bluesky media extraction."""

import re
from typing import ClassVar, Self

from hovorunv2.application.jobs.http_ports import SyncHTTPPort
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.application.media.provider_config import BLUESKY_CONFIG, ProviderConfig
from hovorunv2.application.media.providers.base import BaseMediaClient
from hovorunv2.utils.json_values import mapping, sequence, text

__all__ = ["BLUESKY_CONFIG", "BlueskyService"]


class BlueskyService(BaseMediaClient):
    """Service to interact with Bluesky and process posts."""

    API_BASE_URL: ClassVar[str] = "https://public.api.bsky.app/xrpc"

    def __init__(self, config: ProviderConfig = BLUESKY_CONFIG) -> None:
        """Initialize with provider configuration."""
        super().__init__(config=config)

    @classmethod
    def sync(cls) -> Self:
        """Create a client instance for synchronous worker extraction."""
        return cls(BLUESKY_CONFIG)

    def _extract_raw_payload_sync(
        self,
        url: str,
        match: re.Match[str],
        http: SyncHTTPPort,
    ) -> RichMediaPayload | None:
        """Fetch Bluesky thread data synchronously and construct raw RichMediaPayload."""
        handle = match.group("handle")
        if not handle:
            return None

        did = handle if handle.startswith("did:") else self._resolve_did_sync(handle, http)
        at_uri = f"at://{did}/app.bsky.feed.post/{match.group('rkey')}"
        thread_data = self._fetch_json_sync(
            http, f"{self.API_BASE_URL}/app.bsky.feed.getPostThread", params={"uri": at_uri, "depth": 0}
        )
        return self._parse_thread_data(thread_data, url)

    def _parse_thread_data(self, thread_data: object, url: str) -> RichMediaPayload | None:
        """Parse raw Bluesky thread data into RichMediaPayload."""
        if not isinstance(thread_data, dict) or "thread" not in thread_data:
            msg = "Provider returned unusable extraction metadata."
            raise MediaExtractionError(msg)

        post = mapping(mapping(thread_data.get("thread")).get("post"))
        if not post:
            return None

        author = mapping(post.get("author"))
        record = mapping(post.get("record"))
        embed = mapping(post.get("embed"))

        handle = text(author.get("handle"), "unknown")
        return RichMediaPayload(
            author_name=text(author.get("displayName"), handle),
            author_handle=handle,
            author_url=f"https://bsky.app/profile/{handle}",
            content=text(record.get("text")),
            footer_text=self._build_metrics_footer(
                ("🔁", post.get("repostCount", 0)),
                ("❤️", post.get("likeCount", 0)),
                ("💬", post.get("replyCount", 0)),
                include_zero=True,
            ),
            original_url=url,
            media_items=self._extract_media_from_embed(embed),
            quoted_payload=self._extract_quote(embed),
        )

    def _resolve_did_sync(self, handle: str, http: SyncHTTPPort) -> str:
        """Resolve Bluesky handle to DID synchronously."""
        profile = self._fetch_json_sync(
            http, f"{self.API_BASE_URL}/app.bsky.actor.getProfile", params={"actor": handle}
        )
        return self._parse_did(profile)

    def _parse_did(self, profile: object) -> str:
        did = profile.get("did") if isinstance(profile, dict) else None
        if not isinstance(did, str) or not did:
            msg = "Provider actor identity is unavailable."
            raise MediaExtractionError(msg)
        return did

    def _get_record_view(self, embed: dict[str, object]) -> dict[str, object] | None:
        """Retrieve the record view from the embed if present."""
        match text(embed.get("$type")):
            case "app.bsky.embed.record#view":
                return mapping(embed.get("record"))
            case "app.bsky.embed.recordWithMedia#view":
                return mapping(mapping(embed.get("record")).get("record"))
            case _:
                return None

    def _extract_quote(self, embed: dict[str, object]) -> RichMediaPayload | None:
        """Extract quoted post payload if present in the embed."""
        record_view = self._get_record_view(embed)
        if not record_view or text(record_view.get("$type")) not in (
            "app.bsky.feed.post#view",
            "app.bsky.embed.record#viewRecord",
        ):
            return None

        quote_author = mapping(record_view.get("author"))
        quote_record = mapping(record_view.get("record") or record_view.get("value"))
        quoted_media = self._extract_media_from_embed(mapping(record_view.get("embed")))
        if not quoted_media:
            for embedded in sequence(record_view.get("embeds")):
                quoted_media.extend(self._extract_media_from_embed(mapping(embedded)))

        handle = text(quote_author.get("handle"), "unknown")
        return RichMediaPayload(
            author_name=text(quote_author.get("displayName"), handle),
            author_handle=handle,
            author_url=f"https://bsky.app/profile/{handle}",
            content=text(quote_record.get("text")),
            media_items=quoted_media,
        )

    def _extract_media_from_embed(self, embed: dict[str, object]) -> list[MediaItem]:
        """Extract media items from a Bluesky embed."""
        items: list[MediaItem] = []
        match text(embed.get("$type")):
            case "app.bsky.embed.recordWithMedia#view":
                items.extend(self._extract_media_from_embed(mapping(embed.get("media"))))
            case "app.bsky.embed.images#view":
                for image in sequence(embed.get("images")):
                    url = text(mapping(image).get("fullsize"))
                    if url:
                        items.append(MediaItem(url=url, is_video=False))
            case "app.bsky.embed.video#view" if playlist := text(embed.get("playlist")):
                items.append(MediaItem(url=playlist, is_video=True))
            case _:
                pass
        return items
