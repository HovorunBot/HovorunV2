"""Application service for YouTube media and community post extraction."""

import json
import re
from collections.abc import Sequence
from typing import Self

import aiohttp

from hovorunv2.application.jobs.http_ports import SyncHTTPPort
from hovorunv2.application.media.browser import BrowserPort
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.application.media.providers.base import BaseMediaClient, OGConfig, ProviderConfig
from hovorunv2.domain.enums import CommandName
from hovorunv2.utils.json_values import mapping, sequence, text

_YOUTUBE_OG_CONFIG = OGConfig(
    default_author="YouTube Channel",
    author_handle="youtube",
    author_url="https://www.youtube.com",
    title_suffix=" - YouTube",
)

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
    og_config=_YOUTUBE_OG_CONFIG,
)


class YoutubeService(BaseMediaClient):
    """Service to interact with YouTube videos, Shorts, and Community posts."""

    def __init__(self, http: aiohttp.ClientSession, browser: BrowserPort) -> None:
        """Initialize with HTTP session and browser adapter."""
        super().__init__(config=YOUTUBE_CONFIG, http=http, browser=browser)

    @classmethod
    def sync(cls) -> Self:
        """Create a client instance for synchronous worker extraction."""
        return cls.for_worker(YOUTUBE_CONFIG)

    async def _extract_raw_payload(
        self,
        url: str,
        match: re.Match[str],
    ) -> RichMediaPayload | None:
        """Extract YouTube Community post from ytInitialData or fall back to OG config."""
        fetch_url = self._post_fetch_url(match, url)
        html_content = await self._fetch_html(fetch_url)
        return self._parse_youtube_post_html(html_content, fetch_url)

    def _extract_raw_payload_sync(
        self,
        url: str,
        match: re.Match[str],
        http: SyncHTTPPort,
    ) -> RichMediaPayload | None:
        """Extract YouTube Community post synchronously from ytInitialData or fall back to OG config."""
        fetch_url = self._post_fetch_url(match, url)
        html_content = self._fetch_html_sync(http, fetch_url)
        return self._parse_youtube_post_html(html_content, fetch_url)

    def _post_fetch_url(self, match: re.Match[str], url: str) -> str:
        """Determine canonical post fetch URL from match groups."""
        post_id = match.groupdict().get("post_id") or match.groupdict().get("community_id")
        return f"https://www.youtube.com/post/{post_id}" if post_id else url

    def _parse_youtube_post_html(self, html_content: str, fetch_url: str) -> RichMediaPayload | None:
        """Parse YouTube community post from page HTML or OG tags."""
        if not html_content:
            return None

        if renderer := self._extract_post_renderer(html_content):
            return self._build_payload_from_renderer(renderer, fetch_url)

        return self._parse_og_html(html_content, fetch_url, _YOUTUBE_OG_CONFIG)

    def _extract_post_renderer(self, html_content: str) -> dict[str, object] | None:
        """Extract backstagePostRenderer or sharedPostRenderer from ytInitialData in page HTML."""
        data = self._read_initial_data(html_content)
        if data is None:
            return None

        if isinstance(found := self._find_key_in_json(data, "backstagePostRenderer"), dict):
            return found

        if isinstance(shared := self._find_key_in_json(data, "sharedPostRenderer"), dict):
            found_shared = self._find_key_in_json(shared, "backstagePostRenderer")
            return found_shared if isinstance(found_shared, dict) else shared

        return None

    def _read_initial_data(self, html_content: str) -> object:
        """Read the embedded JSON candidate without interpreting trailing page scripts."""
        marker = "ytInitialData"
        idx = html_content.find(marker)
        brace_idx = html_content.find("{", idx) if idx != -1 else -1
        if brace_idx == -1:
            return None

        try:
            return json.JSONDecoder().raw_decode(html_content[brace_idx:])[0]
        except ValueError, RecursionError:
            return None

    def _build_payload_from_renderer(self, renderer: dict[str, object], url: str) -> RichMediaPayload | None:
        """Construct raw RichMediaPayload from YouTube backstagePostRenderer."""
        author_runs = sequence(mapping(renderer.get("authorText")).get("runs"))
        author_name = self._join_runs(author_runs) or "YouTube Channel"

        author_path = text(
            mapping(
                mapping(mapping(renderer.get("authorEndpoint")).get("commandMetadata")).get("webCommandMetadata")
            ).get("url")
        )
        author_handle = author_path.lstrip("/@") if author_path else author_name
        author_url = f"https://www.youtube.com{author_path}" if author_path else "https://www.youtube.com"

        content_runs = sequence(mapping(renderer.get("contentText")).get("runs"))
        raw_text = self._join_runs(content_runs)
        media_items = self._extract_attachment_media(mapping(renderer.get("backstageAttachment")))

        if not raw_text and not media_items:
            return None

        vote_count = mapping(renderer.get("voteCount"))
        likes = text(vote_count.get("simpleText")) or self._join_runs(sequence(vote_count.get("runs")))

        return RichMediaPayload(
            author_name=author_name,
            author_handle=author_handle,
            author_url=author_url,
            content=raw_text,
            footer_text=self._build_metrics_footer(("❤️", likes)),
            original_url=url,
            media_items=media_items,
        )

    def _join_runs(self, runs: Sequence[object]) -> str:
        """Join provider text runs before trimming the complete author, caption or metric."""
        return "".join(text(mapping(run).get("text")) for run in runs).strip()

    def _extract_attachment_media(self, attachment: dict[str, object]) -> list[MediaItem]:
        """Extract single or multi-image attachments from backstageAttachment."""
        items: list[MediaItem] = []
        if not attachment:
            return items

        renderers: list[dict[str, object]] = []
        if single_img := attachment.get("backstageImageRenderer"):
            renderers.append(mapping(single_img))
        if multi_img := attachment.get("postMultiImageRenderer"):
            renderers.extend(
                mapping(mapping(entry).get("backstageImageRenderer"))
                for entry in sequence(mapping(multi_img).get("images"))
            )

        for renderer in renderers:
            thumbnails = sequence(mapping(renderer.get("image")).get("thumbnails"))
            if thumbnails and (img_url := text(mapping(thumbnails[-1]).get("url"))):
                items.append(MediaItem(url=img_url, is_video=False))

        return items
