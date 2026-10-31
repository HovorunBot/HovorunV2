"""Child process holding an owned Chromium for parent-death termination tests."""

import signal
from pathlib import Path

from hovorunv2.infrastructure.browser_process import OwnedChromium


def hold_owned_chromium(pid_file: str) -> None:
    """Start an owned browser, publish its exact PID, and wait to be killed."""
    browser = OwnedChromium(terminate_with_parent=True)
    assert browser.start() is not None
    process = browser.process
    assert process is not None
    Path(pid_file).write_text(str(process.pid))
    signal.pause()
