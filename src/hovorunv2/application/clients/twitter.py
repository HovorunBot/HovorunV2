"""Application service for Twitter/X media extraction."""

import re
from typing import Any, ClassVar

import aiohttp

from hovorunv2.application.clients.base import BaseMediaClient, ProviderConfig
from hovorunv2.application.data.constants import CommandName
from hovorunv2.application.dtos import MediaItem, RichMediaPayload

TWITTER_CONFIG = ProviderConfig(
    name=CommandName.TWITTER,
    pattern=re.compile(
        r"https?://(?:www\.)?(?:api\.)?(?:x\.com|twitter\.com)/(?:\w+/status/|2/tweets/)(?P<post_id>\d+)"
    ),
)


class TwitterService(BaseMediaClient):
    """Service to interact with Twitter and process tweet links."""

    CONFIG: ClassVar[ProviderConfig] = TWITTER_CONFIG

    async def _extract_raw_payload(
        self, session: aiohttp.ClientSession, url: str, match: re.Match[str]
    ) -> RichMediaPayload | None:
        """Fetch tweet data from vxtwitter API and return raw RichMediaPayload."""
        tweet_data = await self._fetch_json(session, f"https://api.vxtwitter.com/i/status/{match.group('post_id')}")
        if not isinstance(tweet_data, dict) or "text" not in tweet_data:
            return None

        raw_text = tweet_data.get("text", "")
        if (qrt_url := tweet_data.get("qrtURL")) and qrt_url in raw_text:
            raw_text = raw_text.replace(qrt_url, "").strip()

        handle = tweet_data.get("user_screen_name", "unknown")
        return RichMediaPayload(
            author_name=tweet_data.get("user_name", "Unknown"),
            author_handle=handle,
            author_url=f"https://x.com/{handle}",
            content=raw_text,
            footer_text=self._build_metrics_footer(
                ("🔁", tweet_data.get("retweets", 0)),
                ("❤️", tweet_data.get("likes", 0)),
                ("💬", tweet_data.get("replies", 0)),
                include_zero=True,
            ),
            original_url=url,
            media_items=self._extract_media(tweet_data),
            quoted_payload=self._extract_quote(tweet_data),
        )

    def _extract_media(self, data: dict[str, Any]) -> list[MediaItem]:
        """Extract MediaItem list from vxtwitter media_extended."""
        return [
            MediaItem(url=m["url"], is_video=m["type"] in ("video", "gif"))
            for m in data.get("media_extended", [])
        ]

    def _extract_quote(self, tweet_data: dict[str, Any]) -> RichMediaPayload | None:
        """Extract quoted tweet payload if present."""
        quote_data = tweet_data.get("qrt")
        if not isinstance(quote_data, dict) or not quote_data:
            return None

        handle = quote_data.get("user_screen_name", "unknown")
        return RichMediaPayload(
            author_name=quote_data.get("user_name", "Unknown"),
            author_handle=handle,
            author_url=f"https://x.com/{handle}",
            content=quote_data.get("text", ""),
            media_items=self._extract_media(quote_data),
        )
