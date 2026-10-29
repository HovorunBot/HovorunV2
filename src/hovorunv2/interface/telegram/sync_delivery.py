"""Narrow synchronous Telegram media delivery and operations for native workers."""

import json
from contextlib import suppress
from enum import StrEnum
from http import HTTPStatus

from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.media.downloader import NativeMediaDownloader
from hovorunv2.application.media.rendering import build_caption, plan_caption
from hovorunv2.application.worker_io import HTTPFiles, HTTPForm, HTTPTransportError, SyncHTTPPort

MAX_MEDIA_GROUP = 10


class TelegramMethod(StrEnum):
    """The intentionally narrow supported Bot API surface."""

    SEND_MESSAGE = "sendMessage"
    SEND_PHOTO = "sendPhoto"
    SEND_VIDEO = "sendVideo"
    SEND_DOCUMENT = "sendDocument"
    SEND_MEDIA_GROUP = "sendMediaGroup"
    EDIT_MESSAGE_TEXT = "editMessageText"
    DELETE_MESSAGE = "deleteMessage"
    GET_CHAT_MEMBER = "getChatMember"
    GET_CHAT_ADMINISTRATORS = "getChatAdministrators"


class TelegramRejection(StrEnum):
    """Safe explicit remote rejection classes, never the original response description."""

    FETCH = "telegram_fetch_rejected"
    TOO_LARGE = "telegram_too_large"
    OTHER = "telegram_rejected"


class TelegramRejectedError(ValueError):
    """The API explicitly rejected this request without accepting its delivery."""

    def __init__(self, reason: TelegramRejection) -> None:
        self.reason = reason
        super().__init__(reason.value)


class TelegramDeliveryUncertainError(OSError):
    """Missing or malformed acknowledgement cannot authorize an automatic duplicate delivery."""

    def __init__(self, receipts: tuple[int, ...] = ()) -> None:
        self.receipts = receipts
        super().__init__("telegram_delivery_uncertain")


class NativeTelegramAPI:
    """Call only explicitly supported operations with safe technical errors and no automatic retry."""

    def __init__(self, http: SyncHTTPPort, token: str, *, api_base: str = "https://api.telegram.org") -> None:
        if not token:
            msg = "Telegram transport requires configured credentials."
            raise ValueError(msg)
        self._http = http
        self._base = api_base.rstrip("/") + "/bot" + token + "/"

    def call(
        self, method: TelegramMethod | str, data: HTTPForm | None = None, files: HTTPFiles | None = None
    ) -> object:
        """Distinguish a definite Telegram rejection from an uncertain mutating request."""
        operation = TelegramMethod(method)
        try:
            response = self._http.request_json("POST", self._base + operation.value, data=data, files=files)
        except HTTPTransportError:
            raise TelegramDeliveryUncertainError from None
        if not isinstance(response, dict) or type(response.get("ok")) is not bool:
            raise TelegramDeliveryUncertainError
        if response["ok"] is True:
            if "result" not in response:
                raise TelegramDeliveryUncertainError
            return response["result"]
        code, description = response.get("error_code"), response.get("description")
        if not isinstance(code, int) or not isinstance(description, str):
            raise TelegramDeliveryUncertainError
        reason = TelegramRejection.OTHER
        upper = description.upper()
        if code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE or any(
            text in upper for text in ("FILE IS TOO BIG", "REQUEST ENTITY TOO LARGE")
        ):
            reason = TelegramRejection.TOO_LARGE
        elif code == HTTPStatus.BAD_REQUEST and any(text in upper for text in ("WEBPAGE_CURL_FAILED", "FAILED TO GET")):
            reason = TelegramRejection.FETCH
        raise TelegramRejectedError(reason)


def _message_ids(result: object) -> tuple[int, ...]:
    values = result if isinstance(result, list) else [result]
    ids = []
    for value in values:
        if not isinstance(value, dict):
            raise TelegramDeliveryUncertainError
        message_id = value.get("message_id")
        if type(message_id) is not int or message_id <= 0:
            raise TelegramDeliveryUncertainError
        ids.append(message_id)
    if not ids:
        raise TelegramDeliveryUncertainError
    return tuple(ids)


class NativeTelegramDelivery:
    """Deliver using canonical route facts and the transient decrypted sender label."""

    def __init__(self, api: NativeTelegramAPI, downloader: NativeMediaDownloader) -> None:
        self._api = api
        self._downloader = downloader

    def _text(self, chat_id: int, text: str, *, message_id: int | None = None) -> tuple[int, ...]:
        fields: dict[str, str | int | float | bool] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": json.dumps({"is_disabled": True}),
        }
        method = TelegramMethod.SEND_MESSAGE
        if message_id is not None:
            fields["message_id"] = message_id
            method = TelegramMethod.EDIT_MESSAGE_TEXT
        return _message_ids(self._api.call(method, fields))

    def _send_items(self, chat_id: int, message_id: int, items: list[MediaItem], caption: str) -> tuple[int, ...]:
        receipts = []
        for start in range(0, len(items), MAX_MEDIA_GROUP):
            group = items[start : start + MAX_MEDIA_GROUP]
            files: dict[str, tuple[str, bytes, str]] = {}
            media: list[dict[str, str]] = []
            for index, item in enumerate(group):
                source = item.url
                if item.data is not None:
                    name = f"media{index}"
                    extension, mime = ("mp4", "video/mp4") if item.is_video else ("jpg", "image/jpeg")
                    files[name] = (f"media_{start + index}.{extension}", item.data, mime)
                    source = "attach://" + name
                value = {"type": "video" if item.is_video else "photo", "media": source}
                if start == 0 and index == 0:
                    value.update({"caption": caption, "parse_mode": "HTML"})
                media.append(value)
            fields: dict[str, str | int | float | bool] = {
                "chat_id": chat_id,
                "reply_parameters": json.dumps({"message_id": message_id}),
            }
            if len(group) == 1:
                entry = media[0]
                fields[entry["type"]] = entry["media"]
                if "caption" in entry:
                    fields.update({"caption": entry["caption"], "parse_mode": "HTML"})
                method = TelegramMethod.SEND_VIDEO if group[0].is_video else TelegramMethod.SEND_PHOTO
            else:
                fields["media"] = json.dumps(media)
                method = TelegramMethod.SEND_MEDIA_GROUP
            try:
                result = self._api.call(method, fields, files or None)
            except TelegramRejectedError:
                if receipts:
                    raise TelegramDeliveryUncertainError(tuple(receipts)) from None
                raise
            except TelegramDeliveryUncertainError:
                raise TelegramDeliveryUncertainError(tuple(receipts)) from None
            receipts.extend(_message_ids(result))
        return tuple(receipts)

    def _fallback(self, chat_id: int, message_id: int, payload: RichMediaPayload, caption: str) -> tuple[int, ...]:
        placeholder = self._text(chat_id, caption + "\n\n⏳ <i>Downloading media...</i>")[0]
        contents = self._downloader.download_batch(payload.media_items)
        items = [
            MediaItem(item.url, item.is_video, data)
            for item, data in zip(payload.media_items, contents, strict=True)
            if isinstance(data, bytes)
        ]
        if not items:
            return self._text(chat_id, caption + "\n\n⚠️ <i>Failed to download media.</i>", message_id=placeholder)
        try:
            receipts = self._send_items(chat_id, message_id, items, caption)
        except TelegramRejectedError as exc:
            text = (
                "Media is too large, view on original resource."
                if exc.reason == TelegramRejection.TOO_LARGE
                else ("Failed to upload media.")
            )
            return self._text(chat_id, caption + f"\n\n⚠️ <i>{text}</i>", message_id=placeholder)
        with suppress(TelegramRejectedError, TelegramDeliveryUncertainError):
            self._api.call(TelegramMethod.DELETE_MESSAGE, {"chat_id": chat_id, "message_id": placeholder})
        return receipts

    def deliver(
        self, chat_id: int, message_id: int, payload: RichMediaPayload, platform_name: str, sender_label: str | None
    ) -> tuple[int, ...]:
        """Return minimal message receipts; uncertain acceptance always stops the delivery sequence."""
        if chat_id == 0 or message_id <= 0:
            msg = "Delivery requires canonical chat and source message identities."
            raise ValueError(msg)
        total_text = build_caption(payload, sender_label, platform_name)
        if not payload.media_items:
            return self._text(chat_id, total_text)
        plan = plan_caption(payload, sender_label, platform_name, total_text)
        preceding = self._text(chat_id, plan.separate_text) if plan.separate_text is not None else ()
        try:
            return preceding + self._send_items(chat_id, message_id, payload.media_items, plan.caption)
        except TelegramRejectedError as exc:
            if exc.reason == TelegramRejection.FETCH:
                return preceding + self._fallback(chat_id, message_id, payload, plan.caption)
            if exc.reason == TelegramRejection.TOO_LARGE:
                return preceding + self._text(
                    chat_id, plan.caption + "\n\n⚠️ <i>Media is too large, view on original resource.</i>"
                )
            raise
