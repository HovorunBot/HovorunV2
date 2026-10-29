"""Configuration-driven base class for all media provider clients."""

import logging
import re
import time
from collections.abc import Mapping
from functools import partial
from http import HTTPStatus
from typing import Any, ClassVar, Protocol

import aiohttp

from hovorunv2.application.clients.config import (
    FACEBOOK_CONFIG,
    BrowserFallbackConfig,
    OGConfig,
    ProviderConfig,
)
from hovorunv2.application.clients.parsing import MediaParser
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.ports import BrowserPort
from hovorunv2.application.text import extract_og_metadata
from hovorunv2.domain.enums import CommandName

__all__ = ["FACEBOOK_CONFIG", "BaseMediaClient", "BrowserFallbackConfig", "OGConfig", "ProviderConfig"]

logger = logging.getLogger(__name__)


class _BrowserElement(Protocol):
    def click(self) -> None: ...


class _BrowserTab(Protocol):
    @property
    def html(self) -> str: ...

    def ele(self, locator: str, *, timeout: int) -> _BrowserElement | None: ...

    def run_js(self, script: str) -> object: ...


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
        http: aiohttp.ClientSession,
        browser: BrowserPort,
    ) -> None:
        """Initialize client with its provider config and shared infrastructure."""
        self._config = config
        self._http = http
        self._browser = browser

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

    async def extract(self, url: str) -> RichMediaPayload | None:
        """Execute direct API extraction."""
        if self._config.post_url_pattern and not self._config.post_url_pattern.search(url):
            return None

        resolved_url = await self.resolve_url(url)
        if not resolved_url:
            return None

        if not (match := self._config.pattern.search(resolved_url)):
            return None

        return await self._extract_raw_payload(resolved_url, match)

    async def extract_metadata(self, url: str) -> RichMediaPayload | None:
        """Execute OpenGraph / HTML metadata extraction as final fallback."""
        if self._config.og_config is None:
            return None

        resolved_url = await self.resolve_url(url)
        if not resolved_url:
            return None

        return await self._extract_via_og(resolved_url, self._config.og_config)

    async def resolve_url(self, url: str) -> str | None:
        """Resolve redirects when configured on the provider."""
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

    def get_ytdlp_cookies(self) -> dict[str, str] | None:
        """Return optional cookies to pass to yt-dlp fallback."""
        return None

    def get_browser_cookies(self) -> list[dict[str, Any]]:
        """Return optional cookies to inject into headless browser fallback."""
        return []

    async def extract_fallback(self, url: str) -> RichMediaPayload | None:
        """Perform config-driven headless browser fallback when configured."""
        b_cfg = self._config.browser_config
        if not b_cfg:
            return None

        try:
            payload, media_bytes = await self._browser.extract_and_download(
                url,
                extractor_fn=partial(self._extract_from_browser_tab, b_cfg=b_cfg),
                wait_selector=b_cfg.wait_selector,
                cookies=self.get_browser_cookies(),
            )
            if not payload:
                return None
            for item, content in zip(payload.media_items, media_bytes, strict=False):
                item.data = content
        except Exception:
            logger.exception("Browser fallback failed for %s", self.name)
            return None
        else:
            return payload

    # Compatibility aliases
    extract_payload = extract
    extract_metadata_payload = extract_metadata

    def _extract_from_browser_tab(
        self, tab: _BrowserTab, url: str, b_cfg: BrowserFallbackConfig
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

    def _dismiss_gate_buttons(self, tab: _BrowserTab, b_cfg: BrowserFallbackConfig) -> None:
        for btn_text in b_cfg.gate_buttons:
            try:
                if btn := tab.ele(f"text={btn_text}", timeout=1):
                    btn.click()
                    time.sleep(1)
            except Exception:
                logger.debug("Gate button '%s' not clickable", btn_text)

    def _extract_browser_script(
        self, tab: _BrowserTab, url: str, b_cfg: BrowserFallbackConfig
    ) -> RichMediaPayload | None:
        if b_cfg.js_extractor:
            try:
                js_results = tab.run_js(b_cfg.js_extractor)
                if isinstance(js_results, dict) and (js_results.get("video") or js_results.get("image")):
                    return self._build_browser_payload(js_results, url, b_cfg)
            except Exception:
                logger.exception("JS extraction failed for %s", self.name)

        return None

    async def _extract_via_og(self, url: str, og_cfg: OGConfig) -> RichMediaPayload | None:
        """Fetch HTML and construct raw RichMediaPayload from OpenGraph metadata."""
        html_content = await self._fetch_html(url, headers=og_cfg.headers)
        if not html_content:
            return None
        return self._parse_og_html(html_content, url, og_cfg)

    async def _fetch_json(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Perform GET request and return parsed JSON or None on failure."""
        try:
            async with self._http.get(
                url,
                params=params,
                headers=headers or self._config.headers or None,
                timeout=aiohttp.ClientTimeout(total=self.DEFAULT_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status != HTTPStatus.OK:
                    logger.warning("%s API returned HTTP %d", self.name, resp.status)
                    return None
                return await resp.json()
        except Exception:
            logger.exception("Error fetching provider JSON for %s", self.name)
            return None

    async def _fetch_html(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> str | None:
        """Perform GET request and return HTML text or None on failure."""
        try:
            async with self._http.get(
                url,
                headers=headers or self._config.headers or self.DEFAULT_HEADERS,
                timeout=aiohttp.ClientTimeout(total=self.DEFAULT_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status != HTTPStatus.OK:
                    logger.warning("%s fetch returned HTTP %d", self.name, resp.status)
                    return None
                return await resp.text()
        except Exception:
            logger.exception("Error fetching provider HTML for %s", self.name)
            return None

    async def _resolve_redirect(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> str | None:
        """Follow HTTP redirects and return final resolved URL."""
        try:
            async with self._http.get(
                url,
                headers=headers or self._config.headers or self.DEFAULT_HEADERS,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=self.DEFAULT_TIMEOUT_SECONDS),
            ) as resp:
                return str(resp.url)
        except Exception:
            logger.exception("Failed to follow provider redirect for %s", self.name)
            return None
