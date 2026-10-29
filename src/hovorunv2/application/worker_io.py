"""Narrow synchronous HTTP contracts for native worker transports and providers."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol

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
    """Bounded native network I/O; ordinary extraction failures return no payload."""

    def get_json(self, url: str, params: HTTPQuery | None = None, headers: HTTPHeaders | None = None) -> object:
        """Return decoded JSON or None for an unavailable extraction endpoint."""
        ...

    def get_text(self, url: str, params: HTTPQuery | None = None, headers: HTTPHeaders | None = None) -> str | None:
        """Return bounded response text without exposing remote errors."""
        ...

    def resolve_url(self, url: str, headers: HTTPHeaders | None = None) -> str | None:
        """Resolve a bounded chain of HTTP redirects."""
        ...

    def download(
        self,
        url: str,
        headers: HTTPHeaders | None = None,
        *,
        cookies: Mapping[str, str] | None = None,
    ) -> bytes | None:
        """Return bounded media bytes with optional host-bound browser cookies."""
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
