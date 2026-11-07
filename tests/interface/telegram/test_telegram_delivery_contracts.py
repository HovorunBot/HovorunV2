"""Real loopback Telegram acknowledgements, partial receipts and explicit delivery recovery."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from http import HTTPStatus
from urllib.parse import parse_qs

import pytest

from hovorunv2.application.media.downloader import NativeMediaDownloader
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.application.media.rendering import MEDIA_UNAVAILABLE_TEXT
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
from hovorunv2.interface.telegram.transport.sync_delivery import (
    NativeTelegramAPI,
    NativeTelegramDelivery,
    TelegramDeliveryUncertainError,
    TelegramRejectedError,
)
from tests.utils.sync_http import HTTPFixture, HTTPReply, json_reply

# Synthetic loopback-only credentials, never an external account.
SYNTHETIC_BOT_IDENTITY = "123456:synthetic"
API_PREFIX = f"/bot{SYNTHETIC_BOT_IDENTITY}/"
RouteReplies = Mapping[str, HTTPReply | list[HTTPReply]]


def _message(message_id: int) -> dict[str, object]:
    return {"message_id": message_id, "date": 0, "chat": {"id": 42, "type": "private"}}


def _ack(result: object) -> HTTPReply:
    return json_reply({"ok": True, "result": result})


def _reject(description: str = "WEBPAGE_CURL_FAILED", status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> HTTPReply:
    return json_reply({"ok": False, "error_code": status, "description": description}, status=status)


def _payload(count: int = 2, *, long_caption: bool = False) -> RichMediaPayload:
    return RichMediaPayload(
        "Synthetic",
        "fixture",
        "https://synthetic.invalid/author",
        "x" * 1100 if long_caption else "Caption",
        original_url="https://synthetic.invalid/post",
        media_items=[MediaItem(f"https://synthetic.invalid/{index}.jpg", is_video=False) for index in range(count)],
    )


def _methods(server: HTTPFixture) -> list[str]:
    return [request.path.rsplit("/", 1)[-1] for request in server.requests]


def test_native_missing_video_sends_post_text_and_explanation() -> None:
    """A metadata-only video produces a useful text reply without downloading its thumbnail."""
    payload = _payload(0)
    payload.video_unavailable = True
    with _native_delivery({API_PREFIX + "sendMessage": _ack(_message(10))}) as (delivery, server):
        assert delivery.deliver(42, 1, payload, "Synthetic", None) == (10,)
        assert _methods(server) == ["sendMessage"]
        fields = parse_qs(server.requests[0].body.decode())
        assert "Caption" in fields["text"][0]
        assert MEDIA_UNAVAILABLE_TEXT in fields["text"][0]
        assert payload.original_url in fields["text"][0]


@contextmanager
def _native_delivery(routes: RouteReplies) -> Iterator[tuple[NativeTelegramDelivery, HTTPFixture]]:
    with HTTPFixture(routes) as server, NativeHTTPClient() as http:
        api = NativeTelegramAPI(http, SYNTHETIC_BOT_IDENTITY, api_base=server.url(""))
        yield NativeTelegramDelivery(api, NativeMediaDownloader(http)), server


def test_native_later_malformed_ack_retains_caption_and_previous_album_receipts() -> None:
    """A missing later message ID cannot erase earlier accepted effects or trigger another send."""
    accepted = tuple(range(20, 30))
    routes = {
        API_PREFIX + "sendMessage": _ack(_message(10)),
        API_PREFIX + "sendMediaGroup": _ack([_message(message_id) for message_id in accepted]),
        API_PREFIX + "sendPhoto": _ack({}),
    }
    with _native_delivery(routes) as (delivery, server):
        with pytest.raises(TelegramDeliveryUncertainError) as error:
            delivery.deliver(42, 1, _payload(11, long_caption=True), "Synthetic", "Sender")
        assert error.value.receipts == (10, *accepted)
        assert _methods(server) == ["sendMessage", "sendMediaGroup", "sendPhoto"]


@pytest.mark.parametrize("result", [[_message(21), {}], [_message(21)]])
def test_native_partial_ack_retains_known_ids(result: object) -> None:
    """Malformed entries and too few acknowledgements retain every confirmed media receipt."""
    with _native_delivery({API_PREFIX + "sendMediaGroup": _ack(result)}) as (delivery, server):
        with pytest.raises(TelegramDeliveryUncertainError) as error:
            delivery.deliver(42, 1, _payload(), "Synthetic", None)
        assert error.value.receipts == (21,)
        assert _methods(server) == ["sendMediaGroup"]


def test_native_rejection_after_accepted_album_never_restarts_with_downloads() -> None:
    """A fetch rejection in the next group cannot authorize resending the preceding accepted album."""
    accepted = tuple(range(20, 30))
    routes = {
        API_PREFIX + "sendMediaGroup": _ack([_message(message_id) for message_id in accepted]),
        API_PREFIX + "sendPhoto": _reject(),
    }
    with _native_delivery(routes) as (delivery, server):
        with pytest.raises(TelegramDeliveryUncertainError) as error:
            delivery.deliver(42, 1, _payload(11), "Synthetic", None)
        assert error.value.receipts == accepted
        assert _methods(server) == ["sendMediaGroup", "sendPhoto"]


def test_native_uncertain_transport_stops_without_download_or_second_send() -> None:
    """A real disconnected acknowledgement is never treated as an explicit fetch rejection."""
    with _native_delivery({API_PREFIX + "sendMediaGroup": HTTPReply(disconnect=True)}) as (delivery, server):
        with pytest.raises(TelegramDeliveryUncertainError) as error:
            delivery.deliver(42, 1, _payload(), "Synthetic", None)
        assert error.value.receipts == ()
        assert _methods(server) == ["sendMediaGroup"]


@pytest.mark.parametrize("uncertain_upload", [False, True])
def test_native_explicit_fetch_rejection_uploads_once_and_preserves_placeholder(uncertain_upload: bool) -> None:
    """Only known fetch rejection enables RAM fallback; uncertain upload retains its accepted placeholder."""
    upload = HTTPReply(disconnect=True) if uncertain_upload else _ack(_message(21))
    routes = {
        API_PREFIX + "sendPhoto": [_reject(), upload],
        API_PREFIX + "sendMessage": _ack(_message(10)),
        API_PREFIX + "deleteMessage": _ack(True),
        "/asset": HTTPReply(b"synthetic media"),
    }
    with _native_delivery(routes) as (delivery, server):
        payload = _payload(1)
        payload.media_items[0].url = server.url("/asset")
        if uncertain_upload:
            with pytest.raises(TelegramDeliveryUncertainError) as error:
                delivery.deliver(42, 1, payload, "Synthetic", None)
            assert error.value.receipts == (10,)
        else:
            assert delivery.deliver(42, 1, payload, "Synthetic", None) == (21,)
        methods = ["sendPhoto", "sendMessage", "asset", "sendPhoto"]
        assert _methods(server) == methods + ([] if uncertain_upload else ["deleteMessage"])
        assert b"synthetic media" in server.requests[3].body


def test_native_unrelated_url_rejection_does_not_download() -> None:
    """An explicit rejection only permits fallback for the documented fetch reasons."""
    with _native_delivery({API_PREFIX + "sendMediaGroup": _reject("synthetic rejection")}) as (delivery, server):
        with pytest.raises(TelegramRejectedError):
            delivery.deliver(42, 1, _payload(), "Synthetic", None)
        assert _methods(server) == ["sendMediaGroup"]


def test_native_success_receipts_and_single_item_wire_policy() -> None:
    """A successful text-only request returns its receipt and uses the canonical text route."""
    with _native_delivery({API_PREFIX + "sendMessage": _ack(_message(21))}) as (delivery, server):
        assert delivery.deliver(42, 1, _payload(0), "Synthetic", None) == (21,)
        assert _methods(server) == ["sendMessage"]
        assert b"Caption" in server.requests[0].body


def test_native_too_large_is_reported_without_download_retry() -> None:
    """An explicitly rejected size retains a successful notice receipt without attempting RAM fallback."""
    routes = {
        API_PREFIX + "sendMediaGroup": _reject("Request Entity Too Large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE),
        API_PREFIX + "sendMessage": _ack(_message(10)),
    }
    with _native_delivery(routes) as (delivery, server):
        assert delivery.deliver(42, 1, _payload(), "Synthetic", None) == (10,)
        assert _methods(server) == ["sendMediaGroup", "sendMessage"]


def test_native_buffered_album_returns_all_receipts_without_retry() -> None:
    """Mixed photo/video buffers preserve successful acknowledgement order and actual multipart attachment types."""
    with _native_delivery({API_PREFIX + "sendMediaGroup": _ack([_message(21), _message(22)])}) as values:
        delivery, server = values
        payload = _payload()
        payload.media_items[0].data = b"synthetic image"
        payload.media_items[1] = MediaItem("https://synthetic.invalid/video", is_video=True, data=b"synthetic video")
        assert delivery.deliver(42, 1, payload, "Synthetic", None) == (21, 22)
        assert _methods(server) == ["sendMediaGroup"]
        assert b"media_0.jpg" in server.requests[0].body
        assert b"media_1.mp4" in server.requests[0].body


def test_native_empty_download_batch_edits_placeholder_without_upload() -> None:
    """No usable download updates the accepted placeholder instead of submitting an empty album."""
    routes = {
        API_PREFIX + "sendPhoto": _reject(),
        API_PREFIX + "sendMessage": _ack(_message(10)),
        API_PREFIX + "editMessageText": _ack(_message(10)),
        "/missing": HTTPReply(status=HTTPStatus.NOT_FOUND),
    }
    with _native_delivery(routes) as (delivery, server):
        payload = _payload(1)
        payload.media_items[0].url = server.url("/missing")
        assert delivery.deliver(42, 1, payload, "Synthetic", None) == (10,)
        assert _methods(server) == ["sendPhoto", "sendMessage", "missing", "editMessageText"]
        assert "Failed to download media" in parse_qs(server.requests[-1].body.decode())["text"][0]
