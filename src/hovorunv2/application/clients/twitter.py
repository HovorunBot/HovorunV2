"""Application service for Twitter/X media extraction."""

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


class TwitterService:
    """Service to interact with Twitter and process tweet links."""

    PATTERN = re.compile(
        r"https?://(?:www\.)?(?:api\.)?(?:x\.com|twitter\.com)/(?:\w+/status/|2/tweets/)(?P<post_id>\d+)",
    )

    def __init__(self, translation_service: TranslationService) -> None:
        """Initialize with required translation service."""
        self._translation_service = translation_service

    async def extract_payload(
        self, session: aiohttp.ClientSession, url: str, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch tweet data and construct a RichMediaPayload."""
        match = self.PATTERN.search(url)
        if not match:
            return None

        post_id = match.group("post_id")
        tweet_data = await self._fetch_tweet_data(session, post_id, url)
        if not tweet_data:
            return None

        raw_text = tweet_data.get("text", "")
        qrt_url = tweet_data.get("qrtURL")
        if qrt_url and qrt_url in raw_text:
            raw_text = raw_text.replace(qrt_url, "").strip()

        content = html.escape(raw_text)

        # Translation
        trans_res = await self._translation_service.translate_if_needed(content, chat_id, platform, session)
        if trans_res:
            content += f"\n\n{trans_res.flag} <b>Translated:</b>\n{html.escape(trans_res.text)}"

        media_items = [
            MediaItem(url=m["url"], is_video=m["type"] in ("video", "gif"))
            for m in tweet_data.get("media_extended", [])
        ]

        quoted_payload, quoted_media, info_note = await self._handle_quote(session, tweet_data, chat_id, platform)
        media_items.extend(quoted_media)
        content += info_note

        return RichMediaPayload(
            author_name=html.escape(tweet_data.get("user_name", "Unknown")),
            author_handle=html.escape(tweet_data.get("user_screen_name", "unknown")),
            author_url=f"https://x.com/{tweet_data.get('user_screen_name', 'unknown')}",
            content=content,
            footer_text=self._build_footer(tweet_data),
            original_url=url,
            media_items=media_items,
            quoted_payload=quoted_payload,
        )

    async def _fetch_tweet_data(self, session: aiohttp.ClientSession, post_id: str, url: str) -> dict[str, Any] | None:
        """Fetch tweet data from vxtwitter API."""
        api_url = f"https://api.vxtwitter.com/i/status/{post_id}"
        async with session.get(api_url) as response:
            if response.status != HTTPStatus.OK:
                logger.error("vxtwitter API returned HTTP %d for %s", response.status, url)
                return None
            tweet_data = await response.json()

        if not tweet_data or "text" not in tweet_data:
            logger.error("vxtwitter API returned invalid data for %s", url)
            return None

        return tweet_data

    async def _handle_quote(
        self, session: aiohttp.ClientSession, tweet_data: dict[str, Any], chat_id: int, platform: str
    ) -> tuple[RichMediaPayload | None, list[MediaItem], str]:
        """Process quoted tweet if present."""
        quote_data = tweet_data.get("qrt", {})
        if not quote_data:
            return None, [], ""

        quote_text = html.escape(quote_data.get("text", ""))
        quote_trans_res = await self._translation_service.translate_if_needed(quote_text, chat_id, platform, session)
        if quote_trans_res:
            quote_text += f"\n\n{quote_trans_res.flag} <b>Translated:</b>\n{html.escape(quote_trans_res.text)}"

        # Extract quoted media
        quoted_media = [
            MediaItem(url=m["url"], is_video=m["type"] in ("video", "gif"))
            for m in quote_data.get("media_extended", [])
        ]

        info_note = "\n\nℹ️️ <i>Post includes quoted media</i>" if quoted_media else ""  # noqa: RUF001

        payload = RichMediaPayload(
            author_name=html.escape(quote_data.get("user_name", "Unknown")),
            author_handle=html.escape(quote_data.get("user_screen_name", "unknown")),
            author_url=f"https://x.com/{quote_data.get('user_screen_name', 'unknown')}",
            content=quote_text,
            media_items=quoted_media,
        )
        return payload, quoted_media, info_note

    def _build_footer(self, tweet_data: dict[str, Any]) -> str:
        """Build footer string with metrics."""
        return (
            f"🔁 {format_number(tweet_data.get('retweets', 0))} | "
            f"❤️ {format_number(tweet_data.get('likes', 0))} | "
            f"💬 {format_number(tweet_data.get('replies', 0))}"
        )
