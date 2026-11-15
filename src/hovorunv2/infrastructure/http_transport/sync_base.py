"""Shared synchronous HTTP transport validation, redirect policy, and lifecycle ownership."""

import json
from collections.abc import Callable, Mapping
from http import HTTPStatus
from os import getpid
from types import TracebackType
from typing import Self
from urllib.parse import urljoin, urlsplit

from hovorunv2.application.jobs.http_ports import HTTPFailure, HTTPHeaders, HTTPQuery, HTTPTransportError
from hovorunv2.utils.network_destinations import require_public_host

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
HTTP_CHUNK_BYTES = 64 * 1024
MAX_REDIRECTS = 5
REDIRECT_STATUSES = frozenset(
    {
        HTTPStatus.MOVED_PERMANENTLY,
        HTTPStatus.FOUND,
        HTTPStatus.SEE_OTHER,
        HTTPStatus.TEMPORARY_REDIRECT,
        HTTPStatus.PERMANENT_REDIRECT,
    }
)
SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})

type Origin = tuple[str, str, int | None]


def validate_http_url(url: str, *, allowed_hosts: frozenset[str] = frozenset()) -> Origin:
    """Require an HTTP origin without embedded credentials, invalid port, or private destination.

    Args:
        url: Untrusted request or redirect destination.
        allowed_hosts: Optional test-only explicit allowlist of hostnames or IPs.

    Returns:
        Scheme, hostname and optional port for credential-scope comparison.

    Raises:
        HTTPTransportError: If the target has an unsupported, malformed, or non-public origin.
    """
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise HTTPTransportError(HTTPFailure.REJECTED)
        if parts.username or parts.password:
            raise HTTPTransportError(HTTPFailure.REJECTED)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        require_public_host(parts.hostname, port, allowed_hosts=allowed_hosts)
        origin = parts.scheme, parts.hostname, parts.port
    except ValueError:
        raise HTTPTransportError(HTTPFailure.REJECTED) from None
    else:
        return origin


def decode_json_bytes(raw: bytes) -> object:
    """Decode bounded UTF-8 JSON bytes or raise a safe malformed transport error.

    Args:
        raw: Response body bytes.

    Returns:
        Decoded JSON value.

    Raises:
        HTTPTransportError: If decoding or JSON parsing fails.
    """
    try:
        return json.loads(raw)
    except ValueError, UnicodeError:
        raise HTTPTransportError(HTTPFailure.MALFORMED) from None


def strip_cross_origin_credentials(
    origin: Origin,
    next_origin: Origin,
    headers: dict[str, str],
    cookies: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Strip sensitive headers and cookies when redirecting across origins.

    Args:
        origin: Previous request origin.
        next_origin: Redirect target origin.
        headers: Current request headers.
        cookies: Current request cookies.

    Returns:
        Updated headers and cookies safe for the target origin.
    """
    if next_origin == origin:
        return headers, cookies
    filtered_headers = {key: value for key, value in headers.items() if key.lower() not in SENSITIVE_HEADERS}
    return filtered_headers, {}


def resolve_redirect_target(
    current: str,
    location: str | None,
    method: str,
    *,
    allowed_hosts: frozenset[str] = frozenset(),
) -> tuple[str, Origin]:
    """Validate a redirect location and enforce GET-only redirect following.

    Args:
        current: Current request URL.
        location: Redirect ``Location`` header value.
        method: HTTP method of the current request.
        allowed_hosts: Optional test-only explicit allowlist of hostnames or IPs.

    Returns:
        Resolved target URL and its origin tuple.

    Raises:
        HTTPTransportError: If the location is missing, unsafe, or the method is not GET.
    """
    if not location:
        raise HTTPTransportError(HTTPFailure.REJECTED)
    target = urljoin(current, location)
    next_origin = validate_http_url(target, allowed_hosts=allowed_hosts)
    # A mutating request is never automatically resent to a redirect target.
    if method != "GET":
        raise HTTPTransportError(HTTPFailure.REJECTED)
    return target, next_origin


class SyncHTTPClientBase:
    """Shared process-ownership, SSRF validation, and bounded redirect loop for sync HTTP clients."""

    _client_label: str = "Sync HTTP"

    def __init__(
        self,
        *,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        max_redirects: int = MAX_REDIRECTS,
        allowed_hosts: frozenset[str] = frozenset(),
    ) -> None:
        if min(max_bytes, max_response_bytes) <= 0 or max_redirects < 0:
            msg = f"{self._client_label} bounds must be positive and redirects nonnegative."
            raise ValueError(msg)
        self._allowed_hosts = allowed_hosts
        self._owner = getpid()
        self._closed = False
        self._max_bytes = max_bytes
        self._response_bytes = max_response_bytes
        self._max_redirects = max_redirects

    def __enter__(self) -> Self:
        """Keep context-managed clients in their owning process."""
        self._require_open()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close every pooled connection deterministically."""
        self.close()

    def validate_url(self, url: str) -> None:
        """Require an HTTP origin without embedded credentials, invalid port, or private destination.

        Args:
            url: Untrusted destination to validate.
        """
        validate_http_url(url, allowed_hosts=self._allowed_hosts)

    def _require_open(self) -> None:
        if self._closed or self._owner != getpid():
            msg = f"{self._client_label} requires an open client in its owning process."
            raise ValueError(msg)

    def _close_transport(self) -> None:
        """Release underlying transport handles when closing."""

    def close(self) -> None:
        """Invalidate the client after closing its owned transport."""
        self._require_open()
        self._close_transport()
        self._closed = True

    def _execute_redirect_loop[TResponse](
        self,
        method: str,
        url: str,
        *,
        params: HTTPQuery | None,
        headers: HTTPHeaders | None,
        cookies: Mapping[str, str] | None,
        send_fn: Callable[[str, HTTPQuery | None, dict[str, str], dict[str, str]], TResponse],
        status_fn: Callable[[TResponse], int],
        location_fn: Callable[[TResponse], str | None],
        close_fn: Callable[[TResponse], None] | None = None,
    ) -> TResponse:
        self._require_open()
        origin = validate_http_url(url, allowed_hosts=self._allowed_hosts)
        current = url
        request_headers = dict(headers or {})
        cookie_values = dict(cookies or {})
        for _ in range(self._max_redirects + 1):
            response = send_fn(current, params, request_headers, cookie_values)
            if status_fn(response) not in REDIRECT_STATUSES:
                return response
            location = location_fn(response)
            if close_fn is not None:
                close_fn(response)
            target, next_origin = resolve_redirect_target(current, location, method, allowed_hosts=self._allowed_hosts)
            request_headers, cookie_values = strip_cross_origin_credentials(
                origin, next_origin, request_headers, cookie_values
            )
            current, origin, params = target, next_origin, None
        raise HTTPTransportError(HTTPFailure.REJECTED)
