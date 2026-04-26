"""Application service for Twitter/X media extraction."""

import html
import re
from http import HTTPStatus
from typing import TYPE_CHECKING

from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.utils import format_number
from hovorunv2.infrastructure.logger import get_logger

if TYPE_CHECKING:
    import aiohttp

    from hovorunv2.application.services.translation_service import TranslationService

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
        api_url = f"https://api.vxtwitter.com/i/status/{post_id}"

        async with session.get(api_url) as response:
            if response.status != HTTPStatus.OK:
                return None
            data = await response.json()

        raw_text = data.get("text", "")
        qrt_url = data.get("qrtURL")
        if qrt_url and qrt_url in raw_text:
            raw_text = raw_text.replace(qrt_url, "").strip()

        content = html.escape(raw_text)

        # Translation
        trans_res = await self._translation_service.translate_if_needed(content, chat_id, platform, session)
        if trans_res:
            content += f"\n\n{trans_res.flag} <b>Translated:</b>\n{html.escape(trans_res.text)}"

        # Handle Quote
        quote_data = data.get("qrt", {})
        if quote_data:
            orig_text = html.escape(quote_data.get("text", ""))
            quote_trans_res = await self._translation_service.translate_if_needed(orig_text, chat_id, platform, session)
            if quote_trans_res:
                orig_text += f"\n\n{quote_trans_res.flag} <b>Translated:</b>\n{html.escape(quote_trans_res.text)}"

            quote_section = (
                "<blockquote expandable>\n"
                f"🔄 <b>Original by {html.escape(quote_data.get('user_name', 'Unknown'))}</b> "
                f'(<a href="https://x.com/{quote_data.get("user_screen_name", "unknown")}">'
                f"@{quote_data.get('user_screen_name', 'unknown')}</a>):\n"
                f"{orig_text}\n"
                "</blockquote>"
            )
            content += f"\n\n{quote_section}"

        media_items = [
            MediaItem(url=m["url"], is_video=m["type"] in ("video", "gif")) for m in data.get("media_extended", [])
        ]

        footer = (
            f"\n\n🔁 {format_number(data.get('retweets', 0))} | "
            f"❤️ {format_number(data.get('likes', 0))} | "
            f"👁️ {format_number(data.get('replies', 0))}\n"
            f'🔗 <a href="{url}">Open post</a>'
        )

        return RichMediaPayload(
            author_name=html.escape(data.get("user_name", "Unknown")),
            author_handle=html.escape(data.get("user_screen_name", "unknown")),
            author_url=f"https://x.com/{data.get('user_screen_name', 'unknown')}",
            content=content,
            footer_text=footer,
            original_url=url,
            media_items=media_items,
        )
