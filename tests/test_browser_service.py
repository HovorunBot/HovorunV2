"""Tests for the BrowserService."""

import pytest

from hovorunv2.infrastructure.browser import BrowserService


@pytest.mark.asyncio
async def test_browser_lifecycle() -> None:
    """Test that the browser starts, fetches content, and closes correctly."""
    # Use small timeout for testing
    service = BrowserService(max_tabs=2, idle_timeout=1)
    try:
        content = await service.get_content("https://example.com")
        assert "Example Domain" in content
        assert service._browser is not None  # noqa: SLF001
    finally:
        await service.close()
        assert service._browser is None  # noqa: SLF001
