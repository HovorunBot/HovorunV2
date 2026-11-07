"""Real-browser deadline bounds and child teardown for stalled navigation."""

import time

import pytest

from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import BrowserError
from hovorunv2.infrastructure.browser.native_browser import NativeBrowserService
from tests.utils.sync_http import HTTPFixture, HTTPReply

pytestmark = [pytest.mark.browser]


@pytest.mark.slow
def test_stalled_navigation_is_bounded_and_tears_down() -> None:
    """A stalled navigation cannot outlive its shared deadline or leak its child browser."""
    deadline = ExtractionDeadline.after(6.0)
    with HTTPFixture({"/stall": HTTPReply(b"<html><body>ok</body></html>", delay_seconds=15.0)}) as server:
        service = NativeBrowserService()
        try:
            started = time.monotonic()
            with pytest.raises(BrowserError):
                service.get_content(server.url("/stall"), deadline=deadline)
            elapsed = time.monotonic() - started
            assert not service.is_running
        finally:
            service.close()
    assert elapsed < 12.0
