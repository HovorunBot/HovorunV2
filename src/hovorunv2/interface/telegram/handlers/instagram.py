"""Module for handling Instagram media using InstagramService."""

import json
import re
import time
from typing import Any, ClassVar

import aiohttp
from aiogram import Bot
from aiogram.types import BufferedInputFile, InputMediaPhoto, InputMediaVideo, Message

from hovorunv2.application.clients.instagram import InstagramService
from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.application.media.extractor import MediaExtractor
from hovorunv2.infrastructure.browser import BrowserService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.logger import get_logger

from .base import RichMediaCommand

logger = get_logger(__name__)


class InstagramCommand(RichMediaCommand):
    """Command for interacting with Instagram and processing links."""

    # Selectors and JS as constants for maintainability and to fix line length issues
    WAIT_SELECTOR = "meta[property='og:image'], article, main"
    GATE_BUTTONS: ClassVar[list[str]] = ["See Post", "Verify Age", "Переглянути допис", "Підтвердити вік"]

    JS_EXTRACTOR = """
    () => {
        const getMeta = (prop) => {
            const el = document.querySelector(`meta[property="${prop}"], meta[name="${prop}"]`);
            return el ? el.content : null;
        };

        let data = {
            video: getMeta('og:video') || getMeta('og:video:secure_url'),
            image: getMeta('og:image'),
            title: getMeta('og:title') || document.title,
            description: getMeta('og:description') || "",
            likes: null,
            views: null,
            comments: null
        };

        const findData = (obj) => {
            if (obj && obj.items && obj.items[0]) return obj.items[0];
            if (obj && obj.graphql && obj.graphql.shortcode_media) return obj.graphql.shortcode_media;
            return null;
        };

        const post = findData(window.__additionalDataLoaded && window.__additionalDataLoaded.feed) ||
                     findData(window._sharedData && window._sharedData.entry_data &&
                              window._sharedData.entry_data.PostPage &&
                              window._sharedData.entry_data.PostPage[0]);

        if (post) {
            data.title = data.title || (post.caption ? (post.caption.text || post.caption) : "");
            data.video = data.video || (post.video_url ||
                        (post.video_versions ? post.video_versions[0].url : null));
            data.image = data.image || (post.display_url ||
                        (post.image_versions2 ? post.image_versions2.candidates[0].url : null));
            data.likes = data.likes || (post.like_count ||
                        (post.edge_media_preview_like ? post.edge_media_preview_like.count : null));
            data.comments = data.comments || (post.comment_count ||
                        (post.edge_media_to_comment ? post.edge_media_to_comment.count : null));
            data.views = data.views || (post.video_view_count || post.view_count || null);
        }
        return data;
    }
    """

    def __init__(
        self,
        instagram_service: InstagramService,
        media_downloader: MediaDownloader,
        media_extractor: MediaExtractor,
        browser_service: BrowserService,
        session: aiohttp.ClientSession,
        settings: Settings,
    ) -> None:
        """Initialize command with its dependencies."""
        super().__init__(media_downloader, session)
        self._instagram_service = instagram_service
        self._media_extractor = media_extractor
        self._browser_service = browser_service
        self._settings = settings

    @property
    def pattern(self) -> re.Pattern:
        """Regex pattern to match Instagram links."""
        return self._instagram_service.PATTERN

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Fetch Instagram data using InstagramService with fallbacks."""
        url = match.group(0).rstrip(".,")
        logger.info("Attempting extraction for cleaned URL: %s", url)

        # 1. Try specialized InstagramService (instaloader)
        payload = await self._instagram_service.extract_payload(
            session, url, chat_id, platform, sessionid=self._settings.instagram_sessionid
        )
        if payload:
            return payload

        # 2. Try generic MediaExtractor (yt-dlp)
        ytdlp_cookies = (
            {"sessionid": self._settings.instagram_sessionid} if self._settings.instagram_sessionid else None
        )
        payload = await self._media_extractor.extract_payload(session, url, chat_id, platform, cookies=ytdlp_cookies)
        if payload:
            return payload

        # 3. Try BrowserService as final fallback
        return await self._extract_via_browser(url)

    async def _extract_via_browser(self, url: str) -> RichMediaPayload | None:
        """Fetch content using headless browser as final fallback."""
        logger.info("Step 3: BrowserService (Scraping) for %s", url)
        try:
            cookies = []
            if self._settings.instagram_sessionid:
                cookies.append(
                    {
                        "name": "sessionid",
                        "value": self._settings.instagram_sessionid,
                        "domain": ".instagram.com",
                        "path": "/",
                    }
                )

            payload, media_bytes = await self._browser_service.extract_and_download(
                url, extractor_fn=self._extract_from_html, wait_selector=self.WAIT_SELECTOR, cookies=cookies
            )

            if payload:
                payload.downloaded_bytes = media_bytes
                logger.info("Browser fallback succeeded for %s with %d items", url, len(media_bytes))
                return payload
        except Exception:
            logger.exception("Browser fallback failed for %s", url)
        return None

    async def _send_rich_media(
        self, bot: Bot, message: Message, payload: RichMediaPayload, session: aiohttp.ClientSession
    ) -> None:
        """Overload to handle pre-downloaded browser content."""
        if not payload.downloaded_bytes:
            await super()._send_rich_media(bot, message, payload, session)
            return

        logger.info("Using pre-downloaded browser content for delivery")
        tg_user_name = message.from_user.full_name if message.from_user else "User"
        caption = await self._handle_caption_limits(
            message, payload, self._build_caption(payload, tg_user_name), tg_user_name
        )

        final_group: list[Any] = []
        for i, content in enumerate(payload.downloaded_bytes):
            item_meta = payload.media_items[i]
            ext = "mp4" if item_meta.is_video else "jpg"
            file = BufferedInputFile(content, filename=f"browser_{i}.{ext}")

            item = InputMediaVideo(media=file) if item_meta.is_video else InputMediaPhoto(media=file)
            if not final_group:
                item.caption = caption
                item.parse_mode = "HTML"
            final_group.append(item)

        if final_group:
            await bot.send_media_group(
                chat_id=message.chat.id,
                media=final_group,
                reply_to_message_id=message.message_id,
            )

    def _extract_from_html(self, tab: Any, url: str) -> RichMediaPayload | None:  # noqa: ANN401
        """Surgically extract OG metadata from tab using JS and HTML."""
        self._bypass_gates(tab)

        # 1. Try JavaScript extraction (most reliable for modern IG)
        try:
            js_results = tab.run_js(self.JS_EXTRACTOR)
            if js_results and (js_results.get("video") or js_results.get("image")):
                return self._create_payload_from_results(js_results, url)
        except Exception:
            logger.exception("JS extraction failed for %s", url)

        # 2. Fallback to scraping JSON from script tags manually
        payload = self._extract_from_scripts(tab.html, url)
        if payload:
            return payload

        # 3. Fallback to pure HTML regex
        return self._extract_via_regex(tab.html, url)

    def _bypass_gates(self, tab: Any) -> None:  # noqa: ANN401
        """Click bypass buttons for age/login walls."""
        try:
            for btn_text in self.GATE_BUTTONS:
                btn = tab.ele(f"text={btn_text}", timeout=1)
                if btn:
                    logger.info("Found '%s' button, clicking to bypass gate", btn_text)
                    btn.click()
                    time.sleep(1)
        except Exception:
            logger.debug("No clickable gate button found")

    def _extract_from_scripts(self, html_content: str, url: str) -> RichMediaPayload | None:
        """Extract media metadata from application/json script tags."""
        try:
            json_matches = re.findall(
                r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>', html_content, re.DOTALL
            )
            for json_str in json_matches:
                payload = self._try_parse_json_blob(json_str, url)
                if payload:
                    return payload
        except Exception:
            logger.debug("Script JSON extraction failed")
        return None

    def _try_parse_json_blob(self, json_str: str, url: str) -> RichMediaPayload | None:
        """Parse a single JSON blob for media URLs."""
        if "video_url" not in json_str and "display_url" not in json_str:
            return None

        try:
            data = json.loads(json_str)

            def find_key(d: Any, key: str) -> Any:  # noqa: ANN401
                """Recursively find a key in a dictionary or list."""
                if isinstance(d, dict):
                    if key in d:
                        return d[key]
                    for v in d.values():
                        res = find_key(v, key)
                        if res:
                            return res
                elif isinstance(d, list):
                    for item in d:
                        res = find_key(item, key)
                        if res:
                            return res
                return None

            video = find_key(data, "video_url")
            image = find_key(data, "display_url")
            if video or image:
                return self._create_payload_from_results({"video": video, "image": image}, url)
        except Exception:
            logger.debug("Failed to parse individual JSON blob")
        return None

    def _extract_via_regex(self, html_content: str, url: str) -> RichMediaPayload | None:
        """Fallback to pure HTML regex extraction."""
        logger.info("Falling back to regex extraction for %s", url)
        patterns = {
            "video": r'<meta[^>]+property=["\']og:video(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
            "image": r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            "title": r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            "description": r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
        }

        results: dict[str, Any] = {}
        for key, pattern in patterns.items():
            match = re.search(pattern, html_content, re.IGNORECASE)
            results[key] = match.group(1) if match else None

        if results.get("video") or results.get("image"):
            return self._create_payload_from_results(results, url)
        return None

    def _create_payload_from_results(self, results: dict[str, Any], url: str) -> RichMediaPayload:
        """Build RichMediaPayload from dictionary results."""
        media_url = results.get("video") or results.get("image")
        is_video = bool(results.get("video"))
        title = results.get("title") or "Instagram Post"
        description = results.get("description") or ""

        # Clean description
        description = re.sub(r"^.*?on Instagram:.*?\"", "", description).strip(' "')

        stats = []
        for key, emoji in [("likes", "❤️"), ("views", "👁️"), ("comments", "💬")]:
            if results.get(key):
                stats.append(f"{emoji} {results[key]}")

        footer = f"📊 {' | '.join(stats)}" if stats else "📷 Instagram"

        return RichMediaPayload(
            author_name="Instagram User",
            author_handle="instagram",
            author_url="https://www.instagram.com/",
            content=f"<b>{title}</b>\n\n{description}".strip(),
            footer_text=footer,
            original_url=url,
            media_items=[MediaItem(url=str(media_url), is_video=is_video)],
        )
