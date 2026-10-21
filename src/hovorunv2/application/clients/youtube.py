"""Application service for YouTube media and community post extraction."""

import json
import re
from typing import Any, ClassVar

import aiohttp

from hovorunv2.application.clients.base import BaseMediaClient, OGConfig, ProviderConfig
from hovorunv2.application.data.constants import CommandName
from hovorunv2.application.dtos import MediaItem, RichMediaPayload

YOUTUBE_CONFIG = ProviderConfig(
    name=CommandName.YOUTUBE,
    platform_name="YouTube",
    pattern=re.compile(
        r"https?://(?:(?:www|m)\.)?(?:"
        r"youtube\.com/(?:"
        r"shorts/(?P<short_id>[\w-]+)|"
        r"watch\?(?:[^\s#]*&)?v=(?P<video_id>[\w-]+)|"
        r"post/(?P<post_id>[\w-]+)|"
        r"channel/[\w-]+/community\?(?:[^\s#]*&)?lb=(?P<community_id>[\w-]+)"
        r")|"
        r"youtu\.be/(?P<youtu_be_id>[\w-]+)"
        r")"
    ),
    post_url_pattern=re.compile(
        r"https?://(?:(?:www|m)\.)?youtube\.com/(?:"
        r"post/(?P<post_id>[\w-]+)|"
        r"channel/[\w-]+/community\?(?:[^\s#]*&)?lb=(?P<community_id>[\w-]+)"
        r")"
    ),
    og_config=OGConfig(
        default_author="YouTube Channel",
        author_handle="youtube",
        author_url="https://www.youtube.com",
        title_suffix=" - YouTube",
    ),
)


class YoutubeService(BaseMediaClient):
    """Service to interact with YouTube videos, Shorts, and Community posts."""

    CONFIG: ClassVar[ProviderConfig] = YOUTUBE_CONFIG

    async def _extract_raw_payload(
        self,
        session: aiohttp.ClientSession,
        url: str,
        match: re.Match[str],
    ) -> RichMediaPayload | None:
        """Extract YouTube Community post from ytInitialData or fall back to OG config."""
        html_content = await self._fetch_html(session, url)
        if not html_content:
            return None

        if renderer := self._extract_post_renderer(html_content):
            return self._build_payload_from_renderer(renderer, url)

        assert self._config.og_config is not None
        return self._parse_og_html(html_content, url, self._config.og_config)

    def _extract_post_renderer(self, html_content: str) -> dict[str, Any] | None:
        """Extract backstagePostRenderer from ytInitialData in page HTML."""
        match = re.search(r"(?:var\s+ytInitialData|window\[['\"]ytInitialData['\"]\])\s*=\s*(\{.*?\});", html_content)
        if not match:
            return None

        try:
            data = json.loads(match.group(1))
        except Exception:
            return None

        found = self._find_key_in_json(data, "backstagePostRenderer")
        return found if isinstance(found, dict) else None

    def _build_payload_from_renderer(self, renderer: dict[str, Any], url: str) -> RichMediaPayload | None:
        """Construct raw RichMediaPayload from YouTube backstagePostRenderer."""
        author_runs = renderer.get("authorText", {}).get("runs", [])
        author_name = "".join(r.get("text", "") for r in author_runs).strip() or "YouTube Channel"

        author_path = (
            renderer.get("authorEndpoint", {})
            .get("commandMetadata", {})
            .get("webCommandMetadata", {})
            .get("url", "")
        )
        author_handle = author_path.lstrip("/@") if author_path else author_name
        author_url = f"https://www.youtube.com{author_path}" if author_path else "https://www.youtube.com"

        content_runs = renderer.get("contentText", {}).get("runs", [])
        raw_text = "".join(r.get("text", "") for r in content_runs).strip()
        media_items = self._extract_attachment_media(renderer.get("backstageAttachment", {}))

        if not raw_text and not media_items:
            return None

        likes = (
            renderer.get("voteCount", {}).get("simpleText")
            or "".join(r.get("text", "") for r in renderer.get("voteCount", {}).get("runs", [])).strip()
        )

        return RichMediaPayload(
            author_name=author_name,
            author_handle=author_handle,
            author_url=author_url,
            content=raw_text,
            footer_text=self._build_metrics_footer(("❤️", likes)),
            original_url=url,
            media_items=media_items,
        )

    def _extract_attachment_media(self, attachment: dict[str, Any]) -> list[MediaItem]:
        """Extract single or multi-image attachments from backstageAttachment."""
        items: list[MediaItem] = []
        if not attachment:
            return items

        renderers = []
        if single_img := attachment.get("backstageImageRenderer"):
            renderers.append(single_img)
        if multi_img := attachment.get("postMultiImageRenderer"):
            renderers.extend(
                entry.get("backstageImageRenderer", {}) for entry in multi_img.get("images", [])
            )

        for img_renderer in renderers:
            thumbnails = img_renderer.get("image", {}).get("thumbnails", [])
            if thumbnails and isinstance(thumbnails[-1], dict) and (img_url := thumbnails[-1].get("url")):
                items.append(MediaItem(url=str(img_url), is_video=False))

        return items
