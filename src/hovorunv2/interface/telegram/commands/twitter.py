"""Module for handling Twitter/X post links in messages using VxTwitter API."""

import html
import re
from http import HTTPStatus
from typing import TYPE_CHECKING

from hovorunv2.infrastructure.container import container
from hovorunv2.infrastructure.logger import get_logger

from .base import RichMediaCommand, RichMediaPayload, register_command

if TYPE_CHECKING:
    import aiohttp

logger = get_logger(__name__)


@register_command
class TwitterCommand(RichMediaCommand):
    """Command for interacting with Twitter and processing tweet links."""

    @property
    def pattern(self) -> re.Pattern:
        """Regex pattern to match Twitter links."""
        return re.compile(
            r"https?://(?:www\.)?(?:api\.)?(?:x\.com|twitter\.com)/(?:\w+/status/|2/tweets/)(?P<post_id>\d+)",
        )

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch tweet data and construct a RichMediaPayload."""
        post_id = match.group("post_id")
        post_url = match.group(0)
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
        if container.translation_service:
            trans_res = await container.translation_service.translate_if_needed(content, chat_id, platform, session)
            if trans_res:
                content += f"\n\n{trans_res.flag} <b>Translated:</b>\n{html.escape(trans_res.text)}"

        # Handle Quote
        quote_data = data.get("qrt", {})
        if quote_data:
            orig_text = html.escape(quote_data.get("text", ""))
            if container.translation_service:
                quote_trans_res = await container.translation_service.translate_if_needed(
                    orig_text, chat_id, platform, session
                )
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

        media_items = data.get("media_extended", [])
        media_urls = [m["url"] for m in media_items]
        is_video = any(m["type"] in ("video", "gif") for m in media_items)

        footer = (
            f"\n\n🔁 {self._format_number(data.get('retweets', 0))} | "
            f"❤️ {self._format_number(data.get('likes', 0))} | "
            f"👁️ {self._format_number(data.get('replies', 0))}\n"
            f'🔗 <a href="{post_url}">Open post</a>'
        )

        return RichMediaPayload(
            author_name=html.escape(data.get("user_name", "Unknown")),
            author_handle=html.escape(data.get("user_screen_name", "unknown")),
            author_url=f"https://x.com/{data.get('user_screen_name', 'unknown')}",
            content=content,
            footer_text=footer,
            original_url=post_url,
            media_urls=media_urls,
            is_video=is_video,
        )
