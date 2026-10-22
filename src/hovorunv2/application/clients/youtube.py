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
        r"(?:(?:channel|c|user)/[\w-]+/|@[\w.-]+/)?post/(?P<post_id>[\w-]+)|"
        r"(?:(?:channel|c|user)/[\w-]+|@[\w.-]+)/community\?(?:[^\s#]*&)?lb=(?P<community_id>[\w-]+)"
        r")|"
        r"youtu\.be/(?P<youtu_be_id>[\w-]+)"
        r")"
    ),
    post_url_pattern=re.compile(
        r"https?://(?:(?:www|m)\.)?youtube\.com/(?:"
        r"(?:(?:channel|c|user)/[\w-]+/|@[\w.-]+/)?post/(?P<post_id>[\w-]+)|"
        r"(?:(?:channel|c|user)/[\w-]+|@[\w.-]+)/community\?(?:[^\s#]*&)?lb=(?P<community_id>[\w-]+)"
        r")"
    ),
    headers={
        "Cookie": "SOCS=CAESEwgDEgk2MTQ1NzU1MDQaAmVuIAEaBgiA_LyaBg",
    },
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
        post_id = match.groupdict().get("post_id") or match.groupdict().get("community_id")
        fetch_url = f"https://www.youtube.com/post/{post_id}" if post_id else url
        html_content = await self._fetch_html(session, fetch_url)
        if not html_content:
            return None

        if renderer := self._extract_post_renderer(html_content):
            return self._build_payload_from_renderer(renderer, fetch_url)

        assert self._config.og_config is not None
        return self._parse_og_html(html_content, fetch_url, self._config.og_config)

    def _extract_post_renderer(self, html_content: str) -> dict[str, Any] | None:
        """Extract backstagePostRenderer or sharedPostRenderer from ytInitialData in page HTML."""
        marker = "ytInitialData"
        idx = html_content.find(marker)
        brace_idx = html_content.find("{", idx) if idx != -1 else -1
        if brace_idx == -1:
            return None

        try:
            data, _ = json.JSONDecoder().raw_decode(html_content[brace_idx:])
        except Exception:
            return None

        if isinstance(found := self._find_key_in_json(data, "backstagePostRenderer"), dict):
            return found

        if isinstance(shared := self._find_key_in_json(data, "sharedPostRenderer"), dict):
            found_shared = self._find_key_in_json(shared, "backstagePostRenderer")
            return found_shared if isinstance(found_shared, dict) else shared

        return None

    def _build_payload_from_renderer(self, renderer: dict[str, Any], url: str) -> RichMediaPayload | None:
        """Construct raw RichMediaPayload from YouTube backstagePostRenderer."""
        author_runs = renderer.get("authorText", {}).get("runs", [])
        author_name = "".join(r.get("text", "") for r in author_runs).strip() or "YouTube Channel"

        author_path = (
            renderer.get("authorEndpoint", {}).get("commandMetadata", {}).get("webCommandMetadata", {}).get("url", "")
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
            renderers.extend(entry.get("backstageImageRenderer", {}) for entry in multi_img.get("images", []))

        for img_renderer in renderers:
            thumbnails = img_renderer.get("image", {}).get("thumbnails", [])
            if thumbnails and isinstance(thumbnails[-1], dict) and (img_url := thumbnails[-1].get("url")):
                items.append(MediaItem(url=str(img_url), is_video=False))

        return items
