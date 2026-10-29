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
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise HTTPTransportError(HTTPFailure.REJECTED)
        origin = parts.scheme, parts.hostname, parts.port
    except ValueError:
        raise HTTPTransportError(HTTPFailure.REJECTED) from None
    else:
        return origin


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
            try:
                target = urljoin(current, response.headers.get("Location", ""))
                next_origin = _validate_url(target)
            finally:
                response.close()
            # A mutating request is never automatically resent to a redirect target.
            if method != "GET":
                raise HTTPTransportError(HTTPFailure.REJECTED)
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

    def _get_bytes(
        self,
        url: str,
        limit: int,
        *,
        params: HTTPQuery | None = None,
        headers: HTTPHeaders | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> bytes | None:
        started = time.monotonic()
        self._require_session()
        try:
            with self._request("GET", url, params=params, headers=headers, cookies=cookies) as response:
                if response.status_code != HTTPStatus.OK:
                    return None
                return self._read(response, limit, started)
        except requests.RequestException, HTTPTransportError, UnicodeError:
            return None

    def get_json(self, url: str, params: HTTPQuery | None = None, headers: HTTPHeaders | None = None) -> object:
        """Read bounded UTF-8 JSON without retaining malformed response details."""
        raw = self._get_bytes(url, self._response_bytes, params=params, headers=headers)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError, UnicodeError:
            return None

    def get_text(self, url: str, params: HTTPQuery | None = None, headers: HTTPHeaders | None = None) -> str | None:
        """Read bounded provider HTML as UTF-8 text."""
        raw = self._get_bytes(url, self._response_bytes, params=params, headers=headers)
        return raw.decode("utf-8", errors="replace") if raw is not None else None

    def resolve_url(self, url: str, headers: HTTPHeaders | None = None) -> str | None:
        """Resolve only a bounded chain of valid GET redirects."""
        self._require_session()
        try:
            with self._request("GET", url, headers=headers) as response:
                return response.url if response.status_code == HTTPStatus.OK else None
        except requests.RequestException, HTTPTransportError, UnicodeError:
            return None

    def download(
        self, url: str, headers: HTTPHeaders | None = None, *, cookies: Mapping[str, str] | None = None
    ) -> bytes | None:
        """Count decompressed streaming bytes and keep browser cookies transient."""
        return self._get_bytes(url, self._max_bytes, headers=headers, cookies=cookies)

    def request_json(
        self, method: str, url: str, *, data: HTTPForm | None = None, files: HTTPFiles | None = None
    ) -> object:
        """Perform one mutating request; a missing valid response remains an uncertain external effect."""
        self._require_session()
        started = time.monotonic()
        try:
            with self._request(method.upper(), url, data=data, files=files) as response:
                raw = self._read(response, self._response_bytes, started)
                try:
                    result = json.loads(raw)
                except ValueError, UnicodeError:
                    raise HTTPTransportError(HTTPFailure.MALFORMED) from None
                if response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
                    raise HTTPTransportError
                return result
        except requests.RequestException:
            raise HTTPTransportError from None
