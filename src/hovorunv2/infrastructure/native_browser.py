"""Lazy synchronous browser extraction with owned Chromium and bounded media downloads."""

import html
from collections.abc import Callable
from threading import BoundedSemaphore
from typing import Protocol
from urllib.parse import urlsplit

from DrissionPage.errors import BrowserConnectError, PageDisconnectedError

from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.errors import BrowserError
from hovorunv2.infrastructure.browser_process import OwnedChromium
from hovorunv2.infrastructure.native_http import NativeHTTPClient

DEFAULT_BROWSER_TIMEOUT_SECONDS = 30
SELECTOR_TIMEOUT_SECONDS = 10
MAX_BROWSER_ATTEMPTS = 2
MAX_BROWSER_HTML_BYTES = 2 * 1024 * 1024
MAX_BROWSER_MEDIA_BYTES = 50 * 1024 * 1024


class BrowserTabSettings(Protocol):
    """The narrow public cookie-setting operation used before browser navigation."""

    def cookies(self, cookie: dict[str, object], /) -> None:
        """Set one transient browser cookie without retaining it outside this process."""
        ...


class BrowserTab(Protocol):
    """Public DrissionPage tab operations needed by extraction callbacks."""

    @property
    def html(self) -> str:
        """Return current page HTML."""
        ...

    @property
    def user_agent(self) -> str:
        """Return the browser's HTTP user agent."""
        ...

    @property
    def set(self) -> BrowserTabSettings:
        """Expose the public tab settings API."""
        ...

    def get(self, url: str, *, timeout: float) -> bool | None:
        """Navigate once with a finite page timeout."""
        ...

    def ele(self, locator: str, *, timeout: float) -> object:
        """Wait for a provider selector within its bounded timeout."""
        ...

    def cookies(self, *, all_domains: bool = False, all_info: bool = False) -> list[dict[str, object]]:
        """Return transient browser cookies for domain-scoped HTTP transfer."""
        ...

    def run_js(self, script: str) -> object:
        """Execute a provider's configured extraction script."""
        ...

    def close(self) -> None:
        """Close the isolated extraction tab."""
        ...


def cookies_for_url(cookies: list[dict[str, object]], url: str) -> dict[str, str]:
    """Transfer only cookies applicable to the destination host, path and transport."""
    target = urlsplit(url)
    hostname = target.hostname or ""
    target_path = target.path or "/"
    result = {}
    for cookie in cookies:
        name, value, domain = cookie.get("name"), cookie.get("value"), cookie.get("domain")
        path = cookie.get("path", "/")
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or not isinstance(domain, str)
            or not isinstance(path, str)
        ):
            continue
        domain_matches = hostname == domain or (
            domain.startswith(".") and (hostname == domain[1:] or hostname.endswith(domain))
        )
        path_matches = target_path == path or (
            target_path.startswith(path) and (path.endswith("/") or target_path[len(path) :].startswith("/"))
        )
        if domain_matches and path_matches and (not cookie.get("secure") or target.scheme == "https"):
            result[name] = value
    return result


def download_tab_media(tab: BrowserTab, payload: RichMediaPayload, referer: str) -> list[bytes]:
    """Download with browser cookies in memory, preserving media alignment when individual downloads fail."""
    cookies = tab.cookies(all_domains=True, all_info=True)
    downloaded = []
    total_bytes = 0
    headers = {"Referer": referer, "User-Agent": tab.user_agent}
    with NativeHTTPClient() as http:
        for item in payload.media_items:
            if not item.url:
                continue
            url = html.unescape(item.url)
            content = http.download(url, headers, cookies=cookies_for_url(cookies, url))
            if content is not None:
                total_bytes += len(content)
                if total_bytes > MAX_BROWSER_MEDIA_BYTES:
                    msg = "Browser media exceeds its aggregate byte limit."
                    raise BrowserError(msg)
                item.data = content
                downloaded.append(content)
    # Existing async callers zip a complete byte list onto items. An incomplete list would shift media bytes.
    return downloaded if len(downloaded) == len(payload.media_items) else []


class NativeBrowserService:
    """Perform native worker browser I/O without an event loop or inherited browser resources."""

    def __init__(self, max_tabs: int = 1, flags: list[str] | None = None) -> None:
        if max_tabs <= 0:
            msg = "Browser tab capacity must be positive."
            raise ValueError(msg)
        self._browser = OwnedChromium(flags)
        self._semaphore = BoundedSemaphore(max_tabs)

    @property
    def is_running(self) -> bool:
        """Observe child-owned browser state without launching it."""
        return self._browser.is_running

    def get_content(self, url: str, wait_selector: str | None = None) -> str:
        """Fetch one tab synchronously, retrying only a disconnected browser once."""
        with self._semaphore:
            for attempt in range(MAX_BROWSER_ATTEMPTS):
                try:
                    return self._fetch(url, wait_selector)
                except BrowserConnectError, PageDisconnectedError, ConnectionError:
                    self._browser.close()
                    if attempt == MAX_BROWSER_ATTEMPTS - 1:
                        msg = "Browser content is unavailable."
                        raise BrowserError(msg) from None
        msg = "Browser content is unavailable."
        raise BrowserError(msg)

    def _fetch(self, url: str, wait_selector: str | None) -> str:
        page = self._browser.start()
        tab: BrowserTab = page.new_tab()
        try:
            self._navigate(tab, url, wait_selector)
            content = tab.html
            if len(content.encode()) > MAX_BROWSER_HTML_BYTES:
                msg = "Browser HTML exceeds its byte limit."
                raise BrowserError(msg)
            return content
        finally:
            tab.close()

    def _navigate(self, tab: BrowserTab, url: str, wait_selector: str | None) -> None:
        if not tab.get(url, timeout=DEFAULT_BROWSER_TIMEOUT_SECONDS):
            msg = "Browser navigation failed."
            raise BrowserError(msg)
        if wait_selector:
            tab.ele(wait_selector, timeout=SELECTOR_TIMEOUT_SECONDS)

    def extract_and_download(
        self,
        url: str,
        extractor_fn: Callable[[BrowserTab, str], RichMediaPayload | None],
        wait_selector: str | None = None,
        cookies: list[dict[str, object]] | None = None,
    ) -> tuple[RichMediaPayload | None, list[bytes]]:
        """Extract in an isolated tab and download through a fresh bounded cookie-bearing HTTP session."""
        with self._semaphore:
            page = self._browser.start()
            tab: BrowserTab = page.new_tab()
            try:
                for cookie in cookies or ():
                    tab.set.cookies(cookie)
                self._navigate(tab, url, wait_selector)
                payload = extractor_fn(tab, url)
                if payload is None:
                    return None, []
                return payload, download_tab_media(tab, payload, url)
            finally:
                tab.close()

    def close(self) -> None:
        """Deterministically close browser drivers, reap Chromium, and remove its temporary profile."""
        self._browser.close()
