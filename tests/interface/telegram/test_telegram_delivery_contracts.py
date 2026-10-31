"""Real loopback Telegram acknowledgements, partial receipts and explicit delivery recovery."""

from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from http import HTTPStatus
from urllib.parse import parse_qs

import aiohttp
import pytest
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.types import Message

from hovorunv2.application.media.downloader import MediaDownloader, NativeMediaDownloader
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
from hovorunv2.interface.telegram.delivery import TelegramMediaDelivery
from hovorunv2.interface.telegram.sync_delivery import (
    NativeTelegramAPI,
    NativeTelegramDelivery,
    TelegramDeliveryUncertainError,
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


@contextmanager
def _native_delivery(routes: RouteReplies) -> Iterator[tuple[NativeTelegramDelivery, HTTPFixture]]:
    with HTTPFixture(routes) as server, NativeHTTPClient() as http:
        api = NativeTelegramAPI(http, SYNTHETIC_BOT_IDENTITY, api_base=server.url(""))
        yield NativeTelegramDelivery(api, NativeMediaDownloader(http)), server


@asynccontextmanager
async def _async_delivery(
    routes: RouteReplies,
) -> AsyncIterator[tuple[TelegramMediaDelivery, Bot, Message, HTTPFixture]]:
    with HTTPFixture(routes) as server:
        session = AiohttpSession(api=TelegramAPIServer.from_base(server.url("")))
        async with Bot(SYNTHETIC_BOT_IDENTITY, session=session) as bot, aiohttp.ClientSession() as http:
            message = Message.model_validate(
                {**_message(1), "from": {"id": 7, "is_bot": False, "first_name": "Synthetic"}},
                context={"bot": bot},
            )
            yield TelegramMediaDelivery(MediaDownloader(http), http), bot, message, server


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


@pytest.mark.parametrize("predownloaded", [False, True])
async def test_async_uncertain_send_propagates_without_fallback_or_error_message(predownloaded: bool) -> None:
    """Both URL and buffered uncertain acknowledgements stop at the actual send boundary."""
    async with _async_delivery({API_PREFIX + "sendMediaGroup": HTTPReply(disconnect=True)}) as values:
        delivery, bot, message, server = values
        payload = _payload()
        if predownloaded:
            payload.media_items[0].data = b"synthetic media"
        with pytest.raises(TelegramNetworkError):
            await delivery.deliver(bot, message, payload, "Synthetic")
        assert _methods(server) == ["sendMediaGroup"]


async def test_async_explicit_rejection_uses_partial_downloads_and_ignores_cleanup_failure() -> None:
    """The first surviving upload receives the caption; a failed placeholder deletion cannot turn it into failure."""
    routes = {
        API_PREFIX + "sendMediaGroup": [_reject(), _ack([_message(21)])],
        API_PREFIX + "sendMessage": _ack(_message(10)),
        API_PREFIX + "deleteMessage": HTTPReply(disconnect=True),
        "/missing": HTTPReply(status=HTTPStatus.NOT_FOUND),
        "/asset": HTTPReply(b"synthetic media"),
    }
    async with _async_delivery(routes) as (delivery, bot, message, server):
        payload = _payload()
        payload.media_items[0].url = server.url("/missing")
        payload.media_items[1].url = server.url("/asset")
        await delivery.deliver(bot, message, payload, "Synthetic")
        methods = _methods(server)
        assert methods[:2] == ["sendMediaGroup", "sendMessage"]
        assert sorted(methods[2:4]) == ["asset", "missing"]
        assert methods[4:] == ["sendMediaGroup", "deleteMessage"]
        assert b"synthetic media" in server.requests[4].body
        assert b"Caption" in server.requests[4].body
        assert "editMessageText" not in methods


@pytest.mark.parametrize("predownloaded", [False, True])
async def test_async_explicit_upload_rejection_keeps_safe_user_error(predownloaded: bool) -> None:
    """A known rejected upload keeps the existing user-facing error without logging remote payload details."""
    routes = {
        API_PREFIX + "sendMediaGroup": [_reject(), _reject("synthetic rejection")],
        API_PREFIX + "sendMessage": _ack(_message(10)),
        API_PREFIX + "editMessageText": _ack(_message(10)),
        "/asset": HTTPReply(b"synthetic media"),
    }
    async with _async_delivery(routes) as (delivery, bot, message, server):
        payload = _payload(1)
        payload.media_items[0].url = server.url("/asset")
        if predownloaded:
            payload.media_items[0].data = b"synthetic media"
        await delivery.deliver(bot, message, payload, "Synthetic")
        error_request = server.requests[-1]
        assert "Failed to upload media" in parse_qs(error_request.body.decode())["text"][0]
        assert "deleteMessage" not in _methods(server)


async def test_async_unrelated_url_rejection_does_not_download() -> None:
    """An explicit rejection only permits fallback for the documented fetch reasons."""
    async with _async_delivery({API_PREFIX + "sendMediaGroup": _reject("synthetic rejection")}) as values:
        delivery, bot, message, server = values
        with pytest.raises(TelegramBadRequest):
            await delivery.deliver(bot, message, _payload(), "Synthetic")
        assert _methods(server) == ["sendMediaGroup"]


def test_native_success_receipts_and_single_item_wire_policy() -> None:
    """A successful text-only request returns its receipt and uses the canonical text route."""
    with _native_delivery({API_PREFIX + "sendMessage": _ack(_message(21))}) as (delivery, server):
        assert delivery.deliver(42, 1, _payload(0), "Synthetic", None) == (21,)
        assert _methods(server) == ["sendMessage"]
        assert b"Caption" in server.requests[0].body


@pytest.mark.parametrize("predownloaded", [False, True])
async def test_async_too_large_is_reported_without_download_retry(predownloaded: bool) -> None:
    """Explicit size rejection preserves the original size notice for URL and buffered media."""
    routes = {
        API_PREFIX + "sendMediaGroup": _reject("Request Entity Too Large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE),
        API_PREFIX + "sendMessage": _ack(_message(10)),
    }
    async with _async_delivery(routes) as (delivery, bot, message, server):
        payload = _payload()
        if predownloaded:
            payload.media_items[0].data = b"synthetic media"
        await delivery.deliver(bot, message, payload, "Synthetic")
        assert _methods(server) == ["sendMediaGroup", "sendMessage"]
        assert "Media is too large" in parse_qs(server.requests[-1].body.decode())["text"][0]


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


async def test_async_empty_download_batch_edits_placeholder_without_upload() -> None:
    """No usable download updates the accepted placeholder instead of submitting an empty album."""
    routes = {
        API_PREFIX + "sendMediaGroup": _reject(),
        API_PREFIX + "sendMessage": _ack(_message(10)),
        API_PREFIX + "editMessageText": _ack(_message(10)),
        "/missing": HTTPReply(status=HTTPStatus.NOT_FOUND),
    }
    async with _async_delivery(routes) as (delivery, bot, message, server):
        payload = _payload(1)
        payload.media_items[0].url = server.url("/missing")
        await delivery.deliver(bot, message, payload, "Synthetic")
        assert _methods(server) == ["sendMediaGroup", "sendMessage", "missing", "editMessageText"]
        assert "Failed to download media" in parse_qs(server.requests[-1].body.decode())["text"][0]
