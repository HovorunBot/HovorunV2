"""Own real browser resources for adapter behavior tests."""

from collections.abc import Generator
from contextlib import contextmanager

from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.infrastructure.browser.bounded_tab import BoundedBrowserTab
from hovorunv2.infrastructure.browser.browser_process import OwnedChromium


@contextmanager
def browser_tab(deadline: ExtractionDeadline) -> Generator[BoundedBrowserTab]:
    """Provide a real tab and verify exact process and profile cleanup on every exit."""
    browser = OwnedChromium()
    try:
        page = browser.start()
        process, profile = browser.process, browser.profile_path
        assert process is not None
        assert profile is not None
        yield BoundedBrowserTab(page.new_tab(), deadline)
    finally:
        process, profile = browser.process, browser.profile_path
        browser.close()
        if process is not None:
            assert process.poll() is not None
        if profile is not None:
            assert not profile.exists()
