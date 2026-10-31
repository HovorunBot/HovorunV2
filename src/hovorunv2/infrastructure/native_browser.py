"""Lazy synchronous browser extraction with owned Chromium and bounded media downloads."""

import html
from collections.abc import Callable
from threading import BoundedSemaphore
from urllib.parse import SplitResult, urlsplit

from DrissionPage.errors import BaseError, BrowserConnectError, PageDisconnectedError

from hovorunv2.application.browser import BrowserTab
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.errors import BrowserError, MediaFailure
from hovorunv2.application.worker_io import HTTPFailure, HTTPTransportError
from hovorunv2.infrastructure.browser_process import OwnedChromium
from hovorunv2.infrastructure.native_http import NativeHTTPClient

DEFAULT_BROWSER_TIMEOUT_SECONDS = 30
SELECTOR_TIMEOUT_SECONDS = 10
MAX_BROWSER_ATTEMPTS = 2
MAX_BROWSER_HTML_BYTES = 2 * 1024 * 1024
MAX_BROWSER_MEDIA_BYTES = 50 * 1024 * 1024


def cookies_for_url(cookies: list[dict[str, object]], url: str) -> dict[str, str]:
    """Transfer only cookies applicable to the destination host, path and transport."""
    target = urlsplit(url)
    result = {}
    for cookie in cookies:
        pair = _cookie_pair(cookie, target)
        if pair is None:
            continue
        name, value = pair
        result[name] = value
    return result


def _domain_applies(hostname: str, domain: str) -> bool:
    if hostname == domain:
        return True
    if not domain.startswith("."):
        return False
    return hostname == domain[1:] or hostname.endswith(domain)


def _path_applies(target: str, path: str) -> bool:
    if target == path:
        return True
    if not target.startswith(path):
        return False
    return path.endswith("/") or target[len(path) :].startswith("/")


def _cookie_pair(cookie: dict[str, object], target: SplitResult) -> tuple[str, str] | None:
    name, value = cookie.get("name"), cookie.get("value")
    domain, path = cookie.get("domain"), cookie.get("path", "/")
    if not isinstance(name, str) or not isinstance(value, str):
        return None
    if not isinstance(domain, str) or not isinstance(path, str):
        return None
    if not _domain_applies(target.hostname or "", domain):
        return None
    if not _path_applies(target.path or "/", path):
        return None
    if cookie.get("secure") and target.scheme != "https":
        return None
    return name, value


def download_tab_media(tab: BrowserTab, payload: RichMediaPayload, referer: str) -> list[bytes | MediaFailure]:
    """Keep every media position explicit, retaining successful transient bytes on the payload."""
    cookies = tab.cookies(all_domains=True, all_info=True)
    downloaded: list[bytes | MediaFailure] = []
    total_bytes = 0
    headers = {"Referer": referer, "User-Agent": tab.user_agent}
    with NativeHTTPClient() as http:
        for item in payload.media_items:
            if not item.url:
                downloaded.append(MediaFailure.UNUSABLE)
                continue
            url = html.unescape(item.url)
            try:
                content = http.download_bytes(url, headers, cookies=cookies_for_url(cookies, url))
            except HTTPTransportError as error:
                code = MediaFailure.TOO_LARGE if error.code == HTTPFailure.TOO_LARGE else MediaFailure.UNAVAILABLE
                downloaded.append(code)
                continue
            if not content:
                downloaded.append(MediaFailure.UNUSABLE)
                continue
            total_bytes += len(content)
            if total_bytes > MAX_BROWSER_MEDIA_BYTES:
                msg = "Browser media exceeds its aggregate byte limit."
                raise BrowserError(msg)
            item.data = content
            downloaded.append(content)
    return downloaded


class NativeBrowserService:
    """Perform native worker browser I/O without an event loop or inherited browser resources."""

    def __init__(self, max_tabs: int = 1, flags: list[str] | None = None) -> None:
        if max_tabs <= 0:
            msg = "Browser tab capacity must be positive."
            raise ValueError(msg)
        self._browser = OwnedChromium(flags, terminate_with_parent=True)
        self._semaphore = BoundedSemaphore(max_tabs)

    @property
    def is_running(self) -> bool:
        """Observe child-owned browser state without launching it."""
        return self._browser.is_running

    def get_content(self, url: str, wait_selector: str | None = None) -> str:
        """Fetch one tab synchronously, retrying only a disconnected browser once."""
        with self._semaphore:
            return self._fetch_with_retries(url, wait_selector)

    def _fetch_with_retries(self, url: str, wait_selector: str | None) -> str:
        for attempt in range(MAX_BROWSER_ATTEMPTS):
            try:
                return self._fetch(url, wait_selector)
            except BrowserConnectError, PageDisconnectedError, ConnectionError:
                self._browser.close()
                if attempt == MAX_BROWSER_ATTEMPTS - 1:
                    msg = "Browser content is unavailable."
                    raise BrowserError(msg) from None
            except BaseError, OSError:
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
    ) -> tuple[RichMediaPayload | None, list[bytes | MediaFailure]]:
        """Extract in an isolated tab and download through a fresh bounded cookie-bearing HTTP session."""
        with self._semaphore:
            try:
                return self._extract_attempt(url, extractor_fn, wait_selector, cookies)
            except BaseError, OSError:
                msg = "Browser extraction is unavailable."
                raise BrowserError(msg) from None

    def _extract_attempt(
        self,
        url: str,
        extractor_fn: Callable[[BrowserTab, str], RichMediaPayload | None],
        wait_selector: str | None,
        cookies: list[dict[str, object]] | None,
    ) -> tuple[RichMediaPayload | None, list[bytes | MediaFailure]]:
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
