"""Bounded synchronous HTTP owned by the native worker process using it."""

import ipaddress
import json
import logging
import time
from collections.abc import Mapping
from contextlib import suppress
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from os import getpid
from types import TracebackType
from typing import Self
from urllib.parse import urljoin, urlsplit

import requests
import requests.adapters

from hovorunv2.application.jobs.http_ports import (
    HTTPFailure,
    HTTPFiles,
    HTTPForm,
    HTTPHeaders,
    HTTPQuery,
    HTTPTransportError,
)
from hovorunv2.utils.network_destinations import is_public_address, require_public_host

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
HTTP_CHUNK_BYTES = 64 * 1024
MAX_REDIRECTS = 5
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 30
TOTAL_TIMEOUT_SECONDS = 60
MAX_RATE_LIMIT_RETRIES = 2
MIN_RATE_LIMIT_DELAY_SECONDS = 1.0
MAX_RATE_LIMIT_DELAY_SECONDS = 10.0
logger = logging.getLogger(__name__)
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


def validate_http_url(url: str, *, allowed_hosts: frozenset[str] = frozenset()) -> tuple[str, str, int | None]:
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


def _require_success(response: requests.Response) -> None:
    if response.status_code != HTTPStatus.OK:
        logger.error("HTTP media request rejected (status %s)", response.status_code)
        raise HTTPTransportError(HTTPFailure.REJECTED)


def _parse_retry_seconds(retry_after: str) -> float | None:
    try:
        seconds = float(retry_after)
        if seconds >= 0:
            return seconds
    except ValueError:
        with suppress(ValueError, TypeError):
            date = parsedate_to_datetime(retry_after)
            seconds = date.timestamp() - time.time()
            if seconds >= 0:
                return seconds
    return None


def _parse_retry_delay(
    response: requests.Response,
    attempt: int,
    *,
    min_delay: float = MIN_RATE_LIMIT_DELAY_SECONDS,
    max_delay: float = MAX_RATE_LIMIT_DELAY_SECONDS,
) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        seconds = _parse_retry_seconds(retry_after)
        if seconds is not None:
            return min(max(seconds, min_delay), max_delay)
    return min(max(min_delay * (2**attempt), min_delay), max_delay)


def _decode_json(raw: bytes) -> object:
    try:
        return json.loads(raw)
    except ValueError, UnicodeError:
        raise HTTPTransportError(HTTPFailure.MALFORMED) from None


def _redirect_target(
    response: requests.Response,
    current: str,
    method: str,
    *,
    allowed_hosts: frozenset[str] = frozenset(),
) -> tuple[str, tuple[str, str, int | None]]:
    try:
        target = urljoin(current, response.headers.get("Location", ""))
        next_origin = validate_http_url(target, allowed_hosts=allowed_hosts)
    finally:
        response.close()
    # A mutating request is never automatically resent to a redirect target.
    if method != "GET":
        raise HTTPTransportError(HTTPFailure.REJECTED)
    return target, next_origin


def _verify_peer_destination(resp: object, req_url: str | None, allowed_hosts: frozenset[str]) -> None:
    conn = getattr(resp, "_connection", None)
    sock = getattr(conn, "sock", None)
    getpeername = getattr(sock, "getpeername", None)
    if sock is None or not callable(getpeername):
        return
    close_fn = getattr(resp, "close", None)
    try:
        peer_ip = str(getpeername()[0])
        ip = ipaddress.ip_address(peer_ip)
        host = getattr(conn, "host", "") or req_url or ""
        parts = urlsplit(host if "://" in host else f"http://{host}")
        hostname = parts.hostname or ""
        if hostname not in allowed_hosts and not is_public_address(ip):
            if callable(close_fn):
                close_fn()
            raise HTTPTransportError(HTTPFailure.REJECTED)
    except ValueError, OSError:
        if callable(close_fn):
            close_fn()
        raise HTTPTransportError(HTTPFailure.REJECTED) from None


class _PublicDestinationAdapter(requests.adapters.HTTPAdapter):
    def __init__(
        self,
        *,
        allowed_hosts: frozenset[str] = frozenset(),
        pool_connections: int = 10,
        pool_maxsize: int = 10,
    ) -> None:
        self.allowed_hosts = allowed_hosts
        super().__init__(pool_connections=pool_connections, pool_maxsize=pool_maxsize)

    def build_response(self, req: requests.PreparedRequest, resp: object) -> requests.Response:
        _verify_peer_destination(resp, req.url, self.allowed_hosts)
        return super().build_response(req, resp)


class NativeHTTPClient:
    """Never retry mutating requests automatically or retain URL-bearing exception diagnostics."""

    def __init__(
        self,
        *,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
        read_timeout: float = READ_TIMEOUT_SECONDS,
        total_timeout: float = TOTAL_TIMEOUT_SECONDS,
        max_redirects: int = MAX_REDIRECTS,
        max_rate_limit_retries: int = MAX_RATE_LIMIT_RETRIES,
        min_rate_limit_delay: float = MIN_RATE_LIMIT_DELAY_SECONDS,
        max_rate_limit_delay: float = MAX_RATE_LIMIT_DELAY_SECONDS,
        allowed_hosts: frozenset[str] = frozenset(),
    ) -> None:
        if (
            min(
                max_bytes,
                max_response_bytes,
                connect_timeout,
                read_timeout,
                total_timeout,
                min_rate_limit_delay,
                max_rate_limit_delay,
            )
            <= 0
            or max_redirects < 0
            or max_rate_limit_retries < 0
        ):
            msg = "HTTP bounds must be positive and retry/redirect counts nonnegative."
            raise ValueError(msg)
        self._session: requests.Session | None = requests.Session()
        self._session.trust_env = False
        self._allowed_hosts = allowed_hosts
        adapter = _PublicDestinationAdapter(allowed_hosts=allowed_hosts)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
        self._owner = getpid()
        self._max_bytes = max_bytes
        self._response_bytes = max_response_bytes
        self._timeout = (connect_timeout, read_timeout)
        self._total_timeout = total_timeout
        self._max_redirects = max_redirects
        self._max_rate_limit_retries = max_rate_limit_retries
        self._min_rate_limit_delay = min_rate_limit_delay
        self._max_rate_limit_delay = max_rate_limit_delay

    def __enter__(self) -> Self:
        """Keep context-managed clients in their owning process."""
        self._require_session()
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

    def _should_retry_rate_limit(self, response: requests.Response, method: str, attempt: int) -> bool:
        return (
            response.status_code == HTTPStatus.TOO_MANY_REQUESTS
            and method == "GET"
            and attempt < self._max_rate_limit_retries
        )

    def _execute_attempt(
        self,
        session: requests.Session,
        method: str,
        current: str,
        *,
        params: HTTPQuery | None,
        request_headers: dict[str, str],
        data: HTTPForm | None,
        files: HTTPFiles | None,
        cookie_values: dict[str, str],
        start_time: float,
    ) -> requests.Response:
        for attempt in range(self._max_rate_limit_retries + 1):
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
            if not self._should_retry_rate_limit(response, method, attempt):
                return response
            delay = _parse_retry_delay(
                response,
                attempt,
                min_delay=self._min_rate_limit_delay,
                max_delay=self._max_rate_limit_delay,
            )
            remaining = self._total_timeout - (time.monotonic() - start_time)
            if delay >= remaining:
                return response
            logger.warning(
                "HTTP media request rate limited (status 429), retrying in %.2fs (attempt %d/%d)",
                delay,
                attempt + 1,
                self._max_rate_limit_retries,
            )
            response.close()
            time.sleep(delay)
        return response

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
        started: float | None = None,
    ) -> requests.Response:
        session = self._require_session()
        origin = validate_http_url(url, allowed_hosts=self._allowed_hosts)
        current = url
        request_headers = dict(headers or {})
        cookie_values = dict(cookies or {})
        start_time = started if started is not None else time.monotonic()
        for _ in range(self._max_redirects + 1):
            response = self._execute_attempt(
                session,
                method,
                current,
                params=params,
                request_headers=request_headers,
                data=data,
                files=files,
                cookie_values=cookie_values,
                start_time=start_time,
            )
            if response.status_code not in REDIRECT_STATUSES:
                return response
            target, next_origin = _redirect_target(response, current, method, allowed_hosts=self._allowed_hosts)
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
            with self._request(
                "GET", url, params=params, headers=headers, cookies=cookies, started=started
            ) as response:
                _require_success(response)
                return self._read(response, limit, started)
        except requests.RequestException, UnicodeError:
            logger.exception("Worker HTTP media read failed")
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
        started = time.monotonic()
        self._require_session()
        try:
            with self._request("GET", url, headers=headers, started=started) as response:
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
        try:
            with self._request(method.upper(), url, data=data, files=files) as response:
                raw = self._read(response, self._response_bytes, time.monotonic())
                result = _decode_json(raw)
                if response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
                    raise HTTPTransportError
                return result
        except requests.RequestException:
            raise HTTPTransportError from None
