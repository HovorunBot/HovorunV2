"""Verify browser SDK initialization and process ownership in fresh and forked processes."""

import os
import sys
import threading
from typing import get_type_hints

from hovorunv2.infrastructure.browser.bounded_tab import BoundedBrowserTab
from hovorunv2.infrastructure.browser.browser_process import OwnedChromium
from hovorunv2.infrastructure.browser.native_browser import NativeBrowserService


def _verify_browser() -> None:
    browser = OwnedChromium()
    try:
        assert browser.start() is not None
        process, profile = browser.process, browser.profile_path
        assert process is not None
        assert profile is not None
        loops = [thread for thread in threading.enumerate() if thread.name == "pydoll-sync-loop"]
        assert len(loops) == 1
        assert loops[0].daemon
    finally:
        browser.close()
    assert process.poll() is not None
    assert not profile.exists()


def main() -> None:
    """Prove lazy imports, runtime annotations, and first-use reset after a real fork."""
    service = NativeBrowserService()
    assert not service.is_running
    assert not any(name == "pydoll" or name.startswith("pydoll.") for name in sys.modules)
    assert not any(thread.name == "pydoll-sync-loop" for thread in threading.enumerate())
    assert get_type_hints(OwnedChromium.start)["return"].__name__ == "Chrome"
    assert get_type_hints(BoundedBrowserTab.__init__)["tab"].__name__ == "Tab"
    assert not any(thread.name == "pydoll-sync-loop" for thread in threading.enumerate())
    _verify_browser()
    child = os.fork()
    if child == 0:
        try:
            assert not any(thread.name == "pydoll-sync-loop" for thread in threading.enumerate())
            _verify_browser()
        except BaseException:
            os._exit(1)
        os._exit(0)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    _verify_browser()


if __name__ == "__main__":
    main()
