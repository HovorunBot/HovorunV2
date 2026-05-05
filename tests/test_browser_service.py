"""Tests for the BrowserService using DrissionPage."""

import asyncio
from collections.abc import AsyncGenerator

import pytest

from hovorunv2.infrastructure.browser import BrowserService


@pytest.fixture
async def browser_service() -> AsyncGenerator[BrowserService]:
    """Fixture for BrowserService with small timeout for testing."""
    service = BrowserService(max_tabs=2, idle_timeout=5)
    # DrissionPage uses seconds, not milliseconds in my new implementation
    service.DEFAULT_TIMEOUT_S = 5
    yield service
    await service.close()


@pytest.mark.asyncio
async def test_browser_lifecycle(browser_service: BrowserService) -> None:
    """Test that the browser starts, fetches content, and closes correctly."""
    # about:blank might be too simple, but let's try
    content = await browser_service.get_content("about:blank")
    assert "<html><head></head><body></body></html>" in content or "about:blank" in content
    assert browser_service.is_running


@pytest.mark.asyncio
async def test_browser_reconnect_after_manual_close(browser_service: BrowserService) -> None:
    """Test that browser restarts if closed externally."""
    # First request to start browser
    await browser_service.get_content("about:blank")
    assert browser_service.is_running

    # Manually close browser without service knowing
    # In DrissionPage, we can call quit()
    assert browser_service._manager.page is not None  # noqa: SLF001
    await asyncio.to_thread(browser_service._manager.page.quit)  # noqa: SLF001

    # We need to manually set it to None to simulate failure or handle it in _is_recoverable_error
    # Actually, BrowserService checks if it's recoverable.
    # If the page is quit, the next tab.get() or page.new_tab() will fail.

    # Next request should restart and succeed
    content = await browser_service.get_content("about:blank")
    assert "about:blank" in content or "<html>" in content
    assert browser_service.is_running
