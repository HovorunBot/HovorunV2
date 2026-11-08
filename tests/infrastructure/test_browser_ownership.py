"""Real Chromium ownership, cancellation and ordered browser download contracts."""

import multiprocessing
import os
import signal
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path

import pytest

from hovorunv2.application.media.browser import BrowserTab
from hovorunv2.application.media.errors import BrowserError, MediaFailure
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.infrastructure.browser.browser_discovery import find_browser_executable
from hovorunv2.infrastructure.browser.browser_process import OwnedChromium
from hovorunv2.infrastructure.browser.native_browser import NativeBrowserService, cookies_for_url
from tests.utils.browser_termination import hold_owned_chromium
from tests.utils.sync_http import HTTPFixture, HTTPReply

pytestmark = [pytest.mark.browser]


def _extract_images(tab: BrowserTab, url: str) -> RichMediaPayload:
    urls = tab.run_js("return Array.from(document.images, image => image.src)")
    assert isinstance(urls, list)
    assert all(isinstance(value, str) for value in urls)
    return RichMediaPayload(
        author_name="Synthetic",
        author_handle="",
        author_url="",
        content="",
        original_url=url,
        media_items=[MediaItem(value, is_video=False) for value in urls],
    )


def _require_partial_download(payload: RichMediaPayload | None, results: list[bytes | MediaFailure]) -> None:
    assert payload is not None
    assert results == [b"synthetic image", MediaFailure.UNAVAILABLE]
    assert [item.data for item in payload.media_items] == [b"synthetic image", None]


@pytest.mark.slow
def test_owned_chromium_reaps_process_and_removes_profile() -> None:
    """Closing a real browser retains exact process ownership until it is reaped."""
    browser = OwnedChromium()
    try:
        page = browser.start()
        process, profile = browser.process, browser.profile_path
        assert process is not None
        assert profile is not None
        assert profile.is_dir()
        assert process.poll() is None
        assert browser.start() is page
    finally:
        browser.close()
    assert process.poll() is not None
    assert not profile.exists()
    assert browser.page is None
    assert browser.process is None
    assert not browser.is_running
    browser.close()


@pytest.mark.slow
def test_startup_timeout_reaps_process_and_removes_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real launched process cannot outlive its failed attachment or retain a temporary profile."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    browser = OwnedChromium(startup_timeout=1e-9)
    try:
        with pytest.raises(BrowserError, match="Browser startup timed out"):
            browser.start()
        assert browser.process is None
        assert browser.profile_path is None
        assert not list(tmp_path.glob("hovorun-browser-*"))
    finally:
        browser.close()


@contextmanager
def _partial_download_url() -> Iterator[str]:
    routes = {"/image": HTTPReply(b"synthetic image"), "/missing": HTTPReply(status=HTTPStatus.NOT_FOUND)}
    with HTTPFixture(routes) as media:
        document = f'<html><body><img src="{media.url("/image")}"><img src="{media.url("/missing")}"></body></html>'
        with HTTPFixture({"/post": HTTPReply(document.encode(), headers={"Content-Type": "text/html"})}) as server:
            yield server.url("/post")


@pytest.mark.slow
def test_native_browser_partial_download_preserves_positions() -> None:
    """Native browser retains successes and explicitly identifies the missing media slot."""
    with _partial_download_url() as url:
        service = NativeBrowserService(allowed_hosts=frozenset({"127.0.0.1", "localhost"}))
        try:
            payload, results = service.extract_and_download(url, _extract_images)
            _require_partial_download(payload, results)
        finally:
            service.close()


@pytest.mark.parametrize(
    ("cookie", "url", "expected"),
    [
        ({"domain": "example.com"}, "https://example.com/", True),
        ({"domain": "example.com"}, "https://sub.example.com/", False),
        ({"domain": ".example.com"}, "https://sub.example.com/", True),
        ({"domain": ".example.com"}, "https://example.com/", True),
        ({"domain": ".example.com"}, "https://otherexample.com/", False),
        ({"domain": "example.com", "path": "/a"}, "https://example.com/a/b", True),
        ({"domain": "example.com", "path": "/a"}, "https://example.com/ab", False),
        ({"domain": "example.com", "secure": True}, "http://example.com/", False),
        ({"domain": "example.com", "secure": True}, "https://example.com/", True),
        ({"domain": "example.com", "name": None}, "https://example.com/", False),
        ({"domain": "example.com", "path": None}, "https://example.com/", False),
    ],
)
def test_cookie_transfer_respects_destination(cookie: dict[str, object], url: str, expected: bool) -> None:
    """Transient cookies never cross host, path or secure-transport boundaries."""
    scoped = {"name": "synthetic", "value": "fixture", **cookie}
    assert cookies_for_url([scoped], url) == ({"synthetic": "fixture"} if expected else {})


@pytest.mark.slow
@pytest.mark.subprocess
def test_sigkilled_worker_child_terminates_owned_chromium(tmp_path: Path) -> None:
    """A SIGKILLed worker child cannot leave its owned Chromium process behind."""
    if sys.platform != "linux" or find_browser_executable() is None:
        pytest.skip("Parent-death browser cleanup requires Linux and Chromium")
    pid_file = tmp_path / "chromium.pid"
    process = multiprocessing.get_context("spawn").Process(target=hold_owned_chromium, args=(str(pid_file),))
    process.start()
    try:
        deadline = time.monotonic() + 20
        while not pid_file.exists():
            assert process.is_alive()
            if time.monotonic() > deadline:
                pytest.fail("Owned browser did not start")
            time.sleep(0.05)
        chromium_pid = int(pid_file.read_text())
        assert Path(f"/proc/{chromium_pid}").exists()

        os.kill(process.pid, signal.SIGKILL)
        process.join(10)
        assert not process.is_alive()

        deadline = time.monotonic() + 5
        while Path(f"/proc/{chromium_pid}").exists():
            if time.monotonic() > deadline:
                pytest.fail("Chromium survived its owning worker child")
            time.sleep(0.05)
    finally:
        if process.is_alive():
            process.kill()
        process.join(5)
        process.close()
