"""Lazy synchronous browser extraction with owned Chromium and bounded media downloads."""

import html
import logging
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from threading import BoundedSemaphore
from time import monotonic
from urllib.parse import SplitResult, urlsplit

lazy from pydoll.exceptions import (
    BrowserNotRunning,
    ConnectionException,
    PageLoadTimeout,
    PydollException,
    TargetCrashed,
)
lazy from pydoll.sync import Chrome, SyncError

from hovorunv2.application.jobs.http_ports import HTTPFailure, HTTPTransportError
from hovorunv2.application.media.browser import BrowserTab
from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import BrowserError, MediaFailure
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.infrastructure.browser.bounded_tab import BoundedBrowserTab
from hovorunv2.infrastructure.browser.browser_process import OwnedChromium
from hovorunv2.infrastructure.browser.operation_timeout import BrowserOperationTimeout
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
lazy from hovorunv2.infrastructure.http_transport.extraction_http import ExtractionHTTPClient

DEFAULT_BROWSER_TIMEOUT_SECONDS = 30
SELECTOR_TIMEOUT_SECONDS = 10
CONTEXT_CLEANUP_SECONDS = 3
MAX_BROWSER_ATTEMPTS = 2
MAX_BROWSER_HTML_BYTES = 2 * 1024 * 1024
MAX_BROWSER_MEDIA_BYTES = 50 * 1024 * 1024
logger = logging.getLogger(__name__)


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


def download_tab_media(
    tab: BrowserTab,
    payload: RichMediaPayload,
    referer: str,
    *,
    deadline: ExtractionDeadline | None = None,
    allowed_hosts: frozenset[str] = frozenset(),
) -> list[bytes | MediaFailure]:
    """Keep every media position explicit, retaining successful transient bytes on the payload."""
    cookies = tab.cookies(all_domains=True, all_info=True)
    downloaded: list[bytes | MediaFailure] = []
    total_bytes = 0
    headers = {"Referer": referer, "User-Agent": tab.user_agent}
    transport = (
        NativeHTTPClient(allowed_hosts=allowed_hosts)
        if deadline is None
        else ExtractionHTTPClient(deadline, allowed_hosts=allowed_hosts)
    )
    with transport as http:
        for item in payload.media_items:
            if not item.url:
                logger.error("Browser media download has no usable source")
                downloaded.append(MediaFailure.UNUSABLE)
                continue
            url = html.unescape(item.url)
            try:
                logger.info("Browser worker starting media download")
                content = http.download_bytes(url, headers, cookies=cookies_for_url(cookies, url))
            except HTTPTransportError as error:
                logger.exception("Browser media download failed (%s)", error.code)
                code = MediaFailure.TOO_LARGE if error.code == HTTPFailure.TOO_LARGE else MediaFailure.UNAVAILABLE
                downloaded.append(code)
                continue
            if not content:
                logger.error("Browser media download returned empty content")
                downloaded.append(MediaFailure.UNUSABLE)
                continue
            total_bytes += len(content)
            if total_bytes > MAX_BROWSER_MEDIA_BYTES:
                msg = "Browser media exceeds its aggregate byte limit."
                raise BrowserError(msg)
            item.data = content
            logger.info("Browser worker downloaded %s media bytes", len(content))
            downloaded.append(content)
    return downloaded


class NativeBrowserService:
    """Perform native worker browser I/O without an event loop or inherited browser resources."""

    def __init__(
        self,
        max_tabs: int = 1,
        flags: list[str] | None = None,
        *,
        allowed_hosts: frozenset[str] = frozenset(),
    ) -> None:
        if max_tabs <= 0:
            msg = "Browser tab capacity must be positive."
            raise ValueError(msg)
        self._browser = OwnedChromium(flags, terminate_with_parent=True)
        self._semaphore = BoundedSemaphore(max_tabs)
        self._allowed_hosts = allowed_hosts

    @property
    def is_running(self) -> bool:
        """Observe child-owned browser state without launching it."""
        return self._browser.is_running

    @contextmanager
    def _session(self, deadline: ExtractionDeadline | None) -> Generator[None]:
        with ExitStack() as resources:
            if deadline is not None:
                resources.enter_context(BrowserOperationTimeout(deadline))
            resources.enter_context(self._semaphore)
            try:
                yield
            finally:
                if deadline is not None and monotonic() >= deadline.expires_at:
                    self._browser.abort()
                    self._browser.close()

    def get_content(
        self, url: str, wait_selector: str | None = None, *, deadline: ExtractionDeadline | None = None
    ) -> str:
        """Fetch one tab synchronously, retrying only a disconnected browser once."""
        with self._session(deadline):
            return self._fetch_with_retries(url, wait_selector, deadline)

    def _fetch_with_retries(self, url: str, wait_selector: str | None, deadline: ExtractionDeadline | None) -> str:
        for attempt in range(MAX_BROWSER_ATTEMPTS):
            try:
                return self._fetch(url, wait_selector, deadline)
            except ConnectionException, BrowserNotRunning, TargetCrashed, PageLoadTimeout, ConnectionError:
                self._browser.close()
                if attempt == MAX_BROWSER_ATTEMPTS - 1:
                    msg = "Browser content is unavailable."
                    raise BrowserError(msg) from None
            except PydollException, SyncError, TimeoutError, OSError:
                msg = "Browser content is unavailable."
                raise BrowserError(msg) from None
        msg = "Browser content is unavailable."
        raise BrowserError(msg)

    @contextmanager
    def _tab(self, deadline: ExtractionDeadline | None) -> Generator[BrowserTab]:
        with BrowserOperationTimeout(deadline or ExtractionDeadline.after(DEFAULT_BROWSER_TIMEOUT_SECONDS)):
            browser = self._browser.start()
            context = browser.create_browser_context()
        try:
            with BrowserOperationTimeout(deadline or ExtractionDeadline.after(DEFAULT_BROWSER_TIMEOUT_SECONDS)):
                tab = browser.new_tab(browser_context_id=context)
            yield BoundedBrowserTab(tab, deadline)
        finally:
            self._close_context(browser, context, deadline)

    def _close_context(self, browser: Chrome, context: str, deadline: ExtractionDeadline | None) -> None:
        if deadline is not None and monotonic() >= deadline.expires_at:
            self._browser.abort()
            self._browser.close()
            return
        try:
            with BrowserOperationTimeout(ExtractionDeadline.after(CONTEXT_CLEANUP_SECONDS)):
                browser.delete_browser_context(context)
        except PydollException, SyncError, BrowserError, OSError:
            self._browser.abort()
            self._browser.close()
            raise

    def _fetch(self, url: str, wait_selector: str | None, deadline: ExtractionDeadline | None) -> str:
        with self._tab(deadline) as tab:
            self._navigate(tab, url, wait_selector)
            content = tab.html
            if len(content.encode()) > MAX_BROWSER_HTML_BYTES:
                msg = "Browser HTML exceeds its byte limit."
                raise BrowserError(msg)
            return content

    def _navigate(self, tab: BrowserTab, url: str, wait_selector: str | None) -> None:
        if not tab.get(url, timeout=DEFAULT_BROWSER_TIMEOUT_SECONDS):
            msg = "Browser navigation failed."
            raise BrowserError(msg)
        if wait_selector and tab.ele(wait_selector, timeout=SELECTOR_TIMEOUT_SECONDS) is None:
            msg = "Browser required content is unavailable."
            raise BrowserError(msg)

    def extract_and_download(
        self,
        url: str,
        extractor_fn: Callable[[BrowserTab, str], RichMediaPayload | None],
        wait_selector: str | None = None,
        cookies: list[dict[str, object]] | None = None,
        *,
        deadline: ExtractionDeadline | None = None,
    ) -> tuple[RichMediaPayload | None, list[bytes | MediaFailure]]:
        """Extract in an isolated tab and download through a fresh bounded cookie-bearing HTTP session."""
        with self._session(deadline):
            try:
                return self._extract_attempt(url, extractor_fn, wait_selector, cookies, deadline)
            except PydollException, SyncError, TimeoutError, OSError:
                msg = "Browser extraction is unavailable."
                raise BrowserError(msg) from None

    def _extract_attempt(
        self,
        url: str,
        extractor_fn: Callable[[BrowserTab, str], RichMediaPayload | None],
        wait_selector: str | None,
        cookies: list[dict[str, object]] | None,
        deadline: ExtractionDeadline | None,
    ) -> tuple[RichMediaPayload | None, list[bytes | MediaFailure]]:
        with self._tab(deadline) as tab:
            for cookie in cookies or ():
                tab.set.cookies(cookie)
            self._navigate(tab, url, wait_selector)
            payload = extractor_fn(tab, url)
            if payload is None:
                return None, []
            return payload, download_tab_media(tab, payload, url, deadline=deadline, allowed_hosts=self._allowed_hosts)

    def close(self) -> None:
        """Deterministically close browser drivers, reap Chromium, and remove its temporary profile."""
        self._browser.close()
