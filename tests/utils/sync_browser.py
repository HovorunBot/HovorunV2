"""Synchronous browser test fixture implementing SyncBrowserPort."""

from collections.abc import Callable, Mapping

from hovorunv2.application.jobs.http_ports import SyncBrowserPort
from hovorunv2.application.media.browser import BrowserTab
from hovorunv2.application.media.errors import BrowserError, MediaFailure
from hovorunv2.application.media.payloads import RichMediaPayload


class FakeSyncBrowser(SyncBrowserPort):
    """Synchronous browser test fixture implementing SyncBrowserPort."""

    def __init__(
        self,
        *,
        html_responses: Mapping[str, str] | None = None,
        extract_payloads: Mapping[str, tuple[RichMediaPayload | None, list[bytes | MediaFailure]]] | None = None,
    ) -> None:
        self.html_responses: dict[str, str] = dict(html_responses or {})
        self.extract_payloads: dict[str, tuple[RichMediaPayload | None, list[bytes | MediaFailure]]] = dict(
            extract_payloads or {}
        )
        self.requests: list[dict[str, object]] = []

    def get_content(self, url: str, wait_selector: str | None = None) -> str:
        """Return configured HTML content or raise BrowserError."""
        self.requests.append({"action": "get_content", "url": url, "wait_selector": wait_selector})
        for pattern, html in self.html_responses.items():
            if pattern in url:
                return html
        msg = f"No fake browser content configured for {url}"
        raise BrowserError(msg)

    def extract_and_download(
        self,
        url: str,
        extractor_fn: Callable[[BrowserTab, str], RichMediaPayload | None],
        wait_selector: str | None = None,
        cookies: list[dict[str, object]] | None = None,
    ) -> tuple[RichMediaPayload | None, list[bytes | MediaFailure]]:
        """Return configured extract payload or raise BrowserError."""
        self.requests.append(
            {
                "action": "extract_and_download",
                "url": url,
                "wait_selector": wait_selector,
                "cookies": cookies,
            }
        )
        for pattern, res in self.extract_payloads.items():
            if pattern in url:
                return res
        return None, []

    def close(self) -> None:
        """Deterministically close fake browser."""
