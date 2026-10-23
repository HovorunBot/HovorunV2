"""Application service for TikTok media extraction."""

import re
from typing import ClassVar

import aiohttp

from hovorunv2.application.clients.base import BaseMediaClient, ProviderConfig
from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.ports import BrowserPort
from hovorunv2.domain.enums import CommandName

TIKTOK_CONFIG = ProviderConfig(
    name=CommandName.TIKTOK,
    strip_hashtags=True,
    pattern=re.compile(
        r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/(?:(?P<type>v|t|video|photo)/|(?P<username>[@\w.-]+)/)?"
        r"(?:(?P<type_alt>video|photo)/)?(?P<id>[\w-]+)/?(?:(?<=/)\w+/?)*(?=[^\w/]|$)"
    ),
)


class TikTokService(BaseMediaClient):
    """Service to interact with TikTok and process video/photo links."""

    API_URL: ClassVar[str] = "https://www.tikwm.com/api/"
    SUCCESS_CODE: ClassVar[int] = 0

    def __init__(self, http: aiohttp.ClientSession, browser: BrowserPort) -> None:
        """Initialize with HTTP session and browser adapter."""
        super().__init__(config=TIKTOK_CONFIG, http=http, browser=browser)

    async def _extract_raw_payload(
        self,
        url: str,
        match: re.Match[str],
    ) -> RichMediaPayload | None:
        """Fetch TikTok data and construct raw RichMediaPayload."""
        data = await self._fetch_json(self.API_URL, params={"url": url})
        if not isinstance(data, dict) or data.get("code") != self.SUCCESS_CODE:
            return None

        video_data = data.get("data")
        if not isinstance(video_data, dict) or not video_data:
            return None

        if images := video_data.get("images"):
            media_items = [MediaItem(url=img, is_video=False) for img in images]
        else:
            video_url = video_data.get("play") or video_data.get("hdplay") or video_data.get("wmplay", "")
            media_items = [MediaItem(url=video_url, is_video=True)] if video_url else []

        author = video_data.get("author", {})
        handle = author.get("unique_id", "unknown")
        return RichMediaPayload(
            author_name=author.get("nickname", "Unknown"),
            author_handle=handle,
            author_url=f"https://www.tiktok.com/@{handle}",
            content=video_data.get("title", "No Title"),
            footer_text=self._build_metrics_footer(
                ("❤️", video_data.get("digg_count", 0)),
                ("👁️", video_data.get("play_count", 0)),
                include_zero=True,
            ),
            original_url=url,
            media_items=media_items,
        )
