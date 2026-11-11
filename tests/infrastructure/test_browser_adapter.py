"""Verify browser protocol values, lookup semantics, and isolated job state."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from hovorunv2.application.media.browser import BrowserTab
from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import BrowserError
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.infrastructure.browser.native_browser import NativeBrowserService
from tests.utils.browser_session import browser_tab
from tests.utils.sync_http import HTTPFixture, HTTPReply

pytestmark = pytest.mark.browser


@pytest.mark.subprocess
@pytest.mark.skipif(sys.platform != "linux", reason="Fork isolation requires Linux")
def test_sdk_import_annotations_and_forked_first_use() -> None:
    """SDK imports stay lazy and each forked child owns a working daemon loop and Chromium."""
    subprocess.run(
        [sys.executable, "-m", "tests.utils.browser_import_probe"],
        check=True,
        timeout=45,
        capture_output=True,
    )


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("return {ready: true, values: [1, 'ok', null]}", {"ready": True, "values": [1, "ok", None]}),
        ("[1, 2]", [1, 2]),
        ("'ok'", "ok"),
        ("42", 42),
        ("true", True),
        ("null", None),
    ],
)
def test_javascript_returns_python_values(script: str, expected: object) -> None:
    """Unwrap JavaScript metadata instead of returning an SDK response envelope."""
    with browser_tab(ExtractionDeadline.after(15)) as tab:
        assert tab.run_js(script) == expected


def test_javascript_failure_is_a_safe_browser_error() -> None:
    """A thrown script error must not become a missing-value result or expose provider data."""
    with browser_tab(ExtractionDeadline.after(15)) as tab:
        with pytest.raises(BrowserError) as failure:
            tab.run_js("throw new Error('synthetic-private-value')")
        assert "synthetic-private-value" not in str(failure.value)


@pytest.mark.parametrize("message", ["synthetic", "Illegal return statement"])
def test_runtime_syntax_error_never_repeats_script_side_effects(message: str) -> None:
    """An executed script throwing SyntaxError must not be retried as a parse failure."""
    with browser_tab(ExtractionDeadline.after(15)) as tab:
        tab.run_js("window.syntheticCalls = 0")
        with pytest.raises(BrowserError):
            tab.run_js(f"window.syntheticCalls++; throw new SyntaxError({json.dumps(message)})")
        assert tab.run_js("return window.syntheticCalls") == 1


def test_compound_css_and_quoted_text_lookup_can_click() -> None:
    """Both CSS lists and text containing both quote styles select an interactive element."""
    page = (
        b"<html><body><main><button onclick=\"this.dataset.clicked='yes'\">"
        b"Accept 'both' &quot;quotes&quot;</button></main></body></html>"
    )
    with HTTPFixture({"/post": HTTPReply(page)}) as server, browser_tab(ExtractionDeadline.after(15)) as tab:
        assert tab.get(server.url("/post"), timeout=5)
        assert tab.ele('article, main, [role="main"]', timeout=1) is not None
        button = tab.ele("text=Accept 'both' \"quotes\"", timeout=1)
        assert button is not None
        button.click()
        assert tab.run_js("return document.querySelector('button').dataset.clicked") == "yes"
        assert tab.ele(".missing", timeout=0.1) is None


def test_missing_wait_selector_rejects_incomplete_content() -> None:
    """A completed navigation without its required selector cannot count as usable content."""
    with HTTPFixture({"/post": HTTPReply(b"<html><main>incomplete</main></html>")}) as server:
        service = NativeBrowserService()
        try:
            with pytest.raises(BrowserError):
                service.get_content(server.url("/post"), ".required", deadline=ExtractionDeadline.after(15))
        finally:
            service.close()
        assert not service.is_running


def _require_cookie(tab: BrowserTab, url: str) -> RichMediaPayload | None:
    """Verify a supplied transient cookie is visible only in its owning extraction."""
    assert "session=synthetic" in str(tab.run_js("return document.cookie"))
    return None


def _require_empty_state(tab: BrowserTab, url: str) -> RichMediaPayload | None:
    """Verify another job inherits neither cookies nor browser local storage."""
    assert tab.run_js("return document.cookie") == ""
    assert tab.run_js("return localStorage.getItem('synthetic')") is None
    return None


def _store_state(tab: BrowserTab, url: str) -> RichMediaPayload | None:
    """Set browser storage that must disappear before another extraction."""
    tab.run_js("document.cookie='session=synthetic'; localStorage.setItem('synthetic', 'value'); return true")
    return None


def test_jobs_isolate_supplied_cookies_and_browser_storage() -> None:
    """Reusing Chromium does not reuse another extraction's cookie or storage state."""
    with HTTPFixture({"/post": HTTPReply(b"<html><main>ready</main></html>")}) as server:
        service = NativeBrowserService()
        try:
            cookie: dict[str, object] = {"name": "session", "value": "synthetic", "domain": "127.0.0.1", "path": "/"}
            assert service.extract_and_download(server.url("/post"), _require_cookie, cookies=[cookie]) == (None, [])
            assert service.extract_and_download(server.url("/post"), _require_empty_state) == (None, [])
            assert service.extract_and_download(server.url("/post"), _store_state) == (None, [])
            assert service.extract_and_download(server.url("/post"), _require_empty_state) == (None, [])
            assert service.is_running
        finally:
            service.close()
        assert not service.is_running


def test_script_deadline_interrupts_unresolved_promise() -> None:
    """A stalled asynchronous script remains bounded by the caller's extraction cutoff."""
    with browser_tab(ExtractionDeadline.after(4)) as tab:
        started = time.monotonic()
        with pytest.raises(BrowserError):
            tab.run_js("return new Promise(() => {})")
        assert time.monotonic() - started < 5


@pytest.mark.skipif(sys.platform != "linux", reason="Process recovery inspection requires Linux")
def test_reused_service_recovers_after_chromium_is_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A later extraction recovers from external Chromium termination without retaining old resources."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with HTTPFixture({"/post": HTTPReply(b"<html><main>ready</main></html>")}) as server:
        service = NativeBrowserService()
        try:
            assert "ready" in service.get_content(server.url("/post"))
            profile = next(tmp_path.glob("hovorun-browser-*"))
            pid = int((profile / "SingletonLock").readlink().name.rsplit("-", 1)[1])
            os.kill(pid, signal.SIGKILL)
            assert "ready" in service.get_content(server.url("/post"))
            assert not Path(f"/proc/{pid}").exists()
            assert not profile.exists()
            assert service.is_running
        finally:
            service.close()
        assert not list(tmp_path.glob("hovorun-browser-*"))
