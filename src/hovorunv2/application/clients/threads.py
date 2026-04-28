"""Application service for Threads media extraction."""

import html
import re
import urllib.parse
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup, Tag

from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.infrastructure.logger import get_logger

if TYPE_CHECKING:
    import aiohttp

    from hovorunv2.application.services.translation_service import TranslationService
    from hovorunv2.infrastructure.browser import BrowserService

logger = get_logger(__name__)


class ThreadsService:
    """Service to interact with Threads and process thread links."""

    FETCH_TIMEOUT_SECONDS: int = 10
    AUTHOR_NAME_INDEX: int = 1
    AUTHOR_HANDLE_INDEX: int = 2
    MIN_TEXT_LENGTH: int = 10
    MAX_PROFILE_LINK_DEPTH: int = 3
    MIN_QUOTE_LENGTH: int = 50
    MIN_QUOTED_TEXT_LENGTH: int = 20
    MIN_FALLBACK_QUOTE_LENGTH: int = 30
    PROFILE_PATH_PARTS: int = 2

    PATTERN = re.compile(
        r"https?://(?:www\.)?threads\.(?P<tld>net|com)/(?:@[\w.-]+/)?(?:post|t)/(?P<post_id>[\w-]+)",
    )

    def __init__(self, translation_service: TranslationService, browser_service: BrowserService) -> None:
        """Initialize with required services."""
        self._translation_service = translation_service
        self._browser_service = browser_service

    async def extract_payload(
        self, session: aiohttp.ClientSession, url: str, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch native Threads data using Playwright for full extraction."""
        match = self.PATTERN.search(url)
        if not match:
            return None

        tld = match.group("tld")

        try:
            # Use wait_selector to ensure React hydration of the post content
            html_content = await self._browser_service.get_content(url, wait_selector="article")
        except Exception:
            logger.exception("Failed to fetch Threads URL via Playwright: %s", url)
            return None

        soup = BeautifulSoup(html_content, "html.parser")

        # 1. Parse Author Info
        author_name, author_handle = self._extract_author_from_soup(soup)

        # 2. Parse Main Content (Prioritize DOM over OG to avoid truncation)
        raw_text = self._extract_text_from_soup(soup)
        content = html.escape(raw_text)

        trans_res = await self._translation_service.translate_if_needed(content, chat_id, platform, session)
        if trans_res:
            content += f"\n\n{trans_res.flag} <b>Translated:</b>\n{html.escape(trans_res.text)}"

        # 3. Parse Media (Prioritize DOM for carousels and videos)
        media_items = self._extract_media_from_soup(soup)

        # 4. Handle Quoted Post
        quoted_payload = self._extract_quoted_post(soup)

        return RichMediaPayload(
            author_name=html.escape(author_name),
            author_handle=html.escape(author_handle),
            author_url=f"https://www.threads.{tld}/@{urllib.parse.quote(author_handle)}",
            content=content,
            footer_text="Threads",
            original_url=url,
            media_items=media_items,
            quoted_payload=quoted_payload,
        )

    def _extract_author_from_soup(self, soup: BeautifulSoup) -> tuple[str, str]:
        """Extract author info from meta tags or page content."""
        og_title = soup.find("meta", property="og:title")
        title = str(og_title["content"]) if og_title and isinstance(og_title, Tag) else ""

        author_match = re.search(r"^(.*?)\s+\(@(.*?)\)", title)
        if author_match:
            return author_match.group(1).strip(), author_match.group(2).strip()

        return "Unknown", "unknown"

    def _get_main_container(self, soup: BeautifulSoup) -> Tag | None:
        """Find the main post container (article or region)."""
        # 1. Try standard article tag
        article = soup.find("article")
        if article and isinstance(article, Tag):
            return article

        # 2. Try div with role="main" or role="region"
        for role in ["main", "region"]:
            containers = soup.find_all("div", role=role)
            for container in containers:
                # Main post container usually contains a 'Translate' button or specific handle structure
                if container.find(string=re.compile("Translate", re.IGNORECASE)) or container.find("div", dir="auto"):
                    return container

        return None

    def _extract_text_from_soup(self, soup: BeautifulSoup) -> str:
        """Extract post text from DOM, falling back to meta tags."""
        main_container = self._get_main_container(soup)
        if main_container:
            # Identify quoted area to exclude its text from the main post text
            quoted_container = self._find_quoted_container(main_container)
            quoted_text_elements = set()
            if quoted_container:
                quoted_text_elements.update(quoted_container.find_all(["div", "span"], dir="auto"))

            # Also identify common links to exclude (handle, timestamp)
            # These are usually links at the beginning of the main container
            noise_elements = set()
            for link in main_container.find_all("a", role="link"):
                href_val = link.get("href")
                href = str(href_val) if href_val else ""
                # Profile links or timestamp links
                if href.startswith("/@") and len(href.split("/")) <= self.MAX_PROFILE_LINK_DEPTH:
                    noise_elements.update(link.find_all(["div", "span"], dir="auto"))
                    noise_elements.add(link)

            # The main text is usually the first div/span with dir="auto" not in quotes or noise
            for el in main_container.find_all(["div", "span"], dir="auto"):
                if el in quoted_text_elements or el in noise_elements:
                    continue

                # Check if any parent is noise or quote
                is_noise = False
                for parent in el.parents:
                    if parent in quoted_text_elements or parent in noise_elements or parent == quoted_container:
                        is_noise = True
                        break
                if is_noise:
                    continue

                text = el.get_text().strip()
                if text and (
                    len(text) > self.MIN_TEXT_LENGTH
                    or (not text.endswith("d") and not text.endswith("h") and text != "Translate")
                ):
                    return text

        # Fallback: og:description usually contains the post text (might be truncated)
        og_desc = soup.find("meta", property="og:description")
        if og_desc and isinstance(og_desc, Tag) and og_desc.get("content"):
            return str(og_desc["content"])

        return ""

    def _extract_media_from_soup(self, soup: BeautifulSoup) -> list[MediaItem]:
        """Extract media (images/videos) from the main post DOM."""
        items: list[MediaItem] = []
        main_container = self._get_main_container(soup)

        if main_container:
            quoted_container = self._find_quoted_container(main_container)
            quoted_elements = set()
            if quoted_container:
                quoted_elements.add(quoted_container)
                quoted_elements.update(quoted_container.find_all(lambda t: isinstance(t, Tag)))

            # 1. Extract videos from main container
            for video in main_container.find_all("video"):
                if video in quoted_elements:
                    continue
                src_val = video.get("src")
                if src_val and isinstance(src_val, str):
                    items.append(MediaItem(url=src_val, is_video=True))

            # 2. Extract images from main container (including carousels)
            for img in main_container.find_all("img"):
                if img in quoted_elements:
                    continue
                src_val = img.get("src")
                alt_val = img.get("alt")
                src = str(src_val) if src_val else ""
                alt = str(alt_val).lower() if alt_val else ""
                # Filter out avatars and non-content images
                if src and "cdninstagram.com" in src and "profile picture" not in alt:
                    items.append(MediaItem(url=src, is_video=False))

        if items:
            return items

        # 3. Fallback to meta tags if DOM parsing yielded nothing
        return self._extract_media_from_og(soup)

    def _extract_media_from_og(self, soup: BeautifulSoup) -> list[MediaItem]:
        """Fallback to OG tags for media extraction."""
        items: list[MediaItem] = []

        og_video = soup.find("meta", property="og:video")
        if og_video and isinstance(og_video, Tag) and og_video.get("content"):
            items.append(MediaItem(url=html.unescape(str(og_video["content"])), is_video=True))
            return items

        og_images = soup.find_all("meta", property="og:image")
        items.extend(
            [
                MediaItem(url=html.unescape(str(img["content"])), is_video=False)
                for img in og_images
                if isinstance(img, Tag) and img.get("content")
            ]
        )

        return items

    def _find_quoted_container(self, container: Tag) -> Tag | None:
        """Find the container representing a quoted post within a main container."""
        # 1. Try nested article
        nested_article = container.find("article")
        if nested_article and isinstance(nested_article, Tag):
            return nested_article

        # 2. Try any element with role="link" that looks like a quoted post
        # Heuristic: quoted links usually contain text and media and point to a post
        for link in container.find_all(role="link"):
            href_val = link.get("href")
            href = str(href_val) if href_val else ""
            # Skip profile links like /@user
            if (
                href.startswith("/@")
                and len(href.split("/")) == self.PROFILE_PATH_PARTS
                and len(link.get_text()) < self.MIN_QUOTE_LENGTH
            ):
                continue

            # Quoted posts usually have a handle, text, and maybe media
            text = link.get_text().strip()
            if len(text) > self.MIN_QUOTED_TEXT_LENGTH or link.find("video"):
                # Check for images (exclude profile pictures)
                img = link.find("img")
                if img:
                    alt_val = img.get("alt")
                    alt = str(alt_val).lower() if alt_val else ""
                    if "profile picture" in alt:
                        continue
                    return link

                # If no images but substantial text, it's likely the quote
                if len(text) > self.MIN_FALLBACK_QUOTE_LENGTH:
                    return link

        return None

    def _extract_quoted_post(self, soup: BeautifulSoup) -> RichMediaPayload | None:
        """Attempt to find and parse a quoted post in the HTML."""
        main_container = self._get_main_container(soup)
        if not main_container:
            return None

        quoted_container = self._find_quoted_container(main_container)
        if not quoted_container:
            return None

        # Extract basic info from the quoted container
        try:
            # 1. Try to find author name
            # Usually in a bold/strong tag or specific class in standard article
            author_div = quoted_container.find("div", dir="auto", style=re.compile("font-weight: 600"))
            author_name = author_div.get_text() if author_div else None

            if not author_name:
                # Fallback: first few words if it looks like a handle, or use a placeholder
                # In some views, author is the first div with dir="auto"
                first_auto = quoted_container.find("div", dir="auto")
                if first_auto:
                    author_name = first_auto.get_text().strip()

            author_name = author_name or "Quoted Post"

            # 2. Try to find post text
            # Usually in a normal weight div
            text_div = quoted_container.find("div", dir="auto", style=re.compile("font-weight: 400"))
            text = text_div.get_text() if text_div else None

            if not text:
                # If no specific text div found, use the whole container text but strip author name
                text = quoted_container.get_text(separator=" ").strip()
                if author_name and text.startswith(author_name):
                    text = text[len(author_name) :].strip()

            # 3. Media in quoted post
            media: list[MediaItem] = []

            # Check for videos
            for video in quoted_container.find_all("video"):
                src_val = video.get("src")
                if src_val and isinstance(src_val, str):
                    media.append(MediaItem(url=src_val, is_video=True))

            # Check for images
            for img in quoted_container.find_all("img"):
                src_val = img.get("src")
                alt_val = img.get("alt")
                src = str(src_val) if src_val else ""
                alt = str(alt_val).lower() if alt_val else ""
                if src and "cdninstagram.com" in src and "profile picture" not in alt:
                    media.append(MediaItem(url=src, is_video=False))

            return RichMediaPayload(
                author_name=html.escape(author_name),
                author_handle="threads",
                author_url="",
                content=html.escape(text or ""),
                footer_text="Quoted Post",
                media_items=media,
            )
        except Exception:
            logger.debug("Failed to parse quoted post details")
            return None
