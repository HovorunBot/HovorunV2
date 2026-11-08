"""Deadline-bounded native extraction HTTP using real loopback servers."""

import time
from http import HTTPStatus

import pytest

from hovorunv2.application.jobs.http_ports import HTTPFailure, HTTPTransportError
from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.infrastructure.http_transport.extraction_http import ExtractionHTTPClient
from tests.utils.sync_http import HTTPFixture, HTTPReply, json_reply

TIMING_TOLERANCE_SECONDS = 1.0


def test_download_aborts_oversized_body_with_too_large() -> None:
    """The libcurl write callback aborts an over-bound body instead of truncating it."""
    with (
        HTTPFixture({"/large": HTTPReply(b"1234567890")}) as server,
        ExtractionHTTPClient(
            ExtractionDeadline.after(5), max_bytes=4, allowed_hosts=frozenset({"127.0.0.1", "localhost"})
        ) as client,
    ):
        with pytest.raises(HTTPTransportError) as error:
            client.download_bytes(server.url("/large"))
        assert error.value.code == HTTPFailure.TOO_LARGE


def test_read_json_requires_json_content_type() -> None:
    """An HTML challenge page is rejected, never decoded as public JSON."""
    with (
        HTTPFixture({"/login": HTTPReply(b"<html>sign in</html>", headers={"Content-Type": "text/html"})}) as server,
        ExtractionHTTPClient(
            ExtractionDeadline.after(5), allowed_hosts=frozenset({"127.0.0.1", "localhost"})
        ) as client,
    ):
        with pytest.raises(HTTPTransportError) as error:
            client.read_json(server.url("/login"))
        assert error.value.code == HTTPFailure.MALFORMED


def test_read_json_decodes_valid_public_payload() -> None:
    """A bounded JSON response is decoded for application-owned validation."""
    with (
        HTTPFixture({"/ok": json_reply({"ok": True})}) as server,
        ExtractionHTTPClient(
            ExtractionDeadline.after(5), allowed_hosts=frozenset({"127.0.0.1", "localhost"})
        ) as client,
    ):
        assert client.read_json(server.url("/ok")) == {"ok": True}


def test_exhausted_deadline_rejects_before_any_request() -> None:
    """An expired deadline cannot open a network connection."""
    expired = ExtractionDeadline(time.monotonic() - 0.1)
    with (
        HTTPFixture({"/x": HTTPReply(b"ok")}) as server,
        ExtractionHTTPClient(expired, allowed_hosts=frozenset({"127.0.0.1", "localhost"})) as client,
        pytest.raises(HTTPTransportError),
    ):
        client.download_bytes(server.url("/x"))
    assert len(server.requests) == 0


def test_deadline_bounds_delayed_headers() -> None:
    """Slow headers consume the shared deadline instead of restarting a socket budget."""
    deadline = ExtractionDeadline.after(0.4)
    with (
        HTTPFixture({"/slow": HTTPReply(b"ok", delay_seconds=2.0)}) as server,
        ExtractionHTTPClient(deadline, allowed_hosts=frozenset({"127.0.0.1", "localhost"})) as client,
    ):
        started = time.monotonic()
        with pytest.raises(HTTPTransportError):
            client.download_bytes(server.url("/slow"))
        elapsed = time.monotonic() - started
    assert elapsed < 0.4 + TIMING_TOLERANCE_SECONDS


def test_deadline_bounds_drip_fed_body_total_time() -> None:
    """A trickled body is bounded by total elapsed time, not socket inactivity alone."""
    chunks = (b"a" * 64, b"b" * 64, b"c" * 64, b"d" * 64)
    deadline = ExtractionDeadline.after(0.4)
    with (
        HTTPFixture({"/drip": HTTPReply(chunks=chunks, chunk_delay_seconds=0.5)}) as server,
        ExtractionHTTPClient(deadline, allowed_hosts=frozenset({"127.0.0.1", "localhost"})) as client,
    ):
        started = time.monotonic()
        with pytest.raises(HTTPTransportError):
            client.download_bytes(server.url("/drip"))
        elapsed = time.monotonic() - started
    assert elapsed < 0.4 + TIMING_TOLERANCE_SECONDS


def test_disconnect_raises_unavailable_without_retrying() -> None:
    """A closed synthetic connection reports a safe unavailable failure."""
    with (
        HTTPFixture({"/lost": HTTPReply(disconnect=True)}) as server,
        ExtractionHTTPClient(
            ExtractionDeadline.after(5), allowed_hosts=frozenset({"127.0.0.1", "localhost"})
        ) as client,
    ):
        with pytest.raises(HTTPTransportError) as error:
            client.download_bytes(server.url("/lost"))
        assert error.value.code == HTTPFailure.UNAVAILABLE
    assert len(server.requests) == 1


def test_cross_origin_redirect_strips_credentials() -> None:
    """A redirect to another origin never replays explicit credentials or cookies."""
    with (
        HTTPFixture({"/target": HTTPReply(b"synthetic")}) as target,
        HTTPFixture(
            {"/start": HTTPReply(status=HTTPStatus.FOUND, headers={"Location": target.url("/target")})}
        ) as origin,
        ExtractionHTTPClient(
            ExtractionDeadline.after(5), allowed_hosts=frozenset({"127.0.0.1", "localhost"})
        ) as client,
    ):
        headers = {"Authorization": "synthetic"}
        assert client.download_bytes(origin.url("/start"), headers, cookies={"synthetic": "1"}) == b"synthetic"
        assert "Authorization" in origin.requests[0].headers
        assert "Authorization" not in target.requests[0].headers
        assert "Cookie" not in target.requests[0].headers


def test_redirect_chain_is_bounded() -> None:
    """A redirect loop stops at the configured hop count without a false resolution."""
    with HTTPFixture({"/loop": HTTPReply(status=HTTPStatus.FOUND, headers={"Location": "/loop"})}) as server:
        with ExtractionHTTPClient(
            ExtractionDeadline.after(5), max_redirects=1, allowed_hosts=frozenset({"127.0.0.1", "localhost"})
        ) as client:
            with pytest.raises(HTTPTransportError) as error:
                client.download_bytes(server.url("/loop"))
            assert error.value.code == HTTPFailure.REJECTED
        assert len(server.requests) == 2


def test_invalid_url_is_rejected() -> None:
    """Unsupported origins and non-public destinations are rejected before opening a transport."""
    with ExtractionHTTPClient(ExtractionDeadline.after(5)) as client:
        with pytest.raises(HTTPTransportError) as error:
            client.download_bytes("file:///synthetic")
        assert error.value.code == HTTPFailure.REJECTED

        with pytest.raises(HTTPTransportError) as error:
            client.download_bytes("http://127.0.0.1/synthetic")
        assert error.value.code == HTTPFailure.REJECTED

        with pytest.raises(HTTPTransportError) as error:
            client.download_bytes("http://169.254.169.254/latest/meta-data")
        assert error.value.code == HTTPFailure.REJECTED
