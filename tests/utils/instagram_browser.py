"""Route synthetic browser navigation and media HTTP to real loopback servers."""

from collections.abc import Callable
from functools import partial, partialmethod
from urllib.parse import urlsplit

import pytest
from curl_cffi.requests import Response, Session

from hovorunv2.application.media.browser import BrowserTab
from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import MediaFailure
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.infrastructure.browser.native_browser import NativeBrowserService
from tests.utils.sync_http import HTTPFixture


def _extract_original(
    tab: BrowserTab,
    fixture_url: str,
    *,
    callback: Callable[[BrowserTab, str], RichMediaPayload | None],
    original_url: str,
) -> RichMediaPayload | None:
    """Keep the canonical post identity while navigating a synthetic external page."""
    return callback(tab, original_url)


class RoutedInstagramBrowser:
    """Use real Chromium and provider callbacks with local synthetic page navigation."""

    def __init__(self, fixture_url: str) -> None:
        self._fixture_url = fixture_url
        self._native = NativeBrowserService()
        self.deadlines: list[ExtractionDeadline | None] = []

    @property
    def is_running(self) -> bool:
        """Observe owned Chromium cleanup."""
        return self._native.is_running

    def extract_and_download(
        self,
        url: str,
        extractor_fn: Callable[[BrowserTab, str], RichMediaPayload | None],
        wait_selector: str | None = None,
        cookies: list[dict[str, object]] | None = None,
        *,
        deadline: ExtractionDeadline | None = None,
    ) -> tuple[RichMediaPayload | None, list[bytes | MediaFailure]]:
        """Navigate locally and execute the unchanged provider callback and bounded downloader."""
        self.deadlines.append(deadline)
        return self._native.extract_and_download(
            self._fixture_url,
            partial(_extract_original, callback=extractor_fn, original_url=url),
            wait_selector,
            cookies,
            deadline=deadline,
        )

    def get_content(
        self, url: str, wait_selector: str | None = None, *, deadline: ExtractionDeadline | None = None
    ) -> str:
        """Read a synthetic external page through the real bounded browser."""
        return self._native.get_content(self._fixture_url, wait_selector, deadline=deadline)

    def close(self) -> None:
        """Release all owned browser resources."""
        self._native.close()


def _route_media_get(
    session: Session,
    url: str,
    *,
    original: Callable[..., Response],
    server: HTTPFixture,
    **kwargs: object,
) -> Response:
    """Route synthetic public media to a real loopback response after application URL validation."""
    parts = urlsplit(url)
    if parts.hostname == "127.0.0.1":
        return original(session, url, **kwargs)
    if parts.hostname != "media.example.invalid":
        msg = "Unexpected external media host in synthetic browser test."
        raise AssertionError(msg)
    return original(session, server.url(parts.path), **kwargs)


def route_media_downloads(monkeypatch: pytest.MonkeyPatch, server: HTTPFixture) -> None:
    """Keep libcurl downloads real while mapping the synthetic public origin to loopback HTTP."""
    monkeypatch.setattr(Session, "get", partialmethod(_route_media_get, original=Session.get, server=server))
