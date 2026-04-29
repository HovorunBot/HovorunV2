"""Infrastructure service for managing a headless browser instance."""

import asyncio
import contextlib

from playwright.async_api import Browser, Error, Page, Playwright, async_playwright

from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class BrowserError(Exception):
    """Base exception for browser-related errors."""


class BrowserLifecycleManager:
    """Manager for browser instance lifecycle and idle shutdown."""

    def __init__(self, idle_timeout: int) -> None:
        self._idle_timeout = idle_timeout
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._idle_timer: asyncio.Task | None = None
        self._active_requests = 0
        self._lock = asyncio.Lock()

    @property
    def browser(self) -> Browser | None:
        """Get current browser instance."""
        return self._browser

    @property
    def is_running(self) -> bool:
        """Check if browser instance is currently running and connected."""
        return self._browser is not None and self._browser.is_connected()

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
        if not self._playwright:
            logger.info("Starting playwright instance")
            self._playwright = await async_playwright().start()

        if not self._browser or not self._browser.is_connected():
            if self._browser:
                logger.info("Browser disconnected, closing old instance")
                with contextlib.suppress(Exception):
                    await self._browser.close()

            logger.info("Starting new headless browser instance")
            self._browser = await self._playwright.chromium.launch(headless=True)

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
        # Ensure timer is stopped
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

        if self._browser:
            try:
                # Use wait_for to prevent infinite hang
                await asyncio.wait_for(self._browser.close(), timeout=5.0)
            except Exception:
                logger.exception("Error closing browser")
            self._browser = None

        if self._playwright:
            try:
                await asyncio.wait_for(self._playwright.stop(), timeout=5.0)
            except Exception:
                logger.exception("Error stopping playwright")
            self._playwright = None
        logger.info("Browser lifecycle manager closed")

    async def close(self) -> None:
        """Close browser and playwright instance."""
        async with self._lock:
            await self._perform_close()


class BrowserService:
    """Service for fetching web content using a headless browser."""

    DEFAULT_TIMEOUT_MS = 30000
    SELECTOR_TIMEOUT_MS = 10000
    MAX_RETRIES = 2

    def __init__(self, max_tabs: int, idle_timeout: int) -> None:
        self._manager = BrowserLifecycleManager(idle_timeout)
        self._semaphore = asyncio.Semaphore(max_tabs)

    @property
    def is_running(self) -> bool:
        """Check if browser instance is currently running and connected."""
        return self._manager.is_running

    async def get_content(self, url: str, wait_selector: str | None = None) -> str:
        """Fetch HTML content of given URL with retries."""
        async with self._semaphore:
            for attempt in range(self.MAX_RETRIES):
                await self._manager.acquire()
                try:
                    return await self._fetch_url_content(url, wait_selector)
                except Error as e:
                    if self._is_recoverable_error(e) and attempt == 0:
                        logger.warning("Browser error, retrying: %s", e)
                        await self._manager.close()
                        continue
                    raise
                finally:
                    await self._manager.release()

        msg = f"Failed to fetch content from {url} after retries"
        raise BrowserError(msg)

    def _is_recoverable_error(self, error: Error) -> bool:
        """Check if browser error is recoverable by restarting."""
        err_msg = str(error).lower()
        return "closed" in err_msg or "connection" in err_msg

    async def _fetch_url_content(self, url: str, wait_selector: str | None = None) -> str:
        """Fetch content using a new page."""
        browser = self._manager.browser
        if not browser:
            msg = "Browser instance is not initialized"
            raise BrowserError(msg)

        page = await browser.new_page()
        try:
            logger.info("Fetching content from %s", url)
            await page.goto(url, wait_until="domcontentloaded", timeout=self.DEFAULT_TIMEOUT_MS)

            if wait_selector:
                await self._wait_for_selector_safely(page, url, wait_selector)

            return await page.content()
        finally:
            await page.close()

    async def _wait_for_selector_safely(self, page: Page, url: str, selector: str) -> None:
        """Wait for selector without failing whole request on timeout."""
        try:
            logger.debug("Waiting for selector: %s", selector)
            await page.wait_for_selector(selector, timeout=self.SELECTOR_TIMEOUT_MS)
        except Error:
            logger.warning(
                "Timeout waiting for selector '%s' on %s, returning current content",
                selector,
                url,
            )

    async def close(self) -> None:
        """Close browser manager."""
        await self._manager.close()
