"""Application service for Threads media extraction."""

import html
import re
import urllib.parse
from http import HTTPStatus
from typing import TYPE_CHECKING

from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.infrastructure.logger import get_logger

if TYPE_CHECKING:
    import aiohttp

    from hovorunv2.application.services.translation_service import TranslationService

logger = get_logger(__name__)


class ThreadsService:
    """Service to interact with Threads and process thread links."""

    PATTERN = re.compile(
        r"https?://(?:www\.)?threads\.(?P<tld>net|com)/(?:@[\w.-]+/)?(?:post|t)/(?P<post_id>[\w-]+)",
    )

    def __init__(self, translation_service: TranslationService) -> None:
        """Initialize with required translation service."""
        self._translation_service = translation_service

    async def extract_payload(
        self, session: aiohttp.ClientSession, url: str, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch native Threads data using Open Graph."""
        match = self.PATTERN.search(url)
        if not match:
            return None

        tld = match.group("tld")

        # Pretend to be a link preview scraper to get meta tags
        headers = {"User-Agent": "facebookexternalhit/1.1"}
        try:
            async with session.get(url, headers=headers, timeout=10) as response:
                if response.status != HTTPStatus.OK:
                    logger.warning("Threads fetch error: HTTP %d for %s", response.status, url)
                    return None
                html_content = await response.text()
        except Exception:
            logger.exception("Failed to fetch Threads URL %s", url)
            return None

        metadata = self._extract_metadata(html_content)
        if not metadata:
            logger.error("No metadata found for Threads URL: %s", url)
            return None

        # 1. Parse Text
        raw_text = metadata.get("description", "")
        content = html.escape(raw_text)

        trans_res = await self._translation_service.translate_if_needed(content, chat_id, platform, session)
        if trans_res:
            content += f"\n\n{trans_res.flag} <b>Translated:</b>\n{html.escape(trans_res.text)}"

        # 2. Parse Media (Prefer video, then image)
        media_urls = []
        is_video = False

        if "video:secure_url" in metadata:
            media_urls = [metadata["video:secure_url"]]
            is_video = True
        elif "video" in metadata:
            media_urls = [metadata["video"]]
            is_video = True
        elif "image" in metadata:
            media_urls = [metadata["image"]]

        # 3. Parse Author Info
        title = metadata.get("title", "")
        author_name = "Unknown"
        author_handle = "unknown"

        # Format: "Name (@handle) on Threads"  # noqa: ERA001
        author_match = re.search(r"^(.*?)\s+\(@(.*?)\)", title)
        if author_match:
            author_name = author_match.group(1).strip()
            author_handle = author_match.group(2).strip()

        # 4. Footer
        footer = f'\n\n🔗 <a href="{url}">Open thread</a>'

        return RichMediaPayload(
            author_name=html.escape(author_name),
            author_handle=html.escape(author_handle),
            author_url=f"https://www.threads.{tld}/@{urllib.parse.quote(author_handle)}",
            content=content,
            footer_text=footer,
            original_url=url,
            media_urls=media_urls,
            is_video=is_video,
        )

    def _extract_metadata(self, html_content: str) -> dict[str, str]:
        """Robustly extract OG and Twitter metadata from HTML."""
        metadata = {}
        # Match meta tags and extract key-value pairs
        for meta in re.finditer(r"<meta\s+([^>]+)>", html_content, re.IGNORECASE):
            body = meta.group(1)
            # Find property="og:..." or name="twitter:..." etc.
            key_m = re.search(r'(?:property|name)=["\'](?:og:|twitter:)?([^"\']+)["\']', body, re.IGNORECASE)
            val_m = re.search(r'content=["\']([^"\']+)["\']', body, re.IGNORECASE)

            if key_m and val_m:
                key = key_m.group(1).lower()
                val = html.unescape(val_m.group(1))
                if key not in metadata:
                    metadata[key] = val
        return metadata
