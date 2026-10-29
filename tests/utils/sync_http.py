"""Real loopback HTTP fixtures with synthetic request capture and deterministic server cleanup."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socket import SHUT_RDWR
from threading import Lock, Thread
from types import TracebackType
from typing import Self


@dataclass(frozen=True, slots=True)
class HTTPReply:
    """One synthetic external response or an intentional uncertain connection close."""

    body: bytes = b""
    status: HTTPStatus = HTTPStatus.OK
    headers: Mapping[str, str] = field(default_factory=dict)
    disconnect: bool = False


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
        length = int(self.headers.get("Content-Length", "0"))
        request = CapturedRequest(self.command, self.path, dict(self.headers.items()), self.rfile.read(length))
        response = self._fixture.response(request)
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
