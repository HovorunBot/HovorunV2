"""Application service for Threads media extraction."""

import html
import logging
import re
import urllib.parse
from typing import ClassVar

import aiohttp
from bs4 import BeautifulSoup, Tag

from hovorunv2.application.clients.base import BaseMediaClient, OGConfig, ProviderConfig
from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.ports import BrowserPort
from hovorunv2.application.services.translation_service import TranslationService
from hovorunv2.domain.enums import CommandName

logger = logging.getLogger(__name__)

THREADS_CONFIG = ProviderConfig(
    name=CommandName.THREADS,
    merge_quoted_media=False,
    translate_quoted=False,
    pattern=re.compile(r"https?://(?:www\.)?threads\.(?P<tld>net|com)/(?:@[\w.-]+/)?(?:post|t)/(?P<post_id>[\w-]+)"),
    og_config=OGConfig(
        default_author="Threads User",
        author_handle="threads",
        author_url="https://www.threads.net",
        title_suffix=" on Threads",
    ),
)


class ThreadsService(BaseMediaClient):
    """Service to interact with Threads and process thread links."""

    CONFIG: ClassVar[ProviderConfig] = THREADS_CONFIG
    MIN_TEXT_LENGTH: ClassVar[int] = 10
    MAX_PROFILE_LINK_DEPTH: ClassVar[int] = 3
    MIN_QUOTE_LENGTH: ClassVar[int] = 50
    MIN_QUOTED_TEXT_LENGTH: ClassVar[int] = 20
    MIN_FALLBACK_QUOTE_LENGTH: ClassVar[int] = 30
    PROFILE_PATH_PARTS: ClassVar[int] = 2

    def __init__(self, translation_service: TranslationService, browser_service: BrowserPort) -> None:
        """Initialize with required services."""
        super().__init__(translation_service)
        self._browser_service = browser_service

    async def extract_fallback(
        self,
        url: str,
        chat_id: int = 0,
        platform: str = "telegram",
        session: aiohttp.ClientSession | None = None,
    ) -> RichMediaPayload | None:
        """Fetch Threads post via headless browser after yt-dlp is attempted."""
        if session is None:
            return None

        match = self._config.pattern.search(url)
        if not match:
            return None

        payload = await self._extract_from_browser(url, match)
        if payload is None:
            return None

        return await self._finalize_payload(payload, url, chat_id, platform, session)

    async def _extract_from_browser(
        self,
        url: str,
        match: re.Match[str],
    ) -> RichMediaPayload | None:
        """Fetch native Threads data using BrowserService and construct raw RichMediaPayload."""
        if not self._browser_service:
            return None

        tld = match.group("tld")
        post_id = match.group("post_id")

        try:
            html_content = await self._browser_service.get_content(url, wait_selector="article")
        except Exception:
            logger.exception("Failed to fetch Threads URL via BrowserService: %s", url)
            return None

        if not html_content:
            return None

        soup = BeautifulSoup(html_content, "html.parser")
        media_items = self._extract_media_from_soup(soup)
        if not media_items:
            if soup.find("meta", property="og:video"):
                return None
            media_items = self._extract_thumbnail(soup)

        author_name, author_handle = self._extract_author_from_soup(soup)
        return RichMediaPayload(
            author_name=author_name,
            author_handle=author_handle,
            author_url=f"https://www.threads.{tld}/@{urllib.parse.quote(author_handle)}",
            content=self._extract_text_from_soup(soup),
            footer_text=self._extract_footer_from_soup(soup, post_id) or "Threads",
            original_url=url,
            media_items=media_items,
            quoted_payload=self._extract_quoted_post(soup),
        )

    def _extract_footer_from_soup(self, soup: BeautifulSoup, post_id: str) -> str:
        """Extract likes, replies, and reposts from embedded JSON for the footer."""
        metrics_content = None
        for script in soup.find_all("script", {"type": "application/json"}):
            content = script.get_text()
            if content and re.search(rf'"code"\s*:\s*"{re.escape(post_id)}"', content) and '"like_count"' in content:
                metrics_content = content
                break

        if not metrics_content:
            return ""

        likes_m = re.search(r'"like_count"\s*:\s*(\d+)', metrics_content)
        replies_m = re.search(r'"direct_reply_count"\s*:\s*(\d+)', metrics_content)
        reposts_m = re.search(r'"repost_count"\s*:\s*(\d+)', metrics_content)

        return self._build_metrics_footer(
            ("💬", int(replies_m.group(1)) if replies_m else 0),
            ("🔄", int(reposts_m.group(1)) if reposts_m else 0),
            ("❤️", int(likes_m.group(1)) if likes_m else 0),
        )

    def _extract_author_from_soup(self, soup: BeautifulSoup) -> tuple[str, str]:
        """Extract author info from meta tags or page content."""
        og_title = soup.find("meta", property="og:title")
        title = str(og_title["content"]) if og_title and isinstance(og_title, Tag) else ""

        if author_match := re.search(r"^(.*?)\s+\(@(.*?)\)", title):
            return author_match.group(1).strip(), author_match.group(2).strip()
        return "Unknown", "unknown"

    def _get_main_container(self, soup: BeautifulSoup) -> Tag | None:
        """Find the main post container (article or region)."""
        article = soup.find("article")
        if article and isinstance(article, Tag):
            return article

        for role in ["main", "region"]:
            for container in soup.find_all("div", role=role):
                if container.find(string=re.compile("Translate", re.IGNORECASE)) or container.find("div", dir="auto"):
                    return container
        return None

    def _extract_text_from_soup(self, soup: BeautifulSoup) -> str:
        """Extract post text from DOM, falling back to meta tags."""
        main_container = self._get_main_container(soup)
        if not main_container:
            return self._extract_text_from_meta(soup)

        quoted_container = self._find_quoted_container(main_container)
        quoted_elements = set(quoted_container.find_all(["div", "span"], dir="auto")) if quoted_container else set()
        noise_elements = self._get_noise_elements(main_container)

        for el in main_container.find_all(["div", "span"], dir="auto"):
            if self._is_excluded_element(el, quoted_elements, noise_elements, quoted_container):
                continue

            text = el.get_text().strip()
            if text and (len(text) > self.MIN_TEXT_LENGTH or (not text.endswith(("d", "h")) and text != "Translate")):
                return text

        return self._extract_text_from_meta(soup)

    def _get_noise_elements(self, main_container: Tag) -> set[Tag]:
        """Identify common links to exclude (handle, timestamp)."""
        noise: set[Tag] = set()
        for link in main_container.find_all("a", role="link"):
            href = str(link.get("href") or "")
            if href.startswith("/@") and len(href.split("/")) <= self.MAX_PROFILE_LINK_DEPTH:
                noise.update(link.find_all(["div", "span"], dir="auto"))
                noise.add(link)
        return noise

    def _is_excluded_element(self, el: Tag, quoted: set[Tag], noise: set[Tag], quoted_container: Tag | None) -> bool:
        """Check if element should be excluded from text extraction."""
        return (
            el in quoted
            or el in noise
            or any(parent in quoted or parent in noise or parent == quoted_container for parent in el.parents)
        )

    def _extract_text_from_meta(self, soup: BeautifulSoup) -> str:
        """Fallback: og:description usually contains the post text."""
        og_desc = soup.find("meta", property="og:description")
        return str(og_desc["content"]) if og_desc and isinstance(og_desc, Tag) and og_desc.get("content") else ""

    def _extract_media_from_soup(self, soup: BeautifulSoup) -> list[MediaItem]:
        """Extract media (images/videos) from the main post DOM."""
        main_container = self._get_main_container(soup)
        if not main_container:
            return []

        quoted_container = self._find_quoted_container(main_container)
        quoted_elements: set[Tag] = set()
        if quoted_container:
            quoted_elements.add(quoted_container)
            quoted_elements.update(quoted_container.find_all(lambda t: isinstance(t, Tag)))

        return self._collect_container_media(main_container, exclude=quoted_elements)

    def _collect_container_media(self, container: Tag, exclude: set[Tag] | None = None) -> list[MediaItem]:
        """Extract video and CDN image MediaItems from a DOM container."""
        excluded = exclude or set()
        items: list[MediaItem] = [
            MediaItem(url=str(video.get("src")), is_video=True)
            for video in container.find_all("video")
            if video not in excluded and isinstance(video.get("src"), str)
        ]
        items.extend(
            MediaItem(url=str(img.get("src")), is_video=False)
            for img in container.find_all("img")
            if img not in excluded
            and "cdninstagram.com" in str(img.get("src") or "")
            and "profile picture" not in str(img.get("alt") or "").lower()
        )
        return items

    def _extract_thumbnail(self, soup: BeautifulSoup) -> list[MediaItem]:
        """Fallback to OG images (thumbnails) for media extraction."""
        return [
            MediaItem(url=html.unescape(str(img["content"])), is_video=False)
            for img in soup.find_all("meta", property="og:image")
            if isinstance(img, Tag) and img.get("content")
        ]

    def _find_quoted_container(self, container: Tag) -> Tag | None:
        """Find the container representing a quoted post within a main container."""
        if (nested_article := container.find("article")) and isinstance(nested_article, Tag):
            return nested_article

        for link in container.find_all(role="link"):
            href = str(link.get("href") or "")
            if (
                href.startswith("/@")
                and len(href.split("/")) == self.PROFILE_PATH_PARTS
                and len(link.get_text()) < self.MIN_QUOTE_LENGTH
            ):
                continue

            text = link.get_text().strip()
            if len(text) > self.MIN_QUOTED_TEXT_LENGTH or link.find("video"):
                if img := link.find("img"):
                    if "profile picture" in str(img.get("alt") or "").lower():
                        continue
                    return link
                if len(text) > self.MIN_FALLBACK_QUOTE_LENGTH:
                    return link
        return None

    def _extract_quoted_post(self, soup: BeautifulSoup) -> RichMediaPayload | None:
        """Attempt to find and parse a quoted post in the HTML."""
        main_container = self._get_main_container(soup)
        quoted_container = self._find_quoted_container(main_container) if main_container else None
        if not quoted_container:
            return None

        try:
            author_div = quoted_container.find("div", dir="auto", style=re.compile("font-weight: 600"))
            first_auto = quoted_container.find("div", dir="auto")
            author_name = (
                author_div.get_text()
                if author_div
                else (first_auto.get_text().strip() if first_auto else "Quoted Post")
            ) or "Quoted Post"

            text_div = quoted_container.find("div", dir="auto", style=re.compile("font-weight: 400"))
            if text_div:
                text = text_div.get_text()
            else:
                text = quoted_container.get_text(separator=" ").strip()
                if text.startswith(author_name):
                    text = text[len(author_name) :].strip()

            return RichMediaPayload(
                author_name=author_name,
                author_handle="threads",
                author_url="",
                content=text or "",
                footer_text="Quoted Post",
                media_items=self._collect_container_media(quoted_container),
            )
        except Exception:
            logger.debug("Failed to parse quoted post details")
            return None
