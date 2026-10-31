"""Application service for Twitter/X media extraction."""

import re
from typing import Self

import aiohttp

from hovorunv2.application.jobs.http_ports import SyncHTTPPort
from hovorunv2.application.media.browser import BrowserPort
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.application.media.providers.base import BaseMediaClient, ProviderConfig
from hovorunv2.domain.enums import CommandName
from hovorunv2.utils.json_values import mapping, sequence, text

TWITTER_CONFIG = ProviderConfig(
    name=CommandName.TWITTER,
    pattern=re.compile(
        r"https?://(?:www\.)?(?:api\.)?(?:x\.com|twitter\.com)/(?:\w+/status/|2/tweets/)(?P<post_id>\d+)"
    ),
)


class TwitterService(BaseMediaClient):
    """Service to interact with Twitter and process tweet links."""

    def __init__(self, http: aiohttp.ClientSession, browser: BrowserPort) -> None:
        """Initialize with HTTP session and browser adapter."""
        super().__init__(config=TWITTER_CONFIG, http=http, browser=browser)

    @classmethod
    def sync(cls) -> Self:
        """Create a client instance for synchronous worker extraction."""
        return cls.for_worker(TWITTER_CONFIG)

    async def _extract_raw_payload(self, url: str, match: re.Match[str]) -> RichMediaPayload:
        """Fetch tweet data from vxtwitter API and return raw RichMediaPayload."""
        tweet_data = await self._fetch_json(f"https://api.vxtwitter.com/i/status/{match.group('post_id')}")
        return self._parse_tweet_data(tweet_data, url)

    def _extract_raw_payload_sync(
        self,
        url: str,
        match: re.Match[str],
        http: SyncHTTPPort,
    ) -> RichMediaPayload:
        """Fetch tweet data synchronously from vxtwitter API and return raw RichMediaPayload."""
        tweet_data = self._fetch_json_sync(http, f"https://api.vxtwitter.com/i/status/{match.group('post_id')}")
        return self._parse_tweet_data(tweet_data, url)

    def _parse_tweet_data(self, tweet_data: object, url: str) -> RichMediaPayload:
        """Parse raw vxtwitter JSON into RichMediaPayload."""
        if not isinstance(tweet_data, dict) or "text" not in tweet_data:
            msg = "Provider returned unusable extraction metadata."
            raise MediaExtractionError(msg)

        raw_text = self._post_text(tweet_data.get("text", ""), tweet_data.get("qrtURL"))

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

    def _post_text(self, text: str, quote_url: str | None) -> str:
        """Remove the attached quote link without changing unrelated post text or polls."""
        if not quote_url or quote_url not in text:
            return text
        return text.replace(quote_url, "").strip()

    def _extract_media(self, data: dict[str, object]) -> list[MediaItem]:
        """Extract MediaItem list from vxtwitter media_extended."""
        items: list[MediaItem] = []
        for media in sequence(data.get("media_extended")):
            entry = mapping(media)
            url = text(entry.get("url"))
            if url:
                items.append(MediaItem(url=url, is_video=text(entry.get("type")) in ("video", "gif")))
        return items

    def _extract_quote(self, tweet_data: dict[str, object]) -> RichMediaPayload | None:
        """Extract quoted tweet payload if present."""
        quote_data = mapping(tweet_data.get("qrt"))
        if not quote_data:
            return None

        handle = text(quote_data.get("user_screen_name"), "unknown")
        return RichMediaPayload(
            author_name=text(quote_data.get("user_name"), "Unknown"),
            author_handle=handle,
            author_url=f"https://x.com/{handle}",
            content=text(quote_data.get("text")),
            media_items=self._extract_media(quote_data),
        )
