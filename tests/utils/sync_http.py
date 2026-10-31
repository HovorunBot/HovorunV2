"""Real loopback HTTP fixtures with synthetic request capture and deterministic server cleanup."""

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socket import SHUT_RDWR
from threading import Event, Lock, Thread
from types import TracebackType
from typing import Self

from hovorunv2.application.jobs.http_ports import (
    HTTPFailure,
    HTTPFiles,
    HTTPForm,
    HTTPHeaders,
    HTTPQuery,
    HTTPTransportError,
)


@dataclass(frozen=True, slots=True)
class HTTPReply:
    """One synthetic external response or an intentional uncertain connection close."""

    body: bytes = b""
    status: HTTPStatus = HTTPStatus.OK
    headers: Mapping[str, str] = field(default_factory=dict)
    disconnect: bool = False
    delay_seconds: float = 0
    started: Event | None = None


@dataclass(frozen=True, slots=True)
class CapturedRequest:
    """Synthetic loopback request facts for protocol assertions."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes


def json_reply(value: object, *, status: HTTPStatus = HTTPStatus.OK) -> HTTPReply:
    """Encode a synthetic JSON response without calling any external service."""
    return HTTPReply(json.dumps(value).encode(), status, {"Content-Type": "application/json"})


class _FixtureHandler(BaseHTTPRequestHandler):
    def __init__(self, *args: object, fixture: HTTPFixture, **kwargs: object) -> None:
        self._fixture = fixture
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Do not write synthetic URL or request data into server logs."""

    def _respond(self) -> None:
        request = CapturedRequest(self.command, self.path, dict(self.headers.items()), self._read_body())
        response = self._fixture.response(request)
        if response.started is not None:
            response.started.set()
        if response.delay_seconds:
            time.sleep(response.delay_seconds)
        if response.disconnect:
            self.connection.shutdown(SHUT_RDWR)
            self.connection.close()
            return
        self.send_response(response.status)
        for name, value in response.headers.items():
            self.send_header(name, value)
        if "Content-Length" not in response.headers:
            self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)
        self._fixture.complete(request)

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            return self._read_chunked_body()
        return self.rfile.read(int(self.headers.get("Content-Length", "0")))

    def _read_chunked_body(self) -> bytes:
        """Consume real aiohttp multipart streams before acknowledging a synthetic upload."""
        chunks = []
        while True:
            size = int(self.rfile.readline().split(b";", 1)[0], 16)
            if size == 0:
                break
            chunks.append(self.rfile.read(size))
            if self.rfile.read(2) != b"\r\n":
                msg = "Invalid synthetic chunk delimiter."
                raise ValueError(msg)
        while self.rfile.readline() not in (b"\r\n", b""):
            pass
        return b"".join(chunks)

    def do_GET(self) -> None:
        """Serve a synthetic GET endpoint."""
        self._respond()

    def do_HEAD(self) -> None:
        """Serve the same external metadata without sending response content."""
        self._respond()

    def do_POST(self) -> None:
        """Serve a synthetic Telegram POST endpoint."""
        self._respond()


class HTTPFixture:
    """Run a real local HTTP boundary without replacing internal transports or services."""

    def __init__(self, routes: Mapping[str, HTTPReply | list[HTTPReply]]) -> None:
        self._routes = dict(routes)
        self._lock = Lock()
        self.requests: list[CapturedRequest] = []
        self.completed: list[CapturedRequest] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_FixtureHandler, fixture=self))
        self._thread = Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> Self:
        """Start the owned loopback server."""
        self._thread.start()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Stop the server and wait for its thread and request handlers."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        assert not self._thread.is_alive()

    def url(self, path: str) -> str:
        """Return a URL on the ephemeral local port."""
        return f"http://127.0.0.1:{self._server.server_port}{path}"

    def response(self, request: CapturedRequest) -> HTTPReply:
        """Capture a synthetic request and consume the configured external response sequence."""
        with self._lock:
            self.requests.append(request)
            reply = self._routes.get(request.path, self._routes.get(request.path.partition("?")[0]))
            if isinstance(reply, list):
                return reply.pop(0) if reply else HTTPReply(status=HTTPStatus.NOT_FOUND)
            return reply if reply is not None else HTTPReply(status=HTTPStatus.NOT_FOUND)

    def complete(self, request: CapturedRequest) -> None:
        """Record that a delayed response has finished sending before a caller releases its resource."""
        with self._lock:
            self.completed.append(request)


class FakeSyncHTTP:
    """Synchronous HTTP adapter for unit tests simulating external endpoints."""

    def __init__(
        self,
        *,
        json_responses: Mapping[str, object] | None = None,
        text_responses: Mapping[str, str] | None = None,
        redirects: Mapping[str, str] | None = None,
        byte_responses: Mapping[str, bytes] | None = None,
    ) -> None:
        self.json_responses: dict[str, object] = dict(json_responses or {})
        self.text_responses: dict[str, str] = dict(text_responses or {})
        self.redirects: dict[str, str] = dict(redirects or {})
        self.byte_responses: dict[str, bytes] = dict(byte_responses or {})
        self.requests: list[dict[str, object]] = []

    def read_json(
        self,
        url: str,
        params: HTTPQuery | None = None,
        headers: HTTPHeaders | None = None,
    ) -> object:
        """Return configured JSON response or raise HTTPTransportError."""
        self.requests.append({"method": "GET", "url": url, "params": params, "headers": headers})
        for pattern, response in self.json_responses.items():
            if pattern in url or (params and any(pattern in str(v) for v in params.values())):
                if isinstance(response, Exception):
                    raise response
                return response
        raise HTTPTransportError(HTTPFailure.UNAVAILABLE)

    def read_text(
        self,
        url: str,
        params: HTTPQuery | None = None,
        headers: HTTPHeaders | None = None,
    ) -> str:
        """Return configured text response or raise HTTPTransportError."""
        self.requests.append({"method": "GET", "url": url, "params": params, "headers": headers})
        for pattern, response in self.text_responses.items():
            if pattern in url or (params and any(pattern in str(v) for v in params.values())):
                if isinstance(response, Exception):
                    raise response
                return response
        raise HTTPTransportError(HTTPFailure.UNAVAILABLE)

    def resolve(self, url: str, headers: HTTPHeaders | None = None) -> str:
        """Return configured redirect target or echo url."""
        self.requests.append({"method": "RESOLVE", "url": url, "headers": headers})
        for pattern, target in self.redirects.items():
            if pattern in url:
                return target
        return url

    def download_bytes(
        self,
        url: str,
        headers: HTTPHeaders | None = None,
        *,
        cookies: Mapping[str, str] | None = None,
    ) -> bytes:
        """Return configured byte response or raise HTTPTransportError."""
        self.requests.append({"method": "DOWNLOAD", "url": url, "headers": headers, "cookies": cookies})
        for pattern, response in self.byte_responses.items():
            if pattern in url:
                if isinstance(response, Exception):
                    raise response
                return response
        raise HTTPTransportError(HTTPFailure.UNAVAILABLE)

    def request_json(
        self,
        method: str,
        url: str,
        *,
        data: HTTPForm | None = None,
        files: HTTPFiles | None = None,
    ) -> object:
        """Return configured JSON response for mutating request."""
        self.requests.append({"method": method, "url": url, "data": data, "files": files})
        for pattern, response in self.json_responses.items():
            if pattern in url:
                if isinstance(response, Exception):
                    raise response
                return response
        raise HTTPTransportError(HTTPFailure.UNAVAILABLE)
