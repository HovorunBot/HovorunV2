"""Explicit Chromium subprocess ownership using DrissionPage's public attachment API."""

import math
import subprocess
import time
from os import getpid
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock

from DrissionPage import ChromiumOptions, ChromiumPage
from DrissionPage.errors import BaseError

from hovorunv2.application.media.errors import BrowserError
from hovorunv2.infrastructure.browser_discovery import find_browser_executable
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)

DEFAULT_STARTUP_SECONDS = 15
PROCESS_STOP_SECONDS = 3
STARTUP_POLL_SECONDS = 0.05
MAX_TCP_PORT = 65535
OWNED_ARGUMENTS = (
    "--remote-debugging-port",
    "--remote-debugging-address",
    "--remote-debugging-pipe",
    "--user-data-dir",
)
DEFAULT_BROWSER_FLAGS = (
    "--headless",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-software-rasterizer",
    "--no-first-run",
    "--no-default-browser-check",
)


class OwnedChromium:
    """Launch lazily in the using process, retain its handle, and attach without a library-owned launch."""

    def __init__(self, flags: list[str] | None = None, startup_timeout: float = DEFAULT_STARTUP_SECONDS) -> None:
        if not math.isfinite(startup_timeout) or startup_timeout <= 0:
            msg = "Browser startup requires a finite positive timeout."
            raise ValueError(msg)
        self._flags = tuple(flags or ())
        if any(flag.startswith(OWNED_ARGUMENTS) for flag in self._flags):
            msg = "Browser ownership arguments cannot be overridden."
            raise ValueError(msg)
        self._startup_timeout = startup_timeout
        self._owner: int | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._profile: TemporaryDirectory[str] | None = None
        self._page: ChromiumPage | None = None
        self._lock = RLock()

    def _require_owner(self) -> None:
        if self._owner is not None and self._owner != getpid():
            msg = "Browser resources require their owning process."
            raise ValueError(msg)

    @property
    def page(self) -> ChromiumPage | None:
        """Expose the child-local attached page without creating resources."""
        self._require_owner()
        return self._page

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        """Retain the exact launched process for bounded shutdown and lifecycle inspection."""
        self._require_owner()
        return self._process

    @property
    def profile_path(self) -> Path | None:
        """Expose only the owned temporary profile location for lifecycle inspection."""
        self._require_owner()
        return Path(self._profile.name) if self._profile is not None else None

    @property
    def is_running(self) -> bool:
        """Report whether the owned subprocess and attached page remain live."""
        self._require_owner()
        return self._page is not None and self._process is not None and self._process.poll() is None

    def start(self) -> ChromiumPage:
        """Own Chromium before attaching, and clean every resource if startup fails."""
        with self._lock:
            self._require_owner()
            if self.is_running and self._page is not None:
                return self._page
            self.close()
            self._owner = getpid()
            try:
                self._launch()
                address = self._wait_for_address()
                options = ChromiumOptions(read_file=False)
                options.set_address(address)
                options.existing_only(on_off=True)
                options.headless(on_off=True)
                options.set_retry(times=0, interval=0)
                options.set_timeouts(base=DEFAULT_STARTUP_SECONDS, page_load=DEFAULT_STARTUP_SECONDS)
                self._page = ChromiumPage(options)
            except BaseError, OSError:
                self.close()
                msg = "Browser startup is unavailable."
                raise BrowserError(msg) from None
            except BaseException:
                self.close()
                raise
            else:
                return self._page

    def _launch(self) -> None:
        self._owner = getpid()
        executable = find_browser_executable()
        if executable is None:
            msg = "Browser executable is unavailable."
            raise BrowserError(msg)
        self._profile = TemporaryDirectory(prefix="hovorun-browser-", ignore_cleanup_errors=True)
        arguments = [
            executable,
            *DEFAULT_BROWSER_FLAGS,
            *self._flags,
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            f"--user-data-dir={self._profile.name}",
            "about:blank",
        ]
        self._process = subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603

    def _wait_for_address(self) -> str:
        assert self._profile is not None
        assert self._process is not None
        active_port = Path(self._profile.name) / "DevToolsActivePort"
        deadline = time.monotonic() + self._startup_timeout
        while time.monotonic() < deadline:
            if active_port.is_file():
                try:
                    port = int(active_port.read_text(encoding="utf-8").splitlines()[0])
                except OSError, ValueError, IndexError:
                    pass
                else:
                    if 0 < port <= MAX_TCP_PORT:
                        return f"127.0.0.1:{port}"
            if self._process.poll() is not None:
                msg = "Browser process exited before attachment."
                raise BrowserError(msg)
            time.sleep(STARTUP_POLL_SECONDS)
        msg = "Browser startup timed out."
        raise BrowserError(msg)

    def close(self) -> None:
        """Close attached drivers, terminate if necessary, reap the process, and remove its profile."""
        with self._lock:
            self._require_owner()
            try:
                if self._page is not None:
                    try:
                        self._page.quit(timeout=PROCESS_STOP_SECONDS, force=False)
                    except Exception:
                        # The owned subprocess is still reaped when the browser connection already died.
                        logger.debug("Browser connection was already closed during shutdown")
                    finally:
                        self._page = None
                self._stop_process()
            finally:
                if self._profile is not None:
                    self._profile.cleanup()
                    self._profile = None

    def _stop_process(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            try:
                process.wait(timeout=PROCESS_STOP_SECONDS)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=PROCESS_STOP_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=PROCESS_STOP_SECONDS)
        finally:
            self._process = None
