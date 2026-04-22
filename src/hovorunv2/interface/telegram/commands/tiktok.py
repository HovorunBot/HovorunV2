"""Module for handling TikTok video links using TikWM API."""

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
class TikTokCommand(RichMediaCommand):
    """Command for interacting with TikTok and processing video links."""

    API_URL = "https://www.tikwm.com/api/"
    SUCCESS_CODE = 0

    @property
    def pattern(self) -> re.Pattern:
        """Regex pattern to match TikTok links."""
        return re.compile(
            r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/(?:v/|t/|(?P<username>[@\w.-]+)/)?(?:video|photo)?/(?P<video_id>\d+)|"
            r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/(?P<short_id>\w+)",
        )

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch TikTok data and construct a RichMediaPayload."""
        url = match.group(0)
        async with session.get(self.API_URL, params={"url": url}) as resp:
            if resp.status != HTTPStatus.OK:
                return None
            data = await resp.json()
            if data.get("code") != self.SUCCESS_CODE:
                logger.error("Tikwm API error: %s", data.get("msg"))
                return None

            video_data = data["data"]
            author = video_data.get("author", {})

            raw_description = video_data.get("title", "")
            clean_desc = re.sub(r"#\w+", "", raw_description)
            clean_desc = re.sub(r"\s+", " ", clean_desc).strip()
            desc = html.escape(clean_desc)

            if container.translation_service:
                trans_res = await container.translation_service.translate_if_needed(desc, chat_id, platform, session)
                if trans_res:
                    desc += f"\n\n{trans_res.flag} <b>Translated:</b>\n{html.escape(trans_res.text)}"

            media_urls = video_data.get("images", []) or [video_data.get("play", "")]
            is_video = not bool(video_data.get("images"))

            footer = (
                f"\n\n❤️ {self._format_number(video_data.get('digg_count', 0))} | "
                f"👁️ {self._format_number(video_data.get('play_count', 0))}\n"
                f'🔗 <a href="{url}">Original video</a>'
            )

            title = html.escape(video_data.get("title", "No Title").split("#")[0].strip())
            content = f"<b>{title}</b>"
            if desc and desc != title:
                content += f"\n<i>{desc}</i>"

            return RichMediaPayload(
                author_name=html.escape(author.get("nickname", "Unknown")),
                author_handle=html.escape(author.get("unique_id", "unknown")),
                author_url=f"https://www.tiktok.com/@{author.get('unique_id', 'unknown')}",
                content=content,
                footer_text=footer,
                original_url=url,
                media_urls=media_urls,
                is_video=is_video,
            )
