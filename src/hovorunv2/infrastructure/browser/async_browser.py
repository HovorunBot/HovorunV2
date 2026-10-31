"""Manage a headless DrissionPage browser with lifecycle and tab isolation."""

import asyncio
from collections.abc import Callable
from functools import partial

from DrissionPage import ChromiumPage
from DrissionPage.errors import BaseError, BrowserConnectError, PageDisconnectedError

from hovorunv2.application.media.browser import BrowserTab
from hovorunv2.application.media.errors import BrowserError, MediaFailure
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.infrastructure.browser.browser_process import OwnedChromium
from hovorunv2.infrastructure.browser.native_browser import download_tab_media
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


async def _run_owned_operation[T](operation: Callable[[], T]) -> T:
    """Retain the active request and semaphore until its blocking browser work is finished."""
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await _finish_cancelled_operation(task)
        raise


async def _finish_cancelled_operation[T](task: asyncio.Task[T]) -> None:
    """Drain cancelled work without leaking backend exceptions or yielding its resources early."""
    completion = asyncio.gather(task, return_exceptions=True)
    while not completion.done():
        try:
            await asyncio.shield(completion)
        except asyncio.CancelledError:
            continue


class BrowserLifecycleManager:
    """Manage the browser instance lifecycle and idle shutdown on a shared lock."""

    def __init__(self, idle_timeout: int, flags: list[str] | None = None) -> None:
        """Create the owned browser and request tracking.

        Args:
            idle_timeout: Seconds to wait before shutting down an idle browser.
            flags: Optional browser launch arguments, such as ``--headless``.
        """
        self._idle_timeout = idle_timeout
        self._flags = flags or []
        self._browser = OwnedChromium(flags)
        self._page: ChromiumPage | None = None
        self._idle_timer: asyncio.Task[None] | None = None
        self._active_requests = 0
        self._lock = asyncio.Lock()

    @property
    def page(self) -> ChromiumPage | None:
        """Return the current page instance or None if the browser is not started."""
        return self._page

    def require_page(self) -> ChromiumPage:
        """Require the page established by this manager's active request boundary."""
        page = self._page
        if page is None:
            msg = "Browser instance is not initialized"
            raise BrowserError(msg)
        return page

    @property
    def is_running(self) -> bool:
        """Return whether the browser process and attached page are running."""
        return self._browser.is_running

    async def acquire(self) -> None:
        """Start the browser and register an active request."""
        async with self._lock:
            self._stop_idle_timer()
            await self._ensure_browser()
            self._active_requests += 1

    async def release(self) -> None:
        """Deregister a request and start the idle timer when none remain."""
        async with self._lock:
            self._active_requests -= 1
            if self._active_requests == 0:
                self._start_idle_timer()

    async def _ensure_browser(self) -> None:
        """Start the browser if no page is attached yet."""
        if self._page is not None:
            return
        logger.info("Starting new headless DrissionPage instance")
        try:
            await _run_owned_operation(self._init_page)
        except asyncio.CancelledError:
            try:
                await _run_owned_operation(self._browser.close)
            finally:
                self._page = None
            raise

    def _init_page(self) -> None:
        """Start the owned Chromium instance and attach its page."""
        self._page = self._browser.start()

    def _start_idle_timer(self) -> None:
        """Schedule the idle shutdown task."""
        self._stop_idle_timer()
        self._idle_timer = asyncio.create_task(self._idle_shutdown())

    def _stop_idle_timer(self) -> None:
        """Cancel the pending idle shutdown task."""
        if self._idle_timer:
            if self._idle_timer is not asyncio.current_task():
                self._idle_timer.cancel()
            self._idle_timer = None

    async def _idle_shutdown(self) -> None:
        """Close the browser after the idle timeout elapses."""
        try:
            await asyncio.sleep(self._idle_timeout)
            async with self._lock:
                if self._active_requests == 0:
                    logger.info("Idle timeout reached, shutting down browser")
                    await self._perform_close()
        except asyncio.CancelledError:
            return

    async def _perform_close(self) -> None:
        """Close the browser and detach its page without re-acquiring the lock."""
        self._stop_idle_timer()
        try:
            await _run_owned_operation(self._browser.close)
        finally:
            self._page = None
        logger.info("Browser lifecycle manager closed")

    async def close(self) -> None:
        """Close the browser and detach its page."""
        async with self._lock:
            await self._perform_close()


class BrowserService:
    """Fetch web content and media through a headless browser with retry and tab isolation."""

    DEFAULT_TIMEOUT_S = 30
    SELECTOR_TIMEOUT_S = 10
    MAX_RETRIES = 2

    def __init__(self, max_tabs: int, idle_timeout: int, flags: list[str] | None = None) -> None:
        """Create the browser manager and concurrency bound.

        Args:
            max_tabs: Maximum concurrent tabs, guarded by a semaphore.
            idle_timeout: Seconds to wait before shutting down an idle browser.
            flags: Optional browser launch arguments.

        Raises:
            ValueError: If max_tabs is not positive.
        """
        if max_tabs <= 0:
            msg = "Browser tab capacity must be positive."
            raise ValueError(msg)
        self._manager = BrowserLifecycleManager(idle_timeout, flags)
        self._semaphore = asyncio.Semaphore(max_tabs)

    @property
    def is_running(self) -> bool:
        """Return whether the managed browser instance is running."""
        return self._manager.is_running

    async def get_content(self, url: str, wait_selector: str | None = None) -> str:
        """Fetch HTML content of the URL, retrying recoverable browser failures."""
        async with self._semaphore:
            return await self._content_with_retries(url, wait_selector)

    async def _content_with_retries(self, url: str, wait_selector: str | None) -> str:
        for attempt in range(self.MAX_RETRIES):
            await self._manager.acquire()
            try:
                return await self._fetch_url_content(url, wait_selector)
            except (BaseError, BrowserError, OSError) as e:
                if self._is_recoverable_error(e) and attempt == 0:
                    logger.warning("Browser error, retrying (%s)", type(e))
                    await self._manager.close()
                    continue
                msg = "Browser content is unavailable."
                raise BrowserError(msg) from None
            finally:
                await self._manager.release()

        msg = "Failed to fetch browser content after retries."
        raise BrowserError(msg)

    async def extract_and_download[T](
        self,
        url: str,
        extractor_fn: Callable[[BrowserTab, str], T | None],
        wait_selector: str | None = None,
        cookies: list[dict[str, object]] | None = None,
    ) -> tuple[T | None, list[bytes | MediaFailure]]:
        """Fetch the URL, extract metadata, and download media in one browser session.

        Args:
            url: Target URL.
            extractor_fn: Callable that extracts metadata from a tab and URL, or returns None.
            wait_selector: Optional CSS selector to wait for before extraction.
            cookies: Optional list of cookie dictionaries to set before navigation.

        Returns:
            Extracted metadata and downloaded media bytes or per-item failures.

        Raises:
            BrowserError: If the browser session cannot be acquired or the extraction fails.
        """
        async with self._semaphore:
            await self._manager.acquire()
            try:
                page = self._manager.require_page()
                operation = partial(self._sync_extract_and_download, page, url, extractor_fn, wait_selector, cookies)
                return await _run_owned_operation(operation)
            except BaseError, OSError:
                msg = "Browser extraction is unavailable."
                raise BrowserError(msg) from None
            finally:
                await self._manager.release()

    def _sync_extract_and_download[T](
        self,
        page: ChromiumPage,
        url: str,
        extractor_fn: Callable[[BrowserTab, str], T | None],
        wait_selector: str | None,
        cookies: list[dict[str, object]] | None,
    ) -> tuple[T | None, list[bytes | MediaFailure]]:
        """Perform extraction and download synchronously in a tab."""
        logger.info("Extracting and downloading via browser")
        # Create tab but don't load URL yet if we need to set cookies
        tab: BrowserTab = page.new_tab()
        try:
            if cookies:
                logger.info("Injecting %d cookies into browser session", len(cookies))
                for cookie in cookies:
                    tab.set.cookies(cookie)

            self._navigate(tab, url, wait_selector)

            # Try to extract using the provided function.
            # We pass the tab instead of just HTML so the extractor can run JS if needed.
            metadata = extractor_fn(tab, url)
            if not metadata:
                return None, []

            if not isinstance(metadata, RichMediaPayload):
                return metadata, []

            # Use the same tab/session cookies to fetch the resource through bounded streaming HTTP.
            return metadata, download_tab_media(tab, metadata, url)
        finally:
            tab.close()

    def _is_recoverable_error(self, error: Exception) -> bool:
        """Return whether the error is recoverable by restarting the browser."""
        if isinstance(error, (PageDisconnectedError, BrowserConnectError, ConnectionError)):
            return True
        err_msg = str(error).lower()
        return "closed" in err_msg or "connection" in err_msg or "disconnected" in err_msg

    async def _fetch_url_content(self, url: str, wait_selector: str | None = None) -> str:
        """Fetch the URL content in a new tab using the managed page."""
        page = self._manager.require_page()
        return await _run_owned_operation(partial(self._sync_fetch, page, url, wait_selector))

    def _sync_fetch(self, page: ChromiumPage, url: str, wait_selector: str | None) -> str:
        """Fetch the URL content synchronously in a new tab and return its HTML."""
        logger.info("Fetching browser content")
        # new_tab creates an empty tab; the bounded get(url) below performs its only navigation.
        tab: BrowserTab = page.new_tab()
        try:
            self._navigate(tab, url, wait_selector)

            return tab.html
        finally:
            # Closing the tab ensures we don't leak resources
            tab.close()

    def _navigate(self, tab: BrowserTab, url: str, wait_selector: str | None) -> None:
        if not tab.get(url, timeout=self.DEFAULT_TIMEOUT_S):
            msg = "Browser navigation failed."
            raise BrowserError(msg)
        if wait_selector:
            self._wait_for_selector_sync(tab, url, wait_selector)

    def _wait_for_selector_sync(self, tab: BrowserTab, url: str, selector: str) -> None:
        """Wait for the selector, degrading to the current content on timeout."""
        try:
            logger.debug("Waiting for selector: %s", selector)
            tab.ele(selector, timeout=self.SELECTOR_TIMEOUT_S)
        except BaseError, OSError:
            logger.warning(
                "Timeout waiting for selector, returning current content",
            )

    async def close(self) -> None:
        """Close the managed browser and page."""
        await self._manager.close()
