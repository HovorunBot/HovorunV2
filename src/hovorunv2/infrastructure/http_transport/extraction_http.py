"""Bound native media reads by libcurl's total timeout and one shared deadline."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from os import getpid
from types import TracebackType
from typing import Self
from urllib.parse import urljoin

from curl_cffi.curl import CURL_WRITEFUNC_ERROR
from curl_cffi.requests import Session
from curl_cffi.requests.exceptions import RequestException

from hovorunv2.application.jobs.http_ports import HTTPFailure, HTTPHeaders, HTTPQuery, HTTPTransportError
from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.infrastructure.http_transport.native_http import (
    MAX_DOWNLOAD_BYTES,
    MAX_REDIRECTS,
    MAX_RESPONSE_BYTES,
    REDIRECT_STATUSES,
    SENSITIVE_HEADERS,
    validate_http_url,
)

MIN_CURL_TIMEOUT_SECONDS = 0.001


@dataclass
class _BoundedBody:
    limit: int
    chunks: list[bytes] = field(default_factory=list, repr=False)
    size: int = 0
    exceeded: bool = False

    def write(self, chunk: bytes) -> int:
        self.size += len(chunk)
        if self.size > self.limit:
            self.exceeded = True
            return CURL_WRITEFUNC_ERROR
        self.chunks.append(chunk)
        return len(chunk)


@dataclass(frozen=True)
class _ReadResponse:
    body: bytes = field(repr=False)
    status: int
    content_type: str
    location: str = field(repr=False)
    url: str = field(repr=False)


class ExtractionHTTPClient:
    """Own non-streaming libcurl transfers; callbacks bound bytes without background workers."""

    def __init__(
        self,
        deadline: ExtractionDeadline,
        *,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        max_redirects: int = MAX_REDIRECTS,
        allowed_hosts: frozenset[str] = frozenset(),
    ) -> None:
        if min(max_bytes, max_response_bytes) <= 0 or max_redirects < 0:
            msg = "Extraction HTTP bounds must be positive and redirects nonnegative."
            raise ValueError(msg)
        self._deadline = deadline
        self._session = Session(trust_env=False)
        self._allowed_hosts = allowed_hosts
        self._owner = getpid()
        self._closed = False
        self._max_bytes = max_bytes
        self._response_bytes = max_response_bytes
        self._max_redirects = max_redirects

    def __enter__(self) -> Self:
        """Validate resource ownership before allowing extraction reads."""
        self._require_open()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the native handle even when a read or worker cancellation fails."""
        self.close()

    def _require_open(self) -> None:
        if self._closed or self._owner != getpid():
            msg = "Extraction HTTP requires an open client in its owning process."
            raise ValueError(msg)

    def close(self) -> None:
        """Release the child-owned native session without retaining request data."""
        self._require_open()
        self._session.close()
        self._closed = True

    def _remaining(self) -> float:
        try:
            remaining = self._deadline.remaining()
        except MediaExtractionError:
            raise HTTPTransportError from None
        # curl_cffi truncates seconds to milliseconds; zero means no timeout.
        if remaining < MIN_CURL_TIMEOUT_SECONDS:
            raise HTTPTransportError
        return remaining

    def _read_once(
        self,
        url: str,
        limit: int,
        headers: dict[str, str],
        cookies: dict[str, str],
        params: HTTPQuery | None,
    ) -> _ReadResponse:
        body = _BoundedBody(limit)
        try:
            response = self._session.get(
                url,
                params=dict(params) if params else None,
                headers=headers,
                cookies=cookies,
                timeout=self._remaining(),
                allow_redirects=False,
                stream=False,
                content_callback=body.write,
                discard_cookies=True,
                proxy="",
            )
        except RequestException:
            reason = HTTPFailure.TOO_LARGE if body.exceeded else HTTPFailure.UNAVAILABLE
            raise HTTPTransportError(reason) from None
        self._remaining()
        return _ReadResponse(
            b"".join(body.chunks),
            response.status_code,
            response.headers.get("Content-Type", "").partition(";")[0].strip().lower(),
            response.headers.get("Location", ""),
            response.url,
        )

    def _read(
        self,
        url: str,
        limit: int,
        *,
        params: HTTPQuery | None = None,
        headers: HTTPHeaders | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> _ReadResponse:
        self._require_open()
        origin = validate_http_url(url, allowed_hosts=self._allowed_hosts)
        request_headers, cookie_values = dict(headers or {}), dict(cookies or {})
        current = url
        for _ in range(self._max_redirects + 1):
            response = self._read_once(current, limit, request_headers, cookie_values, params)
            if response.status not in REDIRECT_STATUSES:
                if response.status != HTTPStatus.OK:
                    raise HTTPTransportError(HTTPFailure.REJECTED)
                return response
            if not response.location:
                raise HTTPTransportError(HTTPFailure.REJECTED)
            current = urljoin(current, response.location)
            next_origin = validate_http_url(current, allowed_hosts=self._allowed_hosts)
            if next_origin != origin:
                request_headers = {
                    key: value for key, value in request_headers.items() if key.lower() not in SENSITIVE_HEADERS
                }
                cookie_values = {}
            origin, params = next_origin, None
        raise HTTPTransportError(HTTPFailure.REJECTED)

    def read_json(self, url: str, params: HTTPQuery | None = None, headers: HTTPHeaders | None = None) -> object:
        """Require a bounded JSON response without accepting challenge/login HTML.

        Args:
            url: Request URL.
            params: Optional public query parameters.
            headers: Explicit request headers.

        Returns:
            Decoded untrusted JSON for application-owned validation.

        Raises:
            HTTPTransportError: If transport, media type or decoding fails.
        """
        response = self._read(url, self._response_bytes, params=params, headers=headers)
        if response.content_type != "application/json":
            raise HTTPTransportError(HTTPFailure.MALFORMED)
        try:
            return json.loads(response.body)
        except ValueError, UnicodeError:
            raise HTTPTransportError(HTTPFailure.MALFORMED) from None

    def download_bytes(
        self, url: str, headers: HTTPHeaders | None = None, *, cookies: Mapping[str, str] | None = None
    ) -> bytes:
        """Return bounded media bytes within the original shared deadline."""
        return self._read(url, self._max_bytes, headers=headers, cookies=cookies).body
