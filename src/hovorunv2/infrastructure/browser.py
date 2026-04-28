"""Infrastructure service for managing a headless browser instance."""

import asyncio

from playwright.async_api import Browser, Playwright, async_playwright

from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class BrowserService:
    """Service for managing a headless browser instance with auto-shutdown."""

    def __init__(self, max_tabs: int, idle_timeout: int) -> None:
        self._max_tabs = max_tabs
        self._idle_timeout = idle_timeout
        self._semaphore = asyncio.Semaphore(max_tabs)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._idle_timer: asyncio.Task | None = None
        self._active_requests = 0
        self._lock = asyncio.Lock()

    async def get_content(self, url: str, wait_selector: str | None = None) -> str:
        """Fetch HTML content of the given URL."""
        async with self._semaphore:
            async with self._lock:
                self._active_requests += 1
                self._stop_idle_timer()
                await self._ensure_browser()

            try:
                if not self._browser:
                    msg = "Browser failed to start"
                    raise RuntimeError(msg)
                page = await self._browser.new_page()
                try:
                    logger.info("Fetching content from %s", url)
                    # Threads can be slow, wait for network idle
                    await page.goto(url, wait_until="networkidle", timeout=30000)

                    if wait_selector:
                        try:
                            logger.debug("Waiting for selector: %s", wait_selector)
                            await page.wait_for_selector(wait_selector, timeout=10000)
                        except Exception:
                            logger.warning(
                                "Timeout waiting for selector '%s' on %s, returning current content", wait_selector, url
                            )

                    return await page.content()
                finally:
                    await page.close()
            finally:
                async with self._lock:
                    self._active_requests -= 1
                    if self._active_requests == 0:
                        self._start_idle_timer()

    async def _ensure_browser(self) -> None:
        """Ensure browser instance is running."""
        if not self._browser:
            logger.info("Starting new headless browser instance")
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)

    def _stop_idle_timer(self) -> None:
        """Cancel the idle shutdown timer."""
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _start_idle_timer(self) -> None:
        """Start the idle shutdown timer."""
        self._stop_idle_timer()
        self._idle_timer = asyncio.create_task(self._idle_shutdown())

    async def _idle_shutdown(self) -> None:
        """Shutdown browser after idle timeout."""
        try:
            await asyncio.sleep(self._idle_timeout)
            logger.info("Idle timeout reached, shutting down browser")
            await self.close()
        except asyncio.CancelledError:
            pass

    async def close(self) -> None:
        """Close browser and playwright instance."""
        async with self._lock:
            self._stop_idle_timer()
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            logger.info("Browser service closed")
