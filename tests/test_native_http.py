"""Strict native transport failures and redirect ownership using real loopback HTTP."""

from http import HTTPStatus

import pytest

from hovorunv2.application.worker_io import HTTPFailure, HTTPTransportError
from hovorunv2.infrastructure.native_http import NativeHTTPClient
from tests.utils.sync_http import HTTPFixture, HTTPReply, json_reply


def test_strict_json_distinguishes_null_from_transport_and_decode_failure() -> None:
    """Decoded null is data; rejected or malformed responses carry only safe failure codes."""
    with (
        HTTPFixture(
            {
                "/null": json_reply(None),
                "/bad": HTTPReply(b"invalid-json"),
                "/rejected": HTTPReply(status=HTTPStatus.FORBIDDEN),
            }
        ) as server,
        NativeHTTPClient() as client,
    ):
        assert client.read_json(server.url("/null")) is None
        with pytest.raises(HTTPTransportError) as malformed:
            client.read_json(server.url("/bad"))
        assert malformed.value.code == HTTPFailure.MALFORMED
        assert str(malformed.value) == HTTPFailure.MALFORMED.value
        with pytest.raises(HTTPTransportError) as rejected:
            client.read_json(server.url("/rejected"))
        assert rejected.value.code == HTTPFailure.REJECTED
        assert client.get_json(server.url("/bad")) is None
        assert client.get_json(server.url("/rejected")) is None


@pytest.mark.parametrize("declared", [False, True])
def test_strict_reads_enforce_declared_and_streamed_byte_bounds(*, declared: bool) -> None:
    """Untrusted lengths and actual decoded bytes are independently bounded before returning content."""
    body = b"synthetic body"
    headers = {"Content-Length": "100"} if declared else {}
    with HTTPFixture({"/large": HTTPReply(body, headers=headers)}) as server, NativeHTTPClient(max_bytes=4) as client:
        with pytest.raises(HTTPTransportError) as error:
            client.download_bytes(server.url("/large"))
        assert error.value.code == HTTPFailure.TOO_LARGE
        assert client.download(server.url("/large")) is None


def test_strict_redirect_strips_cross_origin_credentials_and_never_replays_post() -> None:
    """Only GET follows redirects; cross-origin targets receive no explicit authorization or cookies."""
    with (
        HTTPFixture({"/target": HTTPReply(b"synthetic")}) as target,
        HTTPFixture(
            {"/start": HTTPReply(status=HTTPStatus.FOUND, headers={"Location": target.url("/target")})}
        ) as origin,
        NativeHTTPClient() as client,
    ):
        headers = {"Authorization": "synthetic", "Cookie": "synthetic=1"}
        assert client.download_bytes(origin.url("/start"), headers, cookies={"synthetic": "1"}) == b"synthetic"
        assert "Authorization" in origin.requests[0].headers
        assert "Authorization" not in target.requests[0].headers
        assert "Cookie" not in target.requests[0].headers
        previous = len(target.requests)
        with pytest.raises(HTTPTransportError) as error:
            client.request_json("POST", origin.url("/start"), data={"synthetic": "input"})
        assert error.value.code == HTTPFailure.REJECTED
        assert len(target.requests) == previous
        assert [request.method for request in origin.requests] == ["GET", "POST"]


def test_strict_redirect_count_is_bounded() -> None:
    """A redirect loop stops at the configured hop count without returning a false resolution."""
    with (
        HTTPFixture({"/loop": HTTPReply(status=HTTPStatus.FOUND, headers={"Location": "/loop"})}) as server,
        NativeHTTPClient(max_redirects=1) as client,
    ):
        with pytest.raises(HTTPTransportError) as error:
            client.resolve(server.url("/loop"))
        assert error.value.code == HTTPFailure.REJECTED
        assert len(server.requests) == 2


def test_mutating_json_preserves_explicit_rejection_and_uncertain_single_attempt() -> None:
    """Telegram-style remote rejection remains JSON; a lost response never triggers another POST."""
    with (
        HTTPFixture(
            {
                "/reject": json_reply({"ok": False}, status=HTTPStatus.BAD_REQUEST),
                "/server": json_reply({"ok": False}, status=HTTPStatus.INTERNAL_SERVER_ERROR),
                "/lost": HTTPReply(disconnect=True),
            }
        ) as server,
        NativeHTTPClient() as client,
    ):
        assert client.request_json("POST", server.url("/reject")) == {"ok": False}
        with pytest.raises(HTTPTransportError) as error:
            client.request_json("POST", server.url("/server"))
        assert error.value.code == HTTPFailure.UNAVAILABLE
        with pytest.raises(HTTPTransportError):
            client.request_json("POST", server.url("/lost"))
        assert [request.path for request in server.requests] == ["/reject", "/server", "/lost"]


def test_strict_reads_retain_closed_owner_and_url_guards() -> None:
    """Required operations reject invalid URLs and never turn a closed client into an optional miss."""
    client = NativeHTTPClient()
    with client:
        with pytest.raises(HTTPTransportError) as error:
            client.read_text("file:///synthetic")
        assert error.value.code == HTTPFailure.REJECTED
    with pytest.raises(ValueError, match="open client"):
        client.read_text("http://127.0.0.1/synthetic")
