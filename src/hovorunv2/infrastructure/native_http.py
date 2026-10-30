"""Bounded synchronous HTTP owned by the native worker process using it."""

import json
import time
from collections.abc import Mapping
from http import HTTPStatus
from os import getpid
from types import TracebackType
from typing import Self
from urllib.parse import urljoin, urlsplit

import requests

from hovorunv2.application.worker_io import (
    HTTPFailure,
    HTTPFiles,
    HTTPForm,
    HTTPHeaders,
    HTTPQuery,
    HTTPTransportError,
)

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
HTTP_CHUNK_BYTES = 64 * 1024
MAX_REDIRECTS = 5
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 30
TOTAL_TIMEOUT_SECONDS = 60
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


def _validate_url(url: str) -> tuple[str, str, int | None]:
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise HTTPTransportError(HTTPFailure.REJECTED)
        if parts.username or parts.password:
            raise HTTPTransportError(HTTPFailure.REJECTED)
        origin = parts.scheme, parts.hostname, parts.port
    except ValueError:
        raise HTTPTransportError(HTTPFailure.REJECTED) from None
    else:
        return origin


def _require_success(response: requests.Response) -> None:
    if response.status_code != HTTPStatus.OK:
        raise HTTPTransportError(HTTPFailure.REJECTED)


def _decode_json(raw: bytes) -> object:
    try:
        return json.loads(raw)
    except ValueError, UnicodeError:
        raise HTTPTransportError(HTTPFailure.MALFORMED) from None


def _redirect_target(response: requests.Response, current: str, method: str) -> tuple[str, tuple[str, str, int | None]]:
    try:
        target = urljoin(current, response.headers.get("Location", ""))
        next_origin = _validate_url(target)
    finally:
        response.close()
    # A mutating request is never automatically resent to a redirect target.
    if method != "GET":
        raise HTTPTransportError(HTTPFailure.REJECTED)
    return target, next_origin


class NativeHTTPClient:
    """Never retry requests automatically or retain URL-bearing exception diagnostics."""

    def __init__(
        self,
        *,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
        read_timeout: float = READ_TIMEOUT_SECONDS,
        total_timeout: float = TOTAL_TIMEOUT_SECONDS,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        if min(max_bytes, max_response_bytes, connect_timeout, read_timeout, total_timeout) <= 0 or max_redirects < 0:
            msg = "HTTP bounds must be positive and redirect count nonnegative."
            raise ValueError(msg)
        self._session: requests.Session | None = requests.Session()
        self._session.trust_env = False
        self._owner = getpid()
        self._max_bytes = max_bytes
        self._response_bytes = max_response_bytes
        self._timeout = (connect_timeout, read_timeout)
        self._total_timeout = total_timeout
        self._max_redirects = max_redirects

    def __enter__(self) -> Self:
        """Keep context-managed clients in their owning process."""
        self._require_session()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close every pooled connection deterministically."""
        self.close()

    def _require_session(self) -> requests.Session:
        if self._session is None or self._owner != getpid():
            msg = "Native HTTP requires an open client in its owning process."
            raise ValueError(msg)
        return self._session

    def close(self) -> None:
        """Invalidate the client after closing its owned session."""
        session = self._require_session()
        session.close()
        self._session = None

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: HTTPQuery | None = None,
        headers: HTTPHeaders | None = None,
        data: HTTPForm | None = None,
        files: HTTPFiles | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> requests.Response:
        session = self._require_session()
        origin = _validate_url(url)
        current = url
        request_headers = dict(headers or {})
        cookie_values = dict(cookies or {})
        for _ in range(self._max_redirects + 1):
            response = session.request(
                method,
                current,
                params=params,
                headers=request_headers,
                data=data,
                files=files,
                cookies=cookie_values,
                timeout=self._timeout,
                stream=True,
                allow_redirects=False,
            )
            if response.status_code not in REDIRECT_STATUSES:
                return response
            target, next_origin = _redirect_target(response, current, method)
            if next_origin != origin:
                request_headers = {
                    key: value for key, value in request_headers.items() if key.lower() not in SENSITIVE_HEADERS
                }
                cookie_values = {}
            current, origin, params = target, next_origin, None
        raise HTTPTransportError(HTTPFailure.REJECTED)

    def _read(self, response: requests.Response, limit: int, started: float) -> bytes:
        length = response.headers.get("Content-Length")
        if length is not None and length.isdecimal() and int(length) > limit:
            raise HTTPTransportError(HTTPFailure.TOO_LARGE)
        chunks = []
        size = 0
        for chunk in response.iter_content(HTTP_CHUNK_BYTES):
            size += len(chunk)
            if size > limit:
                raise HTTPTransportError(HTTPFailure.TOO_LARGE)
            if time.monotonic() - started > self._total_timeout:
                raise HTTPTransportError
            chunks.append(chunk)
        return b"".join(chunks)

    def _get_bytes_required(
        self,
        url: str,
        limit: int,
        *,
        params: HTTPQuery | None = None,
        headers: HTTPHeaders | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> bytes:
        started = time.monotonic()
        self._require_session()
        try:
            with self._request("GET", url, params=params, headers=headers, cookies=cookies) as response:
                _require_success(response)
                return self._read(response, limit, started)
        except requests.RequestException, UnicodeError:
            raise HTTPTransportError from None

    def read_json(self, url: str, params: HTTPQuery | None = None, headers: HTTPHeaders | None = None) -> object:
        """Return bounded decoded JSON, including null; operational failure raises a safe transport error."""
        return _decode_json(self._get_bytes_required(url, self._response_bytes, params=params, headers=headers))

    def read_text(self, url: str, params: HTTPQuery | None = None, headers: HTTPHeaders | None = None) -> str:
        """Return bounded provider text; preserve replacement decoding for non-UTF-8 HTML."""
        return self._get_bytes_required(url, self._response_bytes, params=params, headers=headers).decode(
            "utf-8", errors="replace"
        )

    def resolve(self, url: str, headers: HTTPHeaders | None = None) -> str:
        """Require a successful response after the bounded credential-safe redirect chain."""
        self._require_session()
        try:
            with self._request("GET", url, headers=headers) as response:
                _require_success(response)
                return response.url
        except requests.RequestException, UnicodeError:
            raise HTTPTransportError from None

    def download_bytes(
        self, url: str, headers: HTTPHeaders | None = None, *, cookies: Mapping[str, str] | None = None
    ) -> bytes:
        """Return bounded decompressed media bytes or a safe transport failure."""
        return self._get_bytes_required(url, self._max_bytes, headers=headers, cookies=cookies)

    def request_json(
        self, method: str, url: str, *, data: HTTPForm | None = None, files: HTTPFiles | None = None
    ) -> object:
        """Perform one mutating request; a missing valid response remains an uncertain external effect."""
        self._require_session()
        started = time.monotonic()
        try:
            with self._request(method.upper(), url, data=data, files=files) as response:
                raw = self._read(response, self._response_bytes, started)
                result = _decode_json(raw)
                if response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
                    raise HTTPTransportError
                return result
        except requests.RequestException:
            raise HTTPTransportError from None
