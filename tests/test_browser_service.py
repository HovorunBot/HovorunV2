"""Tests for the BrowserService."""

from collections.abc import AsyncGenerator

import pytest

from hovorunv2.infrastructure.browser import BrowserService


@pytest.fixture
async def browser_service() -> AsyncGenerator[BrowserService]:
    """Fixture for BrowserService with small timeout for testing."""
    service = BrowserService(max_tabs=2, idle_timeout=5)
    service.DEFAULT_TIMEOUT_MS = 5000  # Faster timeout for tests
    yield service
    await service.close()


@pytest.mark.asyncio
async def test_browser_lifecycle(browser_service: BrowserService) -> None:
    """Test that the browser starts, fetches content, and closes correctly."""
    content = await browser_service.get_content("about:blank")
    assert "<html><head></head><body></body></html>" in content
    assert browser_service.is_running


@pytest.mark.asyncio
async def test_browser_reconnect_after_manual_close(browser_service: BrowserService) -> None:
    """Test that browser restarts if closed externally."""
    # First request to start browser
    await browser_service.get_content("about:blank")
    assert browser_service.is_running

    # Manually close browser without service knowing (reproducibility of external failure)
    # We still use private access here to SIMULATE an external failure
    assert browser_service._manager.browser is not None  # noqa: SLF001
    await browser_service._manager.browser.close()  # noqa: SLF001

    # Next request should restart and succeed
    content = await browser_service.get_content("about:blank")
    assert "<html><head></head><body></body></html>" in content
    assert browser_service.is_running
