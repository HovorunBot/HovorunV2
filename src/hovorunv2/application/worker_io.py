"""Narrow synchronous HTTP contracts for native worker transports and providers."""

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Protocol

from hovorunv2.application.browser import BrowserTab
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.errors import MediaFailure

HTTPHeaders = Mapping[str, str]
HTTPQuery = Mapping[str, str | int | float | bool]
HTTPForm = Mapping[str, str | int | float | bool]
HTTPFiles = Mapping[str, tuple[str, bytes, str]]


class HTTPFailure(StrEnum):
    """Allowlisted technical failures without remote response or URL contents."""

    UNAVAILABLE = "http_unavailable"
    REJECTED = "http_rejected"
    MALFORMED = "http_malformed"
    TOO_LARGE = "http_too_large"


class HTTPTransportError(OSError):
    """A request failed without a safe, usable remote response."""

    def __init__(self, code: HTTPFailure = HTTPFailure.UNAVAILABLE) -> None:
        self.code = code
        super().__init__(code.value)


class SyncHTTPPort(Protocol):
    """Bounded native I/O with required reads and explicit transport failures."""

    def read_json(self, url: str, params: HTTPQuery | None = None, headers: HTTPHeaders | None = None) -> object:
        """Return decoded JSON, including null; raise HTTPTransportError for transport/decode failure."""
        ...

    def read_text(self, url: str, params: HTTPQuery | None = None, headers: HTTPHeaders | None = None) -> str:
        """Return bounded text or raise HTTPTransportError."""
        ...

    def resolve(self, url: str, headers: HTTPHeaders | None = None) -> str:
        """Return a successful bounded redirect result or raise HTTPTransportError."""
        ...

    def download_bytes(
        self, url: str, headers: HTTPHeaders | None = None, *, cookies: Mapping[str, str] | None = None
    ) -> bytes:
        """Return bounded media bytes or raise HTTPTransportError."""
        ...

    def request_json(
        self,
        method: str,
        url: str,
        *,
        data: HTTPForm | None = None,
        files: HTTPFiles | None = None,
    ) -> object:
        """Return a JSON response including explicit remote rejection; uncertain I/O raises HTTPTransportError."""
        ...


class SyncBrowserPort(Protocol):
    """Protocol for synchronous worker headless browser automation."""

    def get_content(self, url: str, wait_selector: str | None = None) -> str:
        """Return rendered HTML; navigation or browser failures raise BrowserError."""
        ...

    def extract_and_download(
        self,
        url: str,
        extractor_fn: Callable[[BrowserTab, str], RichMediaPayload | None],
        wait_selector: str | None = None,
        cookies: list[dict[str, object]] | None = None,
    ) -> tuple[RichMediaPayload | None, list[bytes | MediaFailure]]:
        """Run extraction and download in one synchronous browser session."""
        ...
