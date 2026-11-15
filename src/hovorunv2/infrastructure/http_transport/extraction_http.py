"""Bound native media reads by libcurl's total timeout and one shared deadline."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial
from http import HTTPStatus

from curl_cffi.requests import Session
from curl_cffi.requests.exceptions import RequestException

from hovorunv2.application.jobs.http_ports import HTTPFailure, HTTPHeaders, HTTPQuery, HTTPTransportError
from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.infrastructure.http_transport.response_body import BoundedResponseBody
from hovorunv2.infrastructure.http_transport.sync_base import (
    MAX_DOWNLOAD_BYTES,
    MAX_REDIRECTS,
    MAX_RESPONSE_BYTES,
    SyncHTTPClientBase,
    decode_json_bytes,
)

MIN_CURL_TIMEOUT_SECONDS = 0.001


@dataclass(frozen=True)
class _ReadResponse:
    body: bytes = field(repr=False)
    status: int
    content_type: str
    location: str = field(repr=False)
    url: str = field(repr=False)


def _read_response_status(response: _ReadResponse) -> int:
    return response.status


def _read_response_location(response: _ReadResponse) -> str:
    return response.location


class ExtractionHTTPClient(SyncHTTPClientBase):
    """Own non-streaming libcurl transfers; callbacks bound bytes without background workers."""

    _client_label = "Extraction HTTP"

    def __init__(
        self,
        deadline: ExtractionDeadline,
        *,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        max_redirects: int = MAX_REDIRECTS,
        allowed_hosts: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(
            max_bytes=max_bytes,
            max_response_bytes=max_response_bytes,
            max_redirects=max_redirects,
            allowed_hosts=allowed_hosts,
        )
        self._deadline = deadline
        self._session = Session(trust_env=False)

    def _close_transport(self) -> None:
        self._session.close()

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
        limit: int,
        url: str,
        params: HTTPQuery | None,
        headers: dict[str, str],
        cookies: dict[str, str],
    ) -> _ReadResponse:
        body = BoundedResponseBody(limit)
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
        response = self._execute_redirect_loop(
            "GET",
            url,
            params=params,
            headers=headers,
            cookies=cookies,
            send_fn=partial(self._read_once, limit),
            status_fn=_read_response_status,
            location_fn=_read_response_location,
        )
        if response.status != HTTPStatus.OK:
            raise HTTPTransportError(HTTPFailure.REJECTED)
        return response

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
        return decode_json_bytes(response.body)

    def download_bytes(
        self, url: str, headers: HTTPHeaders | None = None, *, cookies: Mapping[str, str] | None = None
    ) -> bytes:
        """Return bounded media bytes within the original shared deadline."""
        return self._read(url, self._max_bytes, headers=headers, cookies=cookies).body
