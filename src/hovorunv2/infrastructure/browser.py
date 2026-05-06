"""Infrastructure service for managing a headless browser instance using DrissionPage."""

import asyncio
import html
import os
import platform
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from DrissionPage import ChromiumOptions, ChromiumPage
from DrissionPage.errors import BaseError, BrowserConnectError, PageDisconnectedError

from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class BrowserError(Exception):
    """Base exception for browser-related errors."""


class BrowserLifecycleManager:
    """Manager for browser instance lifecycle and idle shutdown.

    Attributes:
        _idle_timeout (int): Seconds to wait before shutting down idle browser.
        _page (ChromiumPage | None): The managed DrissionPage instance.
        _idle_timer (asyncio.Task | None): Task for scheduled shutdown.
        _active_requests (int): Counter for active browser operations.
        _lock (asyncio.Lock): Synchronization for lifecycle state changes.

    """

    def __init__(self, idle_timeout: int) -> None:
        """Initialize the manager.

        Args:
            idle_timeout: Seconds to wait before shutting down idle browser.

        """
        self._idle_timeout = idle_timeout
        self._page: ChromiumPage | None = None
        self._idle_timer: asyncio.Task | None = None
        self._active_requests = 0
        self._lock = asyncio.Lock()

    @property
    def page(self) -> ChromiumPage | None:
        """Get current page instance.

        Returns:
            ChromiumPage instance or None if not started.

        """
        return self._page

    @property
    def is_running(self) -> bool:
        """Check if browser instance is currently running.

        Returns:
            True if _page exists.

        """
        return self._page is not None

    async def acquire(self) -> None:
        """Initialize browser and tracking for a new request."""
        async with self._lock:
            self._active_requests += 1
            self._stop_idle_timer()
            await self._ensure_browser()

    async def release(self) -> None:
        """Update tracking and start idle timer if no active requests."""
        async with self._lock:
            self._active_requests -= 1
            if self._active_requests == 0:
                self._start_idle_timer()

    async def _ensure_browser(self) -> None:
        """Ensure browser instance is running."""
        if not self._page:
            logger.info("Starting new headless DrissionPage instance")
            await asyncio.to_thread(self._init_page)

    def _init_page(self) -> None:
        """Initialize ChromiumPage in a thread with detected browser path."""
        options = ChromiumOptions()
        options.set_argument("--headless")
        options.set_argument("--no-sandbox")
        options.set_argument("--disable-gpu")

        if browser_path := self._find_browser_path():
            logger.info("Using browser at: %s", browser_path)
            options.set_browser_path(browser_path)

        self._page = ChromiumPage(options)

    def _find_browser_path(self) -> str | None:
        """Find a suitable Chromium-based browser executable on the system.

        Returns:
            Path to executable or None if not found.

        """
        # Check manual override
        if env_path := os.environ.get("BROWSER_PATH"):
            if Path(env_path).exists():
                return env_path
            logger.warning("BROWSER_PATH set but not found: %s", env_path)

        system = platform.system()

        candidates = [
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
            "brave-browser",
            "microsoft-edge",
            "microsoft-edge-stable",
            "vivaldi",
            "chrome",
        ]
        for candidate in candidates:
            if path := shutil.which(candidate):
                return path

        if system == "Darwin":
            mac_paths = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
            ]
            for path in mac_paths:
                if Path(path).exists():
                    return path

        if system == "Windows":
            program_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
            program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
            local_app_data = os.environ.get("LOCALAPPDATA", str(Path("~\\AppData\\Local").expanduser()))

            win_paths = [
                rf"{program_files}\Google\Chrome\Application\chrome.exe",
                rf"{program_files_x86}\Google\Chrome\Application\chrome.exe",
                rf"{local_app_data}\Google\Chrome\Application\chrome.exe",
                rf"{program_files}\BraveSoftware\Brave-Browser\Application\brave.exe",
                rf"{program_files}\Microsoft\Edge\Application\msedge.exe",
                rf"{local_app_data}\Vivaldi\Application\vivaldi.exe",
            ]
            for path in win_paths:
                if Path(path).exists():
                    return path

        return None

    def _start_idle_timer(self) -> None:
        """Start idle shutdown timer."""
        self._stop_idle_timer()
        self._idle_timer = asyncio.create_task(self._idle_shutdown())

    def _stop_idle_timer(self) -> None:
        """Cancel idle shutdown timer."""
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

    async def _idle_shutdown(self) -> None:
        """Shutdown browser after idle timeout."""
        try:
            await asyncio.sleep(self._idle_timeout)
            async with self._lock:
                if self._active_requests == 0:
                    logger.info("Idle timeout reached, shutting down browser")
                    await self._perform_close()
        except asyncio.CancelledError:
            return

    async def _perform_close(self) -> None:
        """Close without lock."""
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

        if self._page:
            try:
                await asyncio.to_thread(self._page.quit)
            except Exception:
                logger.exception("Error closing DrissionPage")
            self._page = None
        logger.info("Browser lifecycle manager closed")

    async def close(self) -> None:
        """Close browser and page instance."""
        async with self._lock:
            await self._perform_close()


class BrowserService:
    """Service for fetching web content using a headless browser.

    Provides a thread-safe interface for web scraping with automatic
    lifecycle management and tab-based isolation.
    """

    DEFAULT_TIMEOUT_S = 30
    SELECTOR_TIMEOUT_S = 10
    MAX_RETRIES = 2

    def __init__(self, max_tabs: int, idle_timeout: int) -> None:
        """Initialize the service.

        Args:
            max_tabs: Maximum concurrent tabs (controlled by semaphore).
            idle_timeout: Seconds to wait before shutting down idle browser.

        """
        self._manager = BrowserLifecycleManager(idle_timeout)
        self._semaphore = asyncio.Semaphore(max_tabs)

    @property
    def is_running(self) -> bool:
        """Check if browser instance is currently running."""
        return self._manager.is_running

    async def get_content(self, url: str, wait_selector: str | None = None) -> str:
        """Fetch HTML content of given URL with retries."""
        async with self._semaphore:
            for attempt in range(self.MAX_RETRIES):
                await self._manager.acquire()
                try:
                    return await self._fetch_url_content(url, wait_selector)
                except (BaseError, Exception) as e:
                    if self._is_recoverable_error(e) and attempt == 0:
                        logger.warning("Browser error, retrying: %s", e)
                        await self._manager.close()
                        continue
                    raise
                finally:
                    await self._manager.release()

        msg = f"Failed to fetch content from {url} after retries"
        raise BrowserError(msg)

    async def extract_and_download(
        self,
        url: str,
        extractor_fn: Callable,
        wait_selector: str | None = None,
        cookies: list[dict[str, Any]] | None = None,
    ) -> tuple[Any, list[bytes]]:
        """Fetch URL, extract metadata, and download media in one browser session.

        Args:
            url: Target URL.
            extractor_fn: Function that takes HTML string and returns metadata.
            wait_selector: Optional CSS selector to wait for.
            cookies: Optional list of cookie dictionaries to set before navigation.

        Returns:
            Tuple of (extracted_metadata, list of downloaded media bytes).

        """
        async with self._semaphore:
            await self._manager.acquire()
            try:
                page = self._manager.page
                if not page:
                    msg = "Browser instance is not initialized"
                    raise BrowserError(msg)

                return await asyncio.to_thread(
                    self._sync_extract_and_download, page, url, extractor_fn, wait_selector, cookies
                )
            finally:
                await self._manager.release()

    def _sync_extract_and_download(
        self,
        page: ChromiumPage,
        url: str,
        extractor_fn: Callable,
        wait_selector: str | None,
        cookies: list[dict[str, Any]] | None,
    ) -> tuple[Any, list[bytes]]:
        """Perform extraction and download synchronously in a tab."""
        logger.info("Extracting and downloading via browser: %s", url)
        # Create tab but don't load URL yet if we need to set cookies
        tab = page.new_tab()
        try:
            if cookies:
                logger.info("Injecting %d cookies into browser session", len(cookies))
                for cookie in cookies:
                    tab.set.cookies(cookie)

            tab.get(url, timeout=self.DEFAULT_TIMEOUT_S)
            if wait_selector:
                self._wait_for_selector_sync(tab, url, wait_selector)

            # Try to extract using the provided function.
            # We pass the tab instead of just HTML so the extractor can run JS if needed.
            metadata = extractor_fn(tab, url)

            downloaded_bytes = []
            if metadata and hasattr(metadata, "media_items"):
                for item in metadata.media_items:
                    # Use the same tab/session to fetch the resource
                    clean_media_url = html.unescape(item.url)
                    logger.info("Downloading media via browser session: %s", clean_media_url)
                    resp = tab.get(clean_media_url)
                    if resp and hasattr(resp, "content"):
                        downloaded_bytes.append(resp.content)
                    elif isinstance(resp, bytes):
                        downloaded_bytes.append(resp)

            return metadata, downloaded_bytes
        finally:
            tab.close()

    def _is_recoverable_error(self, error: Exception) -> bool:
        """Check if browser error is recoverable by restarting.

        Args:
            error: The exception to check.

        Returns:
            True if error suggests a crash/disconnect that a restart might fix.

        """
        if isinstance(error, (PageDisconnectedError, BrowserConnectError, ConnectionError)):
            return True
        err_msg = str(error).lower()
        return "closed" in err_msg or "connection" in err_msg or "disconnected" in err_msg

    async def _fetch_url_content(self, url: str, wait_selector: str | None = None) -> str:
        """Fetch content using a new tab in the managed page.

        Args:
            url: URL to fetch.
            wait_selector: CSS selector to wait for.

        Returns:
            HTML content.

        """
        page = self._manager.page
        if not page:
            msg = "Browser instance is not initialized"
            raise BrowserError(msg)

        return await asyncio.to_thread(self._sync_fetch, page, url, wait_selector)

    def _sync_fetch(self, page: ChromiumPage, url: str, wait_selector: str | None) -> str:
        """Fetch content synchronously in a thread.

        Uses tabs for lightweight control isolation. Note that in DrissionPage,
        tabs within a single ChromiumPage instance share the same session,
        cookies, and local storage.

        Args:
            page: Main ChromiumPage instance.
            url: URL to fetch.
            wait_selector: CSS selector to wait for.

        Returns:
            HTML content.

        """
        logger.info("Fetching content from %s", url)
        # new_tab creates a new tab object; get(url) loads the content
        tab = page.new_tab(url)
        try:
            tab.get(url, timeout=self.DEFAULT_TIMEOUT_S)

            if wait_selector:
                self._wait_for_selector_sync(tab, url, wait_selector)

            return tab.html
        finally:
            # Closing the tab ensures we don't leak resources
            tab.close()

    def _wait_for_selector_sync(self, tab: Any, url: str, selector: str) -> None:  # noqa: ANN401
        """Wait for selector synchronously.

        Args:
            tab: DrissionPage tab instance.
            url: URL being fetched (for logging).
            selector: CSS selector to wait for.

        """
        try:
            logger.debug("Waiting for selector: %s", selector)
            tab.ele(selector, timeout=self.SELECTOR_TIMEOUT_S)
        except Exception:
            logger.warning(
                "Timeout waiting for selector '%s' on %s, returning current content",
                selector,
                url,
            )

    async def close(self) -> None:
        """Close browser manager."""
        await self._manager.close()
