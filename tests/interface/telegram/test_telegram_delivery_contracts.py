"""Real loopback Telegram acknowledgements, partial receipts and explicit delivery recovery."""

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from hovorunv2.application.media.downloader import NativeMediaDownloader
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.limits import MAX_SOURCE_MEDIA_BYTES
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.application.media.rendering import MEDIA_UNAVAILABLE_TEXT
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
from hovorunv2.interface.telegram.transport.sync_delivery import (
    NativeTelegramAPI,
    NativeTelegramDelivery,
    TelegramDeliveryUncertainError,
    TelegramRejectedError,
)
from tests.utils.split_media import media_stream_types, split_media_routes, synthetic_video
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
def _native_delivery(
    routes: RouteReplies, *, total_timeout: float = 60.0
) -> Generator[tuple[NativeTelegramDelivery, HTTPFixture]]:
    with (
        HTTPFixture(routes) as server,
        NativeHTTPClient(
            max_bytes=MAX_SOURCE_MEDIA_BYTES,
            total_timeout=total_timeout,
            allowed_hosts=frozenset({"127.0.0.1", "localhost", "synthetic.invalid"}),
        ) as http,
    ):
        api = NativeTelegramAPI(http, SYNTHETIC_BOT_IDENTITY, api_base=server.url(""))
        yield NativeTelegramDelivery(api, NativeMediaDownloader(http, destination_policy=http)), server


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
    """Single-item fetch rejection updates the placeholder in-place via editMessageMedia instead of sending twice."""
    upload = HTTPReply(disconnect=True) if uncertain_upload else _ack(_message(10))
    routes = {
        API_PREFIX + "sendPhoto": _reject(),
        API_PREFIX + "sendMessage": _ack(_message(10)),
        API_PREFIX + "editMessageMedia": upload,
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
            assert delivery.deliver(42, 1, payload, "Synthetic", None) == (10,)
        assert _methods(server) == ["sendPhoto", "sendMessage", "asset", "editMessageMedia"]
        placeholder_fields = parse_qs(server.requests[1].body.decode())
        assert placeholder_fields["reply_parameters"] == ['{"message_id": 1}']
        assert b"synthetic media" in server.requests[3].body


def test_native_album_fetch_rejection_uploads_group_and_deletes_placeholder() -> None:
    """Multi-item album fallback sends the uploaded media group and deletes the temporary placeholder."""
    routes = {
        API_PREFIX + "sendMediaGroup": [_reject(), _ack([_message(21), _message(22)])],
        API_PREFIX + "sendMessage": _ack(_message(10)),
        API_PREFIX + "deleteMessage": _ack(True),
        "/0.jpg": HTTPReply(b"image 0"),
        "/1.jpg": HTTPReply(b"image 1"),
    }
    with _native_delivery(routes) as (delivery, server):
        payload = _payload(2)
        payload.media_items[0].url = server.url("/0.jpg")
        payload.media_items[1].url = server.url("/1.jpg")
        assert delivery.deliver(42, 1, payload, "Synthetic", None) == (21, 22)
        assert _methods(server) == [
            "sendMediaGroup",
            "sendMessage",
            "0.jpg",
            "1.jpg",
            "sendMediaGroup",
            "deleteMessage",
        ]


def test_native_slow_upload_updates_placeholder_when_telegram_acknowledges() -> None:
    """Upload time before response headers arrive must not cause response read timeout on editMessageMedia."""
    slow_ack = HTTPReply(
        b'{"ok": true, "result": {"message_id": 10, "date": 0, "chat": {"id": 42, "type": "private"}}}',
        headers={"Content-Type": "application/json"},
        delay_seconds=0.08,
    )
    routes = {
        API_PREFIX + "sendVideo": _reject(),
        API_PREFIX + "sendMessage": _ack(_message(10)),
        API_PREFIX + "editMessageMedia": slow_ack,
        "/video.mp4": HTTPReply(b"synthetic video"),
    }
    with _native_delivery(routes, total_timeout=0.04) as (delivery, server):
        payload = _payload(1)
        payload.media_items[0] = MediaItem(server.url("/video.mp4"), is_video=True)
        assert delivery.deliver(42, 1, payload, "YouTube", None) == (10,)
        assert _methods(server) == ["sendVideo", "sendMessage", "video.mp4", "editMessageMedia"]


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


def test_native_oversized_post_text_is_split_into_multiple_bounded_messages() -> None:
    """Post text longer than 4096 characters is sent across multiple messages within Telegram bounds."""
    long_text = ("<b>Paragraph</b> " + "word " * 600 + "\n\n") * 3
    assert len(long_text) > 8000
    payload = RichMediaPayload(
        "Author",
        "handle",
        "https://synthetic.invalid/author",
        long_text,
        original_url="https://synthetic.invalid/post",
        media_items=[MediaItem("https://synthetic.invalid/photo.jpg", is_video=False)],
    )
    routes = {
        API_PREFIX + "sendMessage": [_ack(_message(101)), _ack(_message(102)), _ack(_message(103))],
        API_PREFIX + "sendPhoto": _ack(_message(104)),
    }
    with _native_delivery(routes) as (delivery, server):
        receipts = delivery.deliver(42, 1, payload, "Synthetic", "Sender")
        assert len(receipts) >= 3
        # Ensure all sendMessage requests sent text <= 4096 chars
        send_message_requests = [req for req in server.requests if req.path.endswith("/sendMessage")]
        assert len(send_message_requests) >= 2
        for req in send_message_requests:
            sent_text = parse_qs(req.body.decode())["text"][0]
            assert len(sent_text) <= 4096


def test_split_stream_video_upload_contains_audio(tmp_path: Path) -> None:
    """Merge independent streams before submitting a Telegram video attachment."""
    routes = split_media_routes(tmp_path)
    routes.update(
        {
            API_PREFIX + "sendVideo": _ack(_message(21)),
            API_PREFIX + "sendMessage": _ack(_message(10)),
            API_PREFIX + "editMessageMedia": _ack(_message(10)),
        }
    )
    with _native_delivery(routes) as (delivery, server):
        payload = NativeMediaExtractor().extract(server.url("/manifest.mpd"))
        delivery.deliver(42, 1, payload, "YouTube", None)
        uploads = [request for request in server.requests if request.path.startswith(API_PREFIX)]
        attachments = []
        for request in uploads:
            message = BytesParser(policy=default).parsebytes(
                f"Content-Type: {request.headers['Content-Type']}\r\n\r\n".encode() + request.body
            )
            attachments.extend(
                part.get_payload(decode=True) for part in message.iter_attachments() if part.get_filename()
            )
        assert len(attachments) == 1
        assert isinstance(attachments[0], bytes)
        output = tmp_path / "uploaded.mp4"
        output.write_bytes(attachments[0])
        assert media_stream_types(output) == {"audio", "video"}


@pytest.mark.parametrize("buffered", [False, True])
def test_oversized_provider_video_is_compressed_for_upload(tmp_path: Path, *, buffered: bool) -> None:
    """Any provider can recover from oversized direct or browser-buffered video."""
    source = synthetic_video(tmp_path)
    oversized = source + bytes(50 * 1024 * 1024)
    routes = {
        API_PREFIX + "sendVideo": _reject("Request Entity Too Large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE),
        API_PREFIX + "sendMessage": _ack(_message(10)),
        API_PREFIX + "editMessageMedia": _ack(_message(10)),
        API_PREFIX + "editMessageText": _ack(_message(10)),
        "/video.mp4": HTTPReply(oversized, headers={"Content-Type": "video/mp4"}),
    }
    with _native_delivery(routes) as (delivery, server):
        payload = _payload(1)
        payload.media_items[0] = MediaItem(server.url("/video.mp4"), True, oversized if buffered else None)
        delivery.deliver(42, 1, payload, "Any provider", None)
        uploads = [request for request in server.requests if request.path.endswith("editMessageMedia")]
        assert len(uploads) == 1
        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {uploads[0].headers['Content-Type']}\r\n\r\n".encode() + uploads[0].body
        )
        attachment = next(part for part in message.iter_attachments() if part.get_filename())
        content = attachment.get_payload(decode=True)
        assert isinstance(content, bytes)
        assert len(content) < 50_000_000
        output = tmp_path / "compressed.mp4"
        output.write_bytes(content)
        assert media_stream_types(output) == {"video", "audio"}
        if not buffered:
            send_message_req = next(r for r in server.requests if r.path.endswith("sendMessage"))
            assert "Downloading media..." in parse_qs(send_message_req.body.decode())["text"][0]
            edit_text_req = next(r for r in server.requests if r.path.endswith("editMessageText"))
            assert "compressed" in parse_qs(edit_text_req.body.decode())["text"][0]


def test_normal_video_does_not_emit_compression_notice(tmp_path: Path) -> None:
    """Normal-sized videos show downloading progress and do not notify about compression."""
    source = synthetic_video(tmp_path)
    routes = {
        API_PREFIX + "sendVideo": _reject("Request Entity Too Large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE),
        API_PREFIX + "sendMessage": _ack(_message(10)),
        API_PREFIX + "editMessageMedia": _ack(_message(10)),
        "/video.mp4": HTTPReply(source, headers={"Content-Type": "video/mp4"}),
    }
    with _native_delivery(routes) as (delivery, server):
        payload = _payload(1)
        payload.media_items[0] = MediaItem(server.url("/video.mp4"), is_video=True)
        delivery.deliver(42, 1, payload, "Any provider", None)
        assert _methods(server) == ["sendVideo", "sendMessage", "video.mp4", "editMessageMedia"]
        send_message_req = next(r for r in server.requests if r.path.endswith("sendMessage"))
        text = parse_qs(send_message_req.body.decode())["text"][0]
        assert "Downloading media..." in text
        assert "compressed" not in text.lower()
        assert "editMessageText" not in _methods(server)


def test_failed_split_audio_download_never_uploads_silent_video(tmp_path: Path) -> None:
    """An unavailable audio track must fail the merge rather than degrade to video-only delivery."""
    routes = split_media_routes(tmp_path)
    for path in routes:
        if "stream1" in path:
            routes[path] = HTTPReply(status=HTTPStatus.NOT_FOUND)
    routes.update(
        {
            API_PREFIX + "sendVideo": _ack(_message(21)),
            API_PREFIX + "sendMessage": _ack(_message(10)),
            API_PREFIX + "editMessageText": _ack(_message(10)),
            API_PREFIX + "editMessageMedia": _ack(_message(10)),
        }
    )
    with _native_delivery(routes) as (delivery, server):
        payload = NativeMediaExtractor().extract(server.url("/manifest.mpd"))
        delivery.deliver(42, 1, payload, "YouTube", None)
        assert "sendVideo" not in _methods(server)
        assert "editMessageMedia" not in _methods(server)
        assert "Failed to download media" in parse_qs(server.requests[-1].body.decode())["text"][0]


def test_deliver_preserves_message_thread_id_on_text_split_and_oversized_fallback() -> None:
    """Forum topic message_thread_id is forwarded to text-only, split-caption, and oversized-media fallback calls."""
    # 1. Text-only post
    with _native_delivery({API_PREFIX + "sendMessage": _ack(_message(10))}) as (delivery, server):
        text_payload = RichMediaPayload(
            "Synthetic",
            "fixture",
            "https://synthetic.invalid/author",
            "Hello forum topic",
            original_url="https://synthetic.invalid/post",
            media_items=[],
        )
        assert delivery.deliver(42, 1, text_payload, "Synthetic", None, message_thread_id=777) == (10,)
        body = parse_qs(server.requests[0].body.decode())
        assert body["message_thread_id"] == ["777"]

    # 2. Split-caption post (separate_text)
    routes = {
        API_PREFIX + "sendMessage": _ack(_message(10)),
        API_PREFIX + "sendPhoto": _ack(_message(11)),
    }
    with _native_delivery(routes) as (delivery, server):
        receipts = delivery.deliver(42, 1, _payload(1, long_caption=True), "Synthetic", "Sender", message_thread_id=777)
        assert receipts == (10, 11)
        send_msg = next(r for r in server.requests if r.path.endswith("sendMessage"))
        assert parse_qs(send_msg.body.decode())["message_thread_id"] == ["777"]

    # 3. Oversized media fallback (TOO_LARGE)
    oversized_routes = {
        API_PREFIX + "sendPhoto": _reject("Request Entity Too Large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE),
        API_PREFIX + "sendMessage": _ack(_message(12)),
    }
    with _native_delivery(oversized_routes) as (delivery, server):
        assert delivery.deliver(42, 1, _payload(1), "Synthetic", None, message_thread_id=777) == (12,)
        send_msg = next(r for r in server.requests if r.path.endswith("sendMessage"))
        assert parse_qs(send_msg.body.decode())["message_thread_id"] == ["777"]


def test_album_upload_fallback_does_not_send_duplicate_globe_markup() -> None:
    """Do not send a duplicate globe markup message when _upload_fallback already attached reply_markup."""
    markup = '{"inline_keyboard":[[{"text":"Translate","callback_data":"tr:s:abc"}]]}'
    # Empty downloads on multi-item album
    routes_empty = {
        API_PREFIX + "sendMediaGroup": _reject(),
        API_PREFIX + "sendMessage": _ack(_message(10)),
        API_PREFIX + "editMessageText": _ack(_message(10)),
        "/0.jpg": HTTPReply(status=HTTPStatus.NOT_FOUND),
        "/1.jpg": HTTPReply(status=HTTPStatus.NOT_FOUND),
    }
    with _native_delivery(routes_empty) as (delivery, server):
        album = _payload(2)
        album.media_items = [
            MediaItem(server.url("/0.jpg"), is_video=False),
            MediaItem(server.url("/1.jpg"), is_video=False),
        ]
        receipts = delivery.deliver(42, 1, album, "Synthetic", None, reply_markup=markup)
        assert receipts == (10,)
        send_messages = [r for r in server.requests if r.path.endswith("sendMessage")]
        assert len(send_messages) == 1
        edit_req = next(r for r in server.requests if r.path.endswith("editMessageText"))
        assert parse_qs(edit_req.body.decode())["reply_markup"] == [markup]

    # Single downloaded item from 2-item album edited onto placeholder
    routes_one = {
        API_PREFIX + "sendMediaGroup": _reject(),
        API_PREFIX + "sendMessage": _ack(_message(10)),
        API_PREFIX + "editMessageMedia": _ack(_message(10)),
        "/0.jpg": HTTPReply(b"jpeg-bytes", headers={"Content-Type": "image/jpeg"}),
        "/1.jpg": HTTPReply(status=HTTPStatus.NOT_FOUND),
    }
    with _native_delivery(routes_one) as (delivery, server):
        album = _payload(2)
        album.media_items = [
            MediaItem(server.url("/0.jpg"), is_video=False),
            MediaItem(server.url("/1.jpg"), is_video=False),
        ]
        receipts = delivery.deliver(42, 1, album, "Synthetic", None, reply_markup=markup)
        assert receipts == (10,)
        send_messages = [r for r in server.requests if r.path.endswith("sendMessage")]
        assert len(send_messages) == 1
        assert _methods(server) == ["sendMediaGroup", "sendMessage", "0.jpg", "1.jpg", "editMessageMedia"]


def test_native_file_backed_video_and_document_stream_and_cleanup_tempdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File-backed MediaItem and send_document stream from Path and clean up temporary delivery dirs."""
    video_file = tmp_path / "local.mp4"
    video_file.write_bytes(b"file-backed-video-bytes")
    doc_file = tmp_path / "transcript.txt"
    doc_file.write_bytes(b"file-backed-doc-bytes")

    # 1. Direct file-backed MediaItem streams via sendVideo
    with _native_delivery({API_PREFIX + "sendVideo": _ack(_message(31))}) as (delivery, server):
        payload = _payload(0)
        payload.media_items = [MediaItem("https://synthetic.invalid/v.mp4", is_video=True, file_path=video_file)]
        assert delivery.deliver(42, 1, payload, "Synthetic", None) == (31,)
        assert b"file-backed-video-bytes" in server.requests[0].body

    # 2. Fallback delivery cleans up TemporaryDirectory even when upload fails
    temp_root = tmp_path / "isolated_tmp"
    temp_root.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(temp_root))
    routes_fail = {
        API_PREFIX + "sendVideo": _reject(),
        API_PREFIX + "sendMessage": _ack(_message(10)),
        API_PREFIX + "editMessageMedia": HTTPReply(disconnect=True),
        "/v.mp4": HTTPReply(b"streamed-video"),
    }
    with _native_delivery(routes_fail) as (delivery, server):
        payload = _payload(0)
        payload.media_items = [MediaItem(server.url("/v.mp4"), is_video=True)]
        with pytest.raises(TelegramDeliveryUncertainError):
            delivery.deliver(42, 1, payload, "Synthetic", None)
    assert list(temp_root.iterdir()) == []

    # 3. NativeTelegramAPI.send_document streams from Path
    with (
        HTTPFixture({API_PREFIX + "sendDocument": _ack(_message(55))}) as server,
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1", "localhost"})) as http,
    ):
        api = NativeTelegramAPI(http, SYNTHETIC_BOT_IDENTITY, api_base=server.url(""))
        assert api.send_document(42, "transcript.txt", doc_file) == 55
        assert b"file-backed-doc-bytes" in server.requests[0].body
