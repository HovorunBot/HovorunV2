"""Configuration-driven base class for all media provider clients."""

import html
import json
import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, ClassVar

import aiohttp

from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.ports import BrowserPort
from hovorunv2.application.services.translation_service import TranslationService
from hovorunv2.application.text import clean_caption_text, extract_og_metadata, format_number
from hovorunv2.domain.enums import CommandName

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OGConfig:
    """Declarative configuration for OpenGraph metadata extraction."""

    default_author: str
    author_handle: str
    author_url: str
    title_separator: str | None = None
    title_suffix: str | None = None
    headers: Mapping[str, str] | None = None
    video_url_pattern: re.Pattern[str] | None = None


@dataclass(frozen=True)
class BrowserFallbackConfig:
    """Declarative configuration for headless browser fallback extraction."""

    wait_selector: str
    gate_buttons: tuple[str, ...] = ()
    js_extractor: str | None = None
    default_author: str = "User"
    author_handle: str = "user"
    author_url: str = ""
    default_title: str = "Post"
    default_footer: str = ""
    description_clean_pattern: re.Pattern[str] | None = None


@dataclass(frozen=True)
class ProviderConfig:
    """Declarative configuration for a media provider."""

    name: CommandName
    pattern: re.Pattern[str]
    platform_name: str = ""
    redirect_pattern: re.Pattern[str] | None = None
    post_url_pattern: re.Pattern[str] | None = None
    strip_hashtags: bool = False
    merge_quoted_media: bool = True
    merge_quoted_only_when_empty: bool = False
    translate_quoted: bool = True
    headers: Mapping[str, str] = field(default_factory=dict)
    og_config: OGConfig | None = None
    browser_config: BrowserFallbackConfig | None = None

    @property
    def label(self) -> str:
        """Human-readable platform name."""
        return self.platform_name or str(self.name).capitalize()


FACEBOOK_CONFIG = ProviderConfig(
    name=CommandName.FACEBOOK,
    pattern=re.compile(
        r"https?://(?:[\w-]+\.)?facebook\.com/(?:[^/]+/posts/\d+|groups/[^/]+/posts/\d+|"
        r"permalink\.php|photo\.php|reel/[\w-]+|watch/|videos/[\w-]+|share/[vpr]/[\w-]+|story\.php|(?P<id>\w+))"
    ),
    og_config=OGConfig(
        default_author="Facebook User",
        author_handle="facebook",
        author_url="https://www.facebook.com",
        title_separator=" - ",
        headers={"User-Agent": "facebookexternalhit/1.1"},
        video_url_pattern=re.compile(r"facebook\.com/(?:reel/|watch/|videos/|share/[vr]/|[^/]+/videos/)"),
    ),
)


class BaseMediaClient:
    """Generalized media client executing config-driven extraction, OG/browser fallbacks, and post-processing."""

    CONFIG: ClassVar[ProviderConfig]
    DEFAULT_TIMEOUT_SECONDS: ClassVar[int] = 15
    QUOTED_MEDIA_NOTE: ClassVar[str] = "\n\nℹ️️ <i>Post includes quoted media</i>"  # noqa: RUF001
    DEFAULT_HEADERS: ClassVar[Mapping[str, str]] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self, translation_service: TranslationService) -> None:
        """Initialize client with translation service."""
        self._translation_service = translation_service
        self._config: ProviderConfig = getattr(self, "CONFIG", FACEBOOK_CONFIG)
        self._browser_service: BrowserPort | None = None

    def with_config(self, config: ProviderConfig) -> BaseMediaClient:
        """Bind a specific ProviderConfig to this client instance and return self."""
        self._config = config
        return self

    @property
    def config(self) -> ProviderConfig:
        """Provider configuration."""
        return self._config

    @property
    def name(self) -> CommandName:
        """Provider command name."""
        return self._config.name

    @property
    def pattern(self) -> re.Pattern[str]:
        """Provider URL pattern."""
        return self._config.pattern

    @property
    def platform_label(self) -> str:
        """Human-readable platform label."""
        return self._config.label

    async def extract_payload(
        self, session: aiohttp.ClientSession, url: str, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Execute direct API extraction and post-processing."""
        if self._config.post_url_pattern and not self._config.post_url_pattern.search(url):
            return None

        resolved_url = await self.resolve_url(session, url)
        if not resolved_url:
            return None

        match = self._config.pattern.search(resolved_url)
        if not match:
            return None

        payload = await self._extract_raw_payload(session, resolved_url, match)
        if payload is None:
            return None

        return await self._finalize_payload(payload, url, chat_id, platform, session)

    async def extract_metadata_payload(
        self, session: aiohttp.ClientSession, url: str, chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Execute OpenGraph / HTML metadata extraction as final fallback."""
        if self._config.og_config is None:
            return None

        resolved_url = await self.resolve_url(session, url)
        if not resolved_url:
            return None

        payload = await self._extract_via_og(session, resolved_url, self._config.og_config)
        if payload is None:
            return None

        return await self._finalize_payload(payload, url, chat_id, platform, session)

    async def resolve_url(self, session: aiohttp.ClientSession, url: str) -> str | None:
        """Resolve redirects when configured on the provider."""
        if self._config.redirect_pattern and self._config.redirect_pattern.search(url):
            return await self._resolve_redirect(session, url, headers=self._config.headers or None)
        return url

    async def _extract_raw_payload(
        self,
        session: aiohttp.ClientSession,
        url: str,
        match: re.Match[str],
    ) -> RichMediaPayload | None:
        """Override in subclasses for provider-specific raw extraction (before escaping/translation)."""
        return None

    async def _finalize_payload(
        self,
        payload: RichMediaPayload,
        original_url: str,
        chat_id: int,
        platform: str,
        session: aiohttp.ClientSession,
    ) -> RichMediaPayload:
        """Centrally escape, clean, translate, and merge quoted media for any extracted payload."""
        payload.author_name = html.escape(payload.author_name)
        payload.author_handle = html.escape(payload.author_handle)
        payload.original_url = original_url
        payload.content = await self._translate_text(
            payload.content, chat_id, platform, session, strip_hashtags=self._config.strip_hashtags
        )

        if quote := payload.quoted_payload:
            quote.author_name = html.escape(quote.author_name)
            quote.author_handle = html.escape(quote.author_handle)
            if self._config.translate_quoted:
                quote.content = await self._translate_text(quote.content, chat_id, platform, session)
            else:
                quote.content = html.escape(quote.content)

            if (
                quote.media_items
                and self._config.merge_quoted_media
                and (not self._config.merge_quoted_only_when_empty or not payload.media_items)
            ):
                payload.media_items.extend(quote.media_items)
                payload.content += self.QUOTED_MEDIA_NOTE

        return payload

    def get_ytdlp_cookies(self) -> dict[str, str] | None:
        """Return optional cookies to pass to yt-dlp fallback."""
        return None

    def get_browser_cookies(self) -> list[dict[str, Any]]:
        """Return optional cookies to inject into headless browser fallback."""
        return []

    async def extract_fallback(
        self,
        url: str,
        chat_id: int = 0,
        platform: str = "telegram",
        session: aiohttp.ClientSession | None = None,
    ) -> RichMediaPayload | None:
        """Perform config-driven headless browser fallback when configured."""
        b_cfg = self._config.browser_config
        if not b_cfg or not self._browser_service:
            return None

        try:
            payload, media_bytes = await self._browser_service.extract_and_download(
                url,
                extractor_fn=lambda tab, target_url: self._extract_from_browser_tab(tab, target_url, b_cfg),
                wait_selector=b_cfg.wait_selector,
                cookies=self.get_browser_cookies(),
            )
            if payload:
                payload.downloaded_bytes = media_bytes
                return payload
        except Exception:
            logger.exception("Browser fallback failed for %s", url)
        return None

    def _extract_from_browser_tab(self, tab: Any, url: str, b_cfg: BrowserFallbackConfig) -> RichMediaPayload | None:
        """Extract metadata from browser tab via JS, JSON scripts, or OG tags."""
        for btn_text in b_cfg.gate_buttons:
            try:
                if btn := tab.ele(f"text={btn_text}", timeout=1):
                    btn.click()
                    time.sleep(1)
            except Exception:
                logger.debug("Gate button '%s' not clickable", btn_text)

        if b_cfg.js_extractor:
            try:
                js_results = tab.run_js(b_cfg.js_extractor)
                if isinstance(js_results, dict) and (js_results.get("video") or js_results.get("image")):
                    return self._build_browser_payload(js_results, url, b_cfg)
            except Exception:
                logger.exception("JS extraction failed for %s", url)

        if script_results := self._extract_from_json_scripts(tab.html):
            return self._build_browser_payload(script_results, url, b_cfg)

        og_meta = extract_og_metadata(tab.html)
        if og_meta.get("video") or og_meta.get("image"):
            return self._build_browser_payload(og_meta, url, b_cfg)
        return None

    def _extract_from_json_scripts(self, html_content: str) -> dict[str, Any] | None:
        """Extract video_url or display_url from application/json script tags."""
        for json_str in re.findall(
            r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>', html_content, re.DOTALL
        ):
            if "video_url" not in json_str and "display_url" not in json_str:
                continue
            try:
                script_data = json.loads(json_str)
                video = self._find_key_in_json(script_data, "video_url")
                image = self._find_key_in_json(script_data, "display_url")
                if video or image:
                    return {"video": video, "image": image}
            except Exception:
                logger.debug("Failed to parse JSON script blob")
        return None

    def _build_browser_payload(
        self, results: dict[str, Any], url: str, b_cfg: BrowserFallbackConfig
    ) -> RichMediaPayload:
        """Build RichMediaPayload from browser extraction dictionary."""
        media_url = results.get("video") or results.get("image")
        title = results.get("title") or b_cfg.default_title
        description = results.get("description") or ""
        if b_cfg.description_clean_pattern:
            description = b_cfg.description_clean_pattern.sub("", description).strip(' "')

        footer = (
            self._build_metrics_footer(
                ("❤️", results.get("likes")),
                ("👁️", results.get("views")),
                ("💬", results.get("comments")),
                prefix="📊 ",
            )
            or b_cfg.default_footer
        )

        return RichMediaPayload(
            author_name=b_cfg.default_author,
            author_handle=b_cfg.author_handle,
            author_url=b_cfg.author_url,
            content=f"<b>{title}</b>\n\n{description}".strip(),
            footer_text=footer,
            original_url=url,
            media_items=[MediaItem(url=str(media_url), is_video=bool(results.get("video")))],
        )

    async def _extract_via_og(
        self, session: aiohttp.ClientSession, url: str, og_cfg: OGConfig
    ) -> RichMediaPayload | None:
        """Fetch HTML and construct raw RichMediaPayload from OpenGraph metadata."""
        html_content = await self._fetch_html(session, url, headers=og_cfg.headers)
        if not html_content:
            return None
        return self._parse_og_html(html_content, url, og_cfg)

    def _parse_og_html(self, html_content: str, url: str, og_cfg: OGConfig) -> RichMediaPayload | None:
        """Parse OpenGraph metadata from HTML string into raw RichMediaPayload."""
        metadata = extract_og_metadata(html_content)
        if not metadata:
            return None

        og_url = metadata.get("url", "")
        is_video = (
            metadata.get("type") == "video"
            or "video" in metadata
            or bool(
                og_cfg.video_url_pattern
                and (og_cfg.video_url_pattern.search(url) or og_cfg.video_url_pattern.search(og_url))
            )
        )
        if is_video:
            return None

        raw_content = metadata.get("description") or metadata.get("title") or ""
        media_items = [MediaItem(url=metadata["image"], is_video=False)] if "image" in metadata else []
        if not raw_content and not media_items:
            return None

        title = metadata.get("title", "")
        author_name = og_cfg.default_author
        if og_cfg.title_separator and og_cfg.title_separator in title:
            author_name = title.split(og_cfg.title_separator, maxsplit=1)[0].strip() or og_cfg.default_author
        elif og_cfg.title_suffix and title:
            author_name = title.removesuffix(og_cfg.title_suffix).strip() or og_cfg.default_author

        return RichMediaPayload(
            author_name=author_name,
            author_handle=og_cfg.author_handle,
            author_url=og_cfg.author_url,
            content=raw_content,
            original_url=url,
            media_items=media_items,
        )

    async def _translate_text(
        self,
        raw_text: str,
        chat_id: int,
        platform: str,
        session: aiohttp.ClientSession,
        *,
        strip_hashtags: bool = False,
    ) -> str:
        """Clean, HTML-escape, and translate text if needed."""
        text_to_escape = clean_caption_text(raw_text, strip_hashtags=True) if strip_hashtags else raw_text
        content = html.escape(text_to_escape)
        trans_res = await self._translation_service.translate_if_needed(content, chat_id, session=session)
        if trans_res:
            content += f"\n\n{trans_res.flag} <b>Translated:</b>\n{html.escape(trans_res.text)}"
        return content

    async def _fetch_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Perform GET request and return parsed JSON or None on failure."""
        try:
            async with session.get(
                url,
                params=params,
                headers=headers or self._config.headers or None,
                timeout=aiohttp.ClientTimeout(total=self.DEFAULT_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status != HTTPStatus.OK:
                    logger.warning("%s API returned HTTP %d for %s", self.platform_label, resp.status, url)
                    return None
                return await resp.json()
        except Exception:
            logger.exception("Error fetching JSON for %s from %s", self.platform_label, url)
            return None

    async def _fetch_html(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> str | None:
        """Perform GET request and return HTML text or None on failure."""
        try:
            async with session.get(
                url,
                headers=headers or self._config.headers or self.DEFAULT_HEADERS,
                timeout=aiohttp.ClientTimeout(total=self.DEFAULT_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status != HTTPStatus.OK:
                    logger.warning("%s fetch returned HTTP %d for %s", self.platform_label, resp.status, url)
                    return None
                return await resp.text()
        except Exception:
            logger.exception("Error fetching HTML for %s from %s", self.platform_label, url)
            return None

    async def _resolve_redirect(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> str | None:
        """Follow HTTP redirects and return final resolved URL."""
        try:
            async with session.get(
                url,
                headers=headers or self._config.headers or self.DEFAULT_HEADERS,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=self.DEFAULT_TIMEOUT_SECONDS),
            ) as resp:
                return str(resp.url)
        except Exception:
            logger.exception("Failed to follow %s redirect for %s", self.platform_label, url)
            return None

    def _build_metrics_footer(
        self,
        *metrics: tuple[str, int | str | None],
        prefix: str = "",
        include_zero: bool = False,
    ) -> str:
        """Format (emoji, value) pairs into a standardized footer string."""
        parts: list[str] = []
        for emoji, val in metrics:
            if val is None:
                continue
            if isinstance(val, int):
                if val or include_zero:
                    parts.append(f"{emoji} {format_number(val)}")
            elif val:
                parts.append(f"{emoji} {val}")

        if not parts:
            return ""
        joined = " | ".join(parts)
        return f"{prefix}{joined}" if prefix else joined

    def _find_key_in_json(self, data: Any, target_key: str) -> Any:
        """Recursively find target_key in nested dicts/lists."""
        match data:
            case dict() if target_key in data:
                return data[target_key]
            case dict():
                items = data.values()
            case list():
                items = data
            case _:
                return None

        for item in items:
            result = self._find_key_in_json(item, target_key)
            if result is not None:
                return result
        return None
