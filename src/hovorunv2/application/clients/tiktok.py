"""Application service for TikTok media extraction."""

import html
import re
from http import HTTPStatus

import aiohttp

from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.services.translation_service import TranslationService
from hovorunv2.application.utils import format_number
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class TikTokService:
    """Service to interact with TikTok and process video links."""

    API_URL = "https://www.tikwm.com/api/"
    SUCCESS_CODE = 0

    PATTERN = re.compile(
        r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/(?:(?P<type>v|t|video|photo)/|(?P<username>[@\w.-]+)/)?"
        r"(?:(?P<type_alt>video|photo)/)?(?P<id>[\w-]+)/?(?:(?<=/)\w+/?)*(?=[^\w/]|$)",
    )

    def __init__(self, translation_service: TranslationService) -> None:
        """Initialize with required translation service."""
        self._translation_service = translation_service

    async def extract_payload(
        self, session: aiohttp.ClientSession, url: str, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch TikTok data and construct a RichMediaPayload."""
        async with session.get(self.API_URL, params={"url": url}) as resp:
            if resp.status != HTTPStatus.OK:
                logger.error("Tikwm API returned HTTP %d for %s", resp.status, url)
                return None
            data = await resp.json()
            if data.get("code") != self.SUCCESS_CODE:
                logger.error("Tikwm API error for %s: %s", url, data.get("msg"))
                return None

            video_data = data.get("data")
            if not video_data:
                logger.error("Tikwm API returned success code but no data for %s", url)
                return None

            author = video_data.get("author", {})

            raw_description = video_data.get("title", "")
            clean_desc = re.sub(r"#\w+", "", raw_description)
            clean_desc = re.sub(r"\s+", " ", clean_desc).strip()
            desc = html.escape(clean_desc)

            trans_res = await self._translation_service.translate_if_needed(desc, chat_id, platform, session)
            if trans_res:
                desc += f"\n\n{trans_res.flag} <b>Translated:</b>\n{html.escape(trans_res.text)}"

            images = video_data.get("images", [])
            if images:
                media_items = [MediaItem(url=img, is_video=False) for img in images]
            else:
                video_url = video_data.get("play") or video_data.get("hdplay") or video_data.get("wmplay", "")
                media_items = [MediaItem(url=video_url, is_video=True)] if video_url else []

            footer = (
                f"❤️ {format_number(video_data.get('digg_count', 0))} | "
                f"👁️ {format_number(video_data.get('play_count', 0))}"
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
                media_items=media_items,
            )
