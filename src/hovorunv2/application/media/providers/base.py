"""Configuration-driven base class for all media provider clients."""

import logging
import re
import time
from collections.abc import Mapping
from functools import partial
from http import HTTPStatus
from typing import ClassVar, Self

import aiohttp

from hovorunv2.application.jobs.http_ports import (
    HTTPTransportError,
    SyncBrowserPort,
    SyncHTTPPort,
)
from hovorunv2.application.media.browser import BrowserPort, BrowserTab
from hovorunv2.application.media.errors import (
    BrowserContinuationRequired,
    BrowserError,
    MediaExtractionError,
)
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.application.media.provider_config import (
    FACEBOOK_CONFIG,
    BrowserFallbackConfig,
    OGConfig,
    ProviderConfig,
)
from hovorunv2.application.media.provider_parsing import MediaParser
from hovorunv2.domain.enums import CommandName
from hovorunv2.utils.metadata import extract_og_metadata

__all__ = ["FACEBOOK_CONFIG", "BaseMediaClient", "BrowserFallbackConfig", "OGConfig", "ProviderConfig"]

logger = logging.getLogger(__name__)


class BaseMediaClient(MediaParser):
    """Generalized media client executing config-driven extraction, OG/browser fallbacks, and post-processing."""

    DEFAULT_TIMEOUT_SECONDS: ClassVar[int] = 15
    DEFAULT_HEADERS: ClassVar[Mapping[str, str]] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(
        self,
        config: ProviderConfig,
        http: aiohttp.ClientSession | None = None,
        browser: BrowserPort | None = None,
    ) -> None:
        """Initialize client with its provider config and shared infrastructure."""
        self._config = config
        self._http = http
        self._browser = browser

    @classmethod
    def for_worker(cls, config: ProviderConfig) -> Self:
        """Create a provider client for synchronous worker extraction."""
        instance = cls.__new__(cls)
        instance.init_worker(config)
        return instance

    def init_worker(self, config: ProviderConfig) -> None:
        """Initialize worker client without async infrastructure dependencies."""
        self._config = config
        self._http = None
        self._browser = None

    def _require_async_http(self) -> aiohttp.ClientSession:
        if self._http is None:
            msg = "Async HTTP client is not available in synchronous worker mode."
            raise ValueError(msg)
        return self._http

    def _require_browser(self) -> BrowserPort:
        if self._browser is None:
            msg = "Browser is not available in synchronous worker mode."
            raise ValueError(msg)
        return self._browser

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

    @property
    def has_browser_fallback(self) -> bool:
        """Whether this provider requires headless browser continuation."""
        return bool(self._config.browser_config)

    async def extract(self, url: str) -> RichMediaPayload | None:
        """Execute direct API extraction.

        Args:
            url: Media URL to extract.

        Returns:
            Parsed provider payload, or None when the URL is not handled.

        Raises:
            MediaExtractionError: If the provider returns unusable extraction metadata.
        """
        if self._config.post_url_pattern and not self._config.post_url_pattern.search(url):
            return None

        resolved_url = await self.resolve_url(url)
        if not (match := self._config.pattern.search(resolved_url)):
            return None

        try:
            payload = await self._extract_raw_payload(resolved_url, match)
        except MediaExtractionError:
            raise
        except KeyError, TypeError, ValueError, IndexError, AttributeError:
            msg = "Provider returned unusable extraction metadata."
            raise MediaExtractionError(msg) from None
        if payload is None:
            return None
        return self._require_usable_payload(payload)

    async def extract_metadata(self, url: str) -> RichMediaPayload | None:
        """Execute OpenGraph/HTML metadata extraction as the final fallback.

        Args:
            url: Media URL to extract.

        Returns:
            Parsed provider payload, or None when no OpenGraph configuration exists.
        """
        if self._config.og_config is None:
            return None

        resolved_url = await self.resolve_url(url)
        return await self._extract_via_og(resolved_url, self._config.og_config)

    async def resolve_url(self, url: str) -> str:
        """Resolve redirects when configured on the provider.

        Args:
            url: URL to resolve.

        Returns:
            Resolved URL, or the original URL when no redirect pattern matches.
        """
        if self._config.redirect_pattern and self._config.redirect_pattern.search(url):
            return await self._resolve_redirect(url, headers=self._config.headers or None)
        return url

    async def _extract_raw_payload(
        self,
        url: str,
        match: re.Match[str],
    ) -> RichMediaPayload | None:
        """Override in subclasses for provider-specific raw extraction."""
        return None

    def _require_usable_payload(self, payload: RichMediaPayload) -> RichMediaPayload:
        self._require_provider_fields(payload)
        if not payload.content and not payload.media_items and payload.quoted_payload is None:
            msg = "Provider returned unusable extraction metadata."
            raise MediaExtractionError(msg)
        return payload

    def _require_provider_fields(self, payload: RichMediaPayload) -> None:
        msg = "Provider returned unusable extraction metadata."
        for value in (
            payload.author_name,
            payload.author_handle,
            payload.author_url,
            payload.content,
            payload.footer_text,
            payload.original_url,
        ):
            if not isinstance(value, str):
                raise MediaExtractionError(msg)
        for item in payload.media_items:
            if not isinstance(item.url, str) or not isinstance(item.is_video, bool):
                raise MediaExtractionError(msg)
            if item.data is not None and not isinstance(item.data, bytes):
                raise MediaExtractionError(msg)
            if not item.url and not item.data:
                raise MediaExtractionError(msg)
        if payload.quoted_payload is not None:
            self._require_provider_fields(payload.quoted_payload)

    def get_ytdlp_cookies(self) -> dict[str, str] | None:
        """Return optional cookies to pass to yt-dlp fallback."""
        return None

    def get_browser_cookies(self) -> list[dict[str, object]]:
        """Return optional cookies to inject into headless browser fallback."""
        return []

    async def extract_fallback(self, url: str) -> RichMediaPayload | None:
        """Perform config-driven headless browser fallback when configured.

        Args:
            url: Media URL to extract.

        Returns:
            Parsed provider payload, or None when no browser fallback is configured.

        Raises:
            MediaExtractionError: If the browser extraction fails.
        """
        b_cfg = self._config.browser_config
        if not b_cfg:
            return None

        browser = self._require_browser()
        try:
            payload, media_bytes = await browser.extract_and_download(
                url,
                extractor_fn=partial(self._extract_from_browser_tab, b_cfg=b_cfg),
                wait_selector=b_cfg.wait_selector,
                cookies=self.get_browser_cookies(),
            )
            if not payload:
                return None
            for item, content in zip(payload.media_items, media_bytes, strict=False):
                if isinstance(content, bytes):
                    item.data = content
        except BrowserError, MediaExtractionError:
            msg = "Provider browser extraction failed."
            raise MediaExtractionError(msg) from None
        else:
            return self._require_usable_payload(payload)

    def extract_sync(self, url: str, http: SyncHTTPPort) -> RichMediaPayload | None:
        """Execute direct API extraction synchronously.

        Args:
            url: Media URL to extract.
            http: Synchronous HTTP port for the request.

        Returns:
            Parsed provider payload, or None when the URL is not handled.

        Raises:
            MediaExtractionError: If the provider returns unusable extraction metadata.
        """
        if self._config.post_url_pattern and not self._config.post_url_pattern.search(url):
            return None

        resolved_url = self.resolve_url_sync(url, http)
        if not (match := self._config.pattern.search(resolved_url)):
            return None

        try:
            payload = self._extract_raw_payload_sync(resolved_url, match, http)
        except MediaExtractionError:
            raise
        except KeyError, TypeError, ValueError, IndexError, AttributeError:
            msg = "Provider returned unusable extraction metadata."
            raise MediaExtractionError(msg) from None
        if payload is None:
            return None
        return self._require_usable_payload(payload)

    def extract_metadata_sync(self, url: str, http: SyncHTTPPort) -> RichMediaPayload | None:
        """Execute OpenGraph/HTML metadata extraction synchronously as the final fallback.

        Args:
            url: Media URL to extract.
            http: Synchronous HTTP port for the request.

        Returns:
            Parsed provider payload, or None when no OpenGraph configuration exists.
        """
        if self._config.og_config is None:
            return None

        resolved_url = self.resolve_url_sync(url, http)
        return self._extract_via_og_sync(resolved_url, self._config.og_config, http)

    def resolve_url_sync(self, url: str, http: SyncHTTPPort) -> str:
        """Resolve redirects synchronously when configured on the provider.

        Args:
            url: URL to resolve.
            http: Synchronous HTTP port for the request.

        Returns:
            Resolved URL, or the original URL when no redirect pattern matches.
        """
        if self._config.redirect_pattern and self._config.redirect_pattern.search(url):
            return self._resolve_redirect_sync(http, url, headers=self._config.headers or None)
        return url

    def extract_media_sync(
        self,
        url: str,
        http: SyncHTTPPort,
        extractor: NativeMediaExtractor,
    ) -> RichMediaPayload:
        """Execute multi-tier extraction synchronously in the media stage.

        Args:
            url: Media URL to extract.
            http: Synchronous HTTP port for the request.
            extractor: Native media extractor for the generic backend strategy.

        Returns:
            Parsed provider payload.

        Raises:
            BrowserContinuationRequired: If browser continuation is needed after non-browser tiers miss.
            MediaExtractionError: If every extraction strategy is exhausted.
        """
        cleaned_url = url.rstrip(".,")

        try:
            payload = self.extract_sync(cleaned_url, http)
            if payload is not None:
                return payload
        except MediaExtractionError:
            logger.info("Direct extraction strategy failed for provider %s", self.name)

        is_post_url = bool(self._config.post_url_pattern and self._config.post_url_pattern.search(cleaned_url))
        if not is_post_url:
            try:
                payload = extractor.extract(cleaned_url, cookies=self.get_ytdlp_cookies())
                if payload is not None:
                    return payload
            except MediaExtractionError:
                logger.info("Generic extraction strategy failed for provider %s", self.name)

        try:
            payload = self._extract_non_browser_fallback_sync(cleaned_url, http)
            if payload is not None:
                return payload
        except MediaExtractionError:
            logger.info("Non-browser fallback strategy failed for provider %s", self.name)

        if self.has_browser_fallback:
            raise BrowserContinuationRequired

        try:
            payload = self.extract_metadata_sync(cleaned_url, http)
            if payload is not None:
                return payload
        except MediaExtractionError:
            logger.info("Metadata extraction strategy failed for provider %s", self.name)

        msg = "Media extraction strategies are exhausted."
        raise MediaExtractionError(msg)

    def _extract_non_browser_fallback_sync(self, url: str, http: SyncHTTPPort) -> RichMediaPayload | None:
        """Override in subclasses for non-browser synchronous fallbacks (e.g. instaloader)."""
        return None

    def extract_browser_media_sync(
        self,
        url: str,
        http: SyncHTTPPort,
        browser: SyncBrowserPort,
    ) -> RichMediaPayload:
        """Execute browser-stage extraction across the browser and OpenGraph tiers.

        Args:
            url: Media URL to extract.
            http: Synchronous HTTP port for the request.
            browser: Synchronous browser port for the browser tier.

        Returns:
            Parsed provider payload.

        Raises:
            MediaExtractionError: If every extraction strategy is exhausted.
        """
        cleaned_url = url.rstrip(".,")
        try:
            payload = self._extract_browser_sync(cleaned_url, browser)
            if payload is not None:
                return payload
        except MediaExtractionError:
            logger.info("Browser extraction strategy failed for provider %s", self.name)

        try:
            payload = self.extract_metadata_sync(cleaned_url, http)
            if payload is not None:
                return payload
        except MediaExtractionError:
            logger.info("Metadata extraction strategy failed for provider %s", self.name)

        msg = "Media extraction strategies are exhausted."
        raise MediaExtractionError(msg)

    def _extract_browser_sync(self, url: str, browser: SyncBrowserPort) -> RichMediaPayload | None:
        """Perform config-driven headless browser extraction synchronously."""
        b_cfg = self._config.browser_config
        if not b_cfg:
            return None

        try:
            payload, media_bytes = browser.extract_and_download(
                url,
                extractor_fn=partial(self._extract_from_browser_tab, b_cfg=b_cfg),
                wait_selector=b_cfg.wait_selector,
                cookies=self.get_browser_cookies(),
            )
            if not payload:
                return None
            for item, content in zip(payload.media_items, media_bytes, strict=False):
                if isinstance(content, bytes):
                    item.data = content
        except BrowserError, MediaExtractionError:
            msg = "Provider browser extraction failed."
            raise MediaExtractionError(msg) from None
        else:
            return self._require_usable_payload(payload)

    # Compatibility aliases
    extract_payload = extract
    extract_metadata_payload = extract_metadata
    extract_payload_sync = extract_sync
    extract_metadata_payload_sync = extract_metadata_sync

    def _extract_from_browser_tab(
        self, tab: BrowserTab, url: str, b_cfg: BrowserFallbackConfig
    ) -> RichMediaPayload | None:
        """Extract metadata from browser tab via JS, JSON scripts, or OG tags."""
        self._dismiss_gate_buttons(tab, b_cfg)
        if payload := self._extract_browser_script(tab, url, b_cfg):
            return payload

        if script_results := self._extract_from_json_scripts(tab.html):
            return self._build_browser_payload(script_results, url, b_cfg)

        og_meta = extract_og_metadata(tab.html)
        if og_meta.get("video") or og_meta.get("image"):
            return self._build_browser_payload(og_meta, url, b_cfg)
        return None

    def _dismiss_gate_buttons(self, tab: BrowserTab, b_cfg: BrowserFallbackConfig) -> None:
        for btn_text in b_cfg.gate_buttons:
            try:
                if btn := tab.ele(f"text={btn_text}", timeout=1):
                    btn.click()
                    time.sleep(1)
            except BrowserError, OSError:
                logger.debug("Gate button '%s' not clickable", btn_text)

    def _extract_browser_script(
        self, tab: BrowserTab, url: str, b_cfg: BrowserFallbackConfig
    ) -> RichMediaPayload | None:
        if b_cfg.js_extractor:
            try:
                js_results = tab.run_js(b_cfg.js_extractor)
                if isinstance(js_results, dict) and (js_results.get("video") or js_results.get("image")):
                    return self._build_browser_payload(js_results, url, b_cfg)
            except BrowserError, OSError, ValueError, TypeError:
                logger.warning("JS extraction failed for %s", self.name)

        return None

    def _extract_raw_payload_sync(
        self,
        url: str,
        match: re.Match[str],
        http: SyncHTTPPort,
    ) -> RichMediaPayload | None:
        """Override in subclasses for provider-specific synchronous raw extraction."""
        return None

    async def _extract_via_og(self, url: str, og_cfg: OGConfig) -> RichMediaPayload | None:
        """Fetch HTML and construct raw RichMediaPayload from OpenGraph metadata."""
        html_content = await self._fetch_html(url, headers=og_cfg.headers)
        if not html_content:
            return None
        return self._parse_og_html(html_content, url, og_cfg)

    def _extract_via_og_sync(self, url: str, og_cfg: OGConfig, http: SyncHTTPPort) -> RichMediaPayload | None:
        """Fetch HTML and construct raw RichMediaPayload from OpenGraph metadata synchronously."""
        html_content = self._fetch_html_sync(http, url, headers=og_cfg.headers)
        if not html_content:
            return None
        return self._parse_og_html(html_content, url, og_cfg)

    async def _fetch_json(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> object:
        """Require decoded provider JSON; malformed or unavailable I/O raises a safe error."""
        http = self._require_async_http()
        try:
            async with http.get(
                url,
                params=params,
                headers=headers or self._config.headers or None,
                timeout=aiohttp.ClientTimeout(total=self.DEFAULT_TIMEOUT_SECONDS),
            ) as resp:
                self._require_success(resp.status)
                return await resp.json()
        except aiohttp.ClientError, OSError, ValueError:
            msg = "Provider JSON request failed."
            raise MediaExtractionError(msg) from None

    def _fetch_json_sync(
        self,
        http: SyncHTTPPort,
        url: str,
        *,
        params: Mapping[str, str | int | float | bool] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> object:
        """Require decoded provider JSON synchronously; transport failures raise MediaExtractionError."""
        try:
            return http.read_json(url, params=params, headers=headers or self._config.headers or None)
        except HTTPTransportError:
            msg = "Provider JSON request failed."
            raise MediaExtractionError(msg) from None

    async def _fetch_html(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        """Require provider HTML without exposing request-bearing backend diagnostics."""
        http = self._require_async_http()
        try:
            async with http.get(
                url,
                headers=headers or self._config.headers or self.DEFAULT_HEADERS,
                timeout=aiohttp.ClientTimeout(total=self.DEFAULT_TIMEOUT_SECONDS),
            ) as resp:
                self._require_success(resp.status)
                return await resp.text()
        except aiohttp.ClientError, OSError, ValueError:
            msg = "Provider HTML request failed."
            raise MediaExtractionError(msg) from None

    def _fetch_html_sync(
        self,
        http: SyncHTTPPort,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        """Require provider HTML synchronously without exposing backend diagnostics."""
        try:
            return http.read_text(url, headers=headers or self._config.headers or self.DEFAULT_HEADERS)
        except HTTPTransportError:
            msg = "Provider HTML request failed."
            raise MediaExtractionError(msg) from None

    async def _resolve_redirect(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        """Require a successful resolved URL, preserving configured request headers."""
        http = self._require_async_http()
        try:
            async with http.get(
                url,
                headers=headers or self._config.headers or self.DEFAULT_HEADERS,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=self.DEFAULT_TIMEOUT_SECONDS),
            ) as resp:
                self._require_success(resp.status)
                return str(resp.url)
        except aiohttp.ClientError, OSError, ValueError:
            msg = "Provider redirect request failed."
            raise MediaExtractionError(msg) from None

    def _resolve_redirect_sync(
        self,
        http: SyncHTTPPort,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        """Require a successful resolved URL synchronously, preserving configured request headers."""
        try:
            return http.resolve(url, headers=headers or self._config.headers or self.DEFAULT_HEADERS)
        except HTTPTransportError:
            msg = "Provider redirect request failed."
            raise MediaExtractionError(msg) from None

    def _require_success(self, status: int) -> None:
        if status != HTTPStatus.OK:
            msg = "Provider request did not return a usable response."
            raise MediaExtractionError(msg)
