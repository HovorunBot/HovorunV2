"""Module for handling TikTok video links using TikWM API."""

import html
import re
from http import HTTPStatus
from typing import TYPE_CHECKING

import aiohttp

from hovorunv2.infrastructure.container import container
from hovorunv2.infrastructure.logger import get_logger

from .base import RichMediaCommand, RichMediaPayload, register_command

if TYPE_CHECKING:
    from aiogram.types import Message

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

    async def _extract_payload(self, session: aiohttp.ClientSession, match: re.Match) -> RichMediaPayload | None:
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
            music = video_data.get("music_info", {})

            author_nickname = author.get("nickname", "")
            music_author = music.get("author", "")
            music_title = music.get("title", "")
            music_url = music.get("play", "")

            # Filter out generic soundtracks (e.g., "Original Sound - [User]")
            is_original = any(x in music_title.lower() for x in ["original sound", "оригінальний звук", "звук від"])
            is_self_audio = is_original and (music_author == author_nickname or not music_author)

            music_section = ""
            if music_title and not is_self_audio:
                raw_music_text = f"{music_title} - {music_author}" if music_author else music_title
                music_text = html.escape(raw_music_text)
                music_section = (
                    f'\n\n🎵 <a href="{music_url}">{music_text}</a>' if music_url else f"\n\n🎵 {music_text}"
                )

            raw_description = video_data.get("title", "")
            clean_desc = re.sub(r"#\w+", "", raw_description)
            clean_desc = re.sub(r"\s+", " ", clean_desc).strip()
            desc = html.escape(clean_desc)

            if container.translation_service:
                translated = await container.translation_service.translate_if_needed(desc, session)
                if translated:
                    desc += f"\n\n🇺🇦 <b>Translated:</b>\n{html.escape(translated)}"

            media_urls = video_data.get("images", []) or [video_data.get("play", "")]
            is_video = not bool(video_data.get("images"))

            footer = (
                f"\n\n❤️ {self._format_number(video_data.get('digg_count', 0))} | "
                f"👁️ {self._format_number(video_data.get('play_count', 0))}\n"
                f'🔗 <a href="{url}">Original video</a>'
            )

            return RichMediaPayload(
                author_name=html.escape(author.get("nickname", "Unknown")),
                author_handle=html.escape(author.get("unique_id", "unknown")),
                author_url=f"https://www.tiktok.com/@{author.get('unique_id', 'unknown')}",
                content=(
                    f"<b>{html.escape(video_data.get('title', 'No Title').split('#')[0].strip())}</b>\n"
                    f"<i>{desc}</i>{music_section}"
                ),
                footer_text=footer,
                original_url=url,
                media_urls=media_urls,
                is_video=is_video,
            )
