"""Application service for Reddit media and post extraction via vxReddit."""

import html
import re
from typing import ClassVar

import aiohttp
from bs4 import BeautifulSoup, Tag

from hovorunv2.application.clients.base import BaseMediaClient, ProviderConfig
from hovorunv2.application.data.constants import CommandName
from hovorunv2.application.dtos import MediaItem, RichMediaPayload

REDDIT_CONFIG = ProviderConfig(
    name=CommandName.REDDIT,
    headers={
        "User-Agent": "TelegramBot (like TwitterBot)",
    },
    pattern=re.compile(
        r"https?://(?:"
        r"(?:[\w-]+\.)?reddit\.com/(?:r|u|user)/[\w-]+/(?:comments/(?P<post_id>[\w-]+)(?:/[^\s/?#]*)?/?|s/(?P<share_id>[\w-]+))|"
        r"redd\.it/(?P<short_id>[\w-]+)"
        r")"
    ),
)


class RedditService(BaseMediaClient):
    """Service to interact with Reddit and process post links via vxReddit."""

    CONFIG: ClassVar[ProviderConfig] = REDDIT_CONFIG
    VXREDDIT_BASE_URL: ClassVar[str] = "https://vxreddit.com"
    STATS_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"^u/(?P<author>\S+)\s+on\s+(?P<subreddit>\S+)\s+-\s+⬆️\s*(?P<upvotes>\d+)(?:\s*\|\s*💬\s*(?P<comments>\d+))?"
    )

    async def _extract_raw_payload(
        self,
        session: aiohttp.ClientSession,
        url: str,
        match: re.Match[str],
    ) -> RichMediaPayload | None:
        """Fetch Reddit post metadata from vxReddit and construct raw RichMediaPayload."""
        vx_url = re.sub(
            r"^https?://(?:(?:[\w-]+\.)?reddit\.com|redd\.it)",
            self.VXREDDIT_BASE_URL,
            url.split("?", maxsplit=1)[0],
        )
        html_content = await self._fetch_html(session, vx_url)
        if not html_content:
            return None

        return self._parse_vxreddit_html(html_content, url)

    def _parse_vxreddit_html(self, html_content: str, original_url: str) -> RichMediaPayload | None:
        """Parse OpenGraph metadata from vxReddit HTML response."""
        soup = BeautifulSoup(html_content, "html.parser")
        site_name = self._get_meta_content(soup, "og:site_name")
        if not site_name:
            return None

        if stats_match := self.STATS_PATTERN.search(site_name):
            author = stats_match.group("author")
            subreddit = stats_match.group("subreddit")
            upvotes = int(stats_match.group("upvotes"))
            comments = int(stats_match.group("comments")) if stats_match.group("comments") else None
            author_name = f"{author} ({subreddit})"
            author_handle = author
            author_url = f"https://www.reddit.com/user/{author}"
            footer_text = self._build_metrics_footer(("⬆️", upvotes), ("💬", comments), include_zero=True)
        else:
            author_name = site_name
            author_handle = "reddit"
            author_url = "https://www.reddit.com"
            footer_text = ""

        og_title = self._get_meta_content(soup, "og:title")
        og_desc = self._get_meta_content(soup, "og:description")
        if og_title == "vxReddit":
            content = og_desc
        elif og_title and og_desc and og_title != og_desc:
            content = f"{og_title}\n\n{og_desc}"
        else:
            content = og_title or og_desc

        media_items = self._extract_media_items(soup)
        if not content and not media_items:
            return None

        return RichMediaPayload(
            author_name=author_name,
            author_handle=author_handle,
            author_url=author_url,
            content=content,
            footer_text=footer_text,
            original_url=original_url,
            media_items=media_items,
        )

    def _extract_media_items(self, soup: BeautifulSoup) -> list[MediaItem]:
        """Extract video or image media items from vxReddit meta tags."""
        if video_url := self._get_meta_content(soup, "og:video"):
            return [MediaItem(url=html.unescape(video_url), is_video=True)]

        return [
            MediaItem(url=html.unescape(str(tag["content"])), is_video=False)
            for tag in soup.find_all("meta", property="og:image")
            if isinstance(tag, Tag) and tag.get("content")
        ]

    def _get_meta_content(self, soup: BeautifulSoup, prop: str) -> str:
        """Return trimmed content attribute for a meta property tag."""
        tag = soup.find("meta", property=prop)
        if isinstance(tag, Tag) and tag.get("content"):
            return str(tag["content"]).strip()
        return ""
