"""Strict native transport failures and redirect ownership using real loopback HTTP."""

from http import HTTPStatus

import pytest

from hovorunv2.application.jobs.http_ports import HTTPFailure, HTTPTransportError
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
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
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1", "localhost"})) as client,
    ):
        assert client.read_json(server.url("/null")) is None
        with pytest.raises(HTTPTransportError) as malformed:
            client.read_json(server.url("/bad"))
        assert malformed.value.code == HTTPFailure.MALFORMED
        assert str(malformed.value) == HTTPFailure.MALFORMED.value
        with pytest.raises(HTTPTransportError) as rejected:
            client.read_json(server.url("/rejected"))
        assert rejected.value.code == HTTPFailure.REJECTED


@pytest.mark.parametrize("declared", [False, True])
def test_strict_reads_enforce_declared_and_streamed_byte_bounds(*, declared: bool) -> None:
    """Untrusted lengths and actual decoded bytes are independently bounded before returning content."""
    body = b"synthetic body"
    headers = {"Content-Length": "100"} if declared else {}
    with (
        HTTPFixture({"/large": HTTPReply(body, headers=headers)}) as server,
        NativeHTTPClient(max_bytes=4, allowed_hosts=frozenset({"127.0.0.1", "localhost"})) as client,
    ):
        with pytest.raises(HTTPTransportError) as error:
            client.download_bytes(server.url("/large"))
        assert error.value.code == HTTPFailure.TOO_LARGE


def test_strict_redirect_strips_cross_origin_credentials_and_never_replays_post() -> None:
    """Only GET follows redirects; cross-origin targets receive no explicit authorization or cookies."""
    with (
        HTTPFixture({"/target": HTTPReply(b"synthetic")}) as target,
        HTTPFixture(
            {"/start": HTTPReply(status=HTTPStatus.FOUND, headers={"Location": target.url("/target")})}
        ) as origin,
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1", "localhost"})) as client,
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
        NativeHTTPClient(max_redirects=1, allowed_hosts=frozenset({"127.0.0.1", "localhost"})) as client,
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
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1", "localhost"})) as client,
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
        # Rejection of un-allowed private loopback
        with pytest.raises(HTTPTransportError) as error:
            client.read_text("http://127.0.0.1/synthetic")
        assert error.value.code == HTTPFailure.REJECTED
    with pytest.raises(ValueError, match="open client"):
        client.read_text("http://127.0.0.1/synthetic")


def test_native_http_rejects_ssrf_destination_and_redirect_hop() -> None:
    """Direct and redirected requests to private / loopback / metadata destinations are rejected."""
    with (
        HTTPFixture(
            {
                "/redirect-meta": HTTPReply(
                    status=HTTPStatus.FOUND,
                    headers={"Location": "http://169.254.169.254/latest/meta-data"},
                ),
                "/redirect-private": HTTPReply(
                    status=HTTPStatus.FOUND,
                    headers={"Location": "http://10.0.0.1/secrets"},
                ),
            }
        ) as server,
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1", "localhost"})) as client,
    ):
        with pytest.raises(HTTPTransportError) as error:
            client.download_bytes(server.url("/redirect-meta"))
        assert error.value.code == HTTPFailure.REJECTED

        with pytest.raises(HTTPTransportError) as error:
            client.download_bytes(server.url("/redirect-private"))
        assert error.value.code == HTTPFailure.REJECTED

    # Without allowlist, direct requests to loopback/private/metadata are rejected
    with NativeHTTPClient() as unprivileged_client:
        for bad_url in [
            "http://127.0.0.1:8080/test",
            "http://169.254.169.254/latest/meta-data",
            "http://10.0.0.1/admin",
            "http://[::1]/internal",
        ]:
            with pytest.raises(HTTPTransportError) as error:
                unprivileged_client.download_bytes(bad_url)
            assert error.value.code == HTTPFailure.REJECTED


def test_rate_limiting_429_retries_and_succeeds() -> None:
    """A transient 429 response backs off using Retry-After and returns content on retry."""
    with (
        HTTPFixture(
            {
                "/transient-429": [
                    HTTPReply(status=HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": "0.01"}),
                    HTTPReply(b"recovered"),
                ]
            }
        ) as server,
        NativeHTTPClient(
            max_rate_limit_retries=2,
            min_rate_limit_delay=0.01,
            max_rate_limit_delay=0.1,
            allowed_hosts=frozenset({"127.0.0.1", "localhost"}),
        ) as client,
    ):
        result = client.download_bytes(server.url("/transient-429"))
        assert result == b"recovered"
        assert len(server.requests) == 2


def test_rate_limiting_429_exhausted_raises_rejected() -> None:
    """Persistent 429 rate limiting exhausts attempts and raises HTTPFailure.REJECTED."""
    with (
        HTTPFixture(
            {
                "/persistent-429": [
                    HTTPReply(status=HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": "0.01"}),
                    HTTPReply(status=HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": "0.01"}),
                    HTTPReply(status=HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": "0.01"}),
                ]
            }
        ) as server,
        NativeHTTPClient(
            max_rate_limit_retries=1,
            min_rate_limit_delay=0.01,
            max_rate_limit_delay=0.1,
            allowed_hosts=frozenset({"127.0.0.1", "localhost"}),
        ) as client,
    ):
        with pytest.raises(HTTPTransportError) as error:
            client.download_bytes(server.url("/persistent-429"))
        assert error.value.code == HTTPFailure.REJECTED
        assert len(server.requests) == 2


def test_mutating_request_never_retries_on_429() -> None:
    """Mutating POST requests never automatically retry even when receiving 429."""
    with (
        HTTPFixture(
            {
                "/post-429": json_reply({"ok": False}, status=HTTPStatus.TOO_MANY_REQUESTS),
            }
        ) as server,
        NativeHTTPClient(
            max_rate_limit_retries=2,
            min_rate_limit_delay=0.01,
            max_rate_limit_delay=0.1,
            allowed_hosts=frozenset({"127.0.0.1", "localhost"}),
        ) as client,
    ):
        assert client.request_json("POST", server.url("/post-429")) == {"ok": False}
        assert len(server.requests) == 1


def test_rate_limiting_429_respects_total_timeout() -> None:
    """When retry delay would exceed total timeout, request is rejected without sleeping past deadline."""
    with (
        HTTPFixture(
            {
                "/timeout-429": HTTPReply(status=HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": "100"}),
            }
        ) as server,
        NativeHTTPClient(
            total_timeout=0.2,
            max_rate_limit_retries=2,
            min_rate_limit_delay=0.01,
            max_rate_limit_delay=100.0,
            allowed_hosts=frozenset({"127.0.0.1", "localhost"}),
        ) as client,
    ):
        with pytest.raises(HTTPTransportError) as error:
            client.download_bytes(server.url("/timeout-429"))
        assert error.value.code == HTTPFailure.REJECTED
        assert len(server.requests) == 1
