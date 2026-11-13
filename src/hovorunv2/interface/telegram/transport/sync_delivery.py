"""Narrow synchronous Telegram media delivery and operations for native workers."""

import functools
import json
import logging
from contextlib import suppress

from hovorunv2.application.jobs.http_ports import (
    HTTPFiles,
    HTTPForm,
    TelegramDeliveryUncertainError,
    TelegramRejectedError,
    TelegramRejection,
)
from hovorunv2.application.media.downloader import NativeMediaDownloader
from hovorunv2.application.media.errors import MediaFailure
from hovorunv2.application.media.limits import TELEGRAM_VIDEO_BYTES
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.application.media.rendering import build_caption, plan_caption, split_html_message
from hovorunv2.interface.telegram.transport.sync_api import (
    NativeTelegramAPI,
    TelegramMethod,
    extract_message_ids,
)

MAX_MEDIA_GROUP = 10
logger = logging.getLogger(__name__)


def _media_group(items: list[MediaItem], start: int, caption: str) -> tuple[list[dict[str, str]], HTTPFiles]:
    files: dict[str, tuple[str, bytes, str]] = {}
    media: list[dict[str, str]] = []
    for index, item in enumerate(items):
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
    return media, files


def _group_request(
    chat_id: int, message_id: int, media: list[dict[str, str]], *, reply_markup: str | None = None
) -> tuple[TelegramMethod, HTTPForm]:
    fields: dict[str, str | int | float | bool] = {
        "chat_id": chat_id,
        "reply_parameters": json.dumps({"message_id": message_id}),
    }
    if len(media) != 1:
        fields["media"] = json.dumps(media)
        return TelegramMethod.SEND_MEDIA_GROUP, fields
    entry = media[0]
    fields[entry["type"]] = entry["media"]
    if "caption" in entry:
        fields.update({"caption": entry["caption"], "parse_mode": "HTML"})
    if reply_markup is not None:
        fields["reply_markup"] = reply_markup
    method = TelegramMethod.SEND_VIDEO if entry["type"] == "video" else TelegramMethod.SEND_PHOTO
    return method, fields


class NativeTelegramDelivery:
    """Deliver using canonical route facts and the transient decrypted sender label."""

    def __init__(self, api: NativeTelegramAPI, downloader: NativeMediaDownloader) -> None:
        self._api = api
        self._downloader = downloader

    def _text(
        self,
        chat_id: int,
        text: str,
        *,
        message_id: int | None = None,
        reply_to_message_id: int | None = None,
        reply_markup: str | None = None,
    ) -> tuple[int, ...]:
        fields: dict[str, str | int | float | bool] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": json.dumps({"is_disabled": True}),
        }
        if reply_markup is not None:
            fields["reply_markup"] = reply_markup
        method = TelegramMethod.SEND_MESSAGE
        if message_id is not None:
            fields["message_id"] = message_id
            method = TelegramMethod.EDIT_MESSAGE_TEXT
        elif reply_to_message_id is not None:
            fields["reply_parameters"] = json.dumps({"message_id": reply_to_message_id})
        return extract_message_ids(self._api.call(method, fields))

    def _edit_media(
        self,
        chat_id: int,
        placeholder: int,
        item: MediaItem,
        caption: str,
        *,
        reply_markup: str | None = None,
    ) -> tuple[int, ...]:
        logger.info("Worker updating placeholder with uploaded Telegram media item")
        media, files = _media_group([item], 0, caption)
        fields: dict[str, str | int | float | bool] = {
            "chat_id": chat_id,
            "message_id": placeholder,
            "media": json.dumps(media[0]),
        }
        if reply_markup is not None:
            fields["reply_markup"] = reply_markup
        return extract_message_ids(self._api.call(TelegramMethod.EDIT_MESSAGE_MEDIA, fields, files or None))

    def _send_items(
        self,
        chat_id: int,
        message_id: int,
        items: list[MediaItem],
        caption: str,
        *,
        reply_markup: str | None = None,
    ) -> tuple[int, ...]:
        receipts = []
        for start in range(0, len(items), MAX_MEDIA_GROUP):
            group = items[start : start + MAX_MEDIA_GROUP]
            try:
                markup = reply_markup if start == 0 and len(items) == 1 else None
                receipts.extend(self._send_group(chat_id, message_id, group, start, caption, reply_markup=markup))
            except TelegramRejectedError:
                if receipts:
                    raise TelegramDeliveryUncertainError(tuple(receipts)) from None
                raise
            except TelegramDeliveryUncertainError as error:
                raise TelegramDeliveryUncertainError((*receipts, *error.receipts)) from None
        return tuple(receipts)

    def _send_group(
        self,
        chat_id: int,
        message_id: int,
        items: list[MediaItem],
        start: int,
        caption: str,
        *,
        reply_markup: str | None = None,
    ) -> tuple[int, ...]:
        logger.info("Worker sending %s Telegram media items", len(items))
        media, files = _media_group(items, start, caption)
        method, fields = _group_request(chat_id, message_id, media, reply_markup=reply_markup)
        receipts = extract_message_ids(self._api.call(method, fields, files or None))
        logger.info("Telegram acknowledged %s media messages", len(receipts))
        if len(receipts) != len(items):
            raise TelegramDeliveryUncertainError(receipts)
        return receipts

    def _notify_compression(self, chat_id: int, caption: str, placeholder: int) -> None:
        """Update placeholder to notify requester that video compression is underway.

        Args:
            chat_id: Canonical Telegram chat ID.
            caption: Formatted post caption.
            placeholder: Message ID of the temporary placeholder.
        """
        with suppress(TelegramRejectedError, TelegramDeliveryUncertainError):
            self._text(
                chat_id,
                caption + "\n\n⏳ <i>Preparing video; oversized files will be compressed to fit Telegram...</i>",
                message_id=placeholder,
            )

    def _fallback(
        self,
        chat_id: int,
        message_id: int,
        payload: RichMediaPayload,
        caption: str,
        *,
        reply_markup: str | None = None,
    ) -> tuple[int, ...]:
        logger.info("Telegram could not fetch media URLs; starting download and upload fallback")
        oversized = any(
            item.is_video and item.data is not None and len(item.data) > TELEGRAM_VIDEO_BYTES
            for item in payload.media_items
        )
        progress = (
            "Preparing video; oversized files will be compressed to fit Telegram..."
            if oversized
            else "Downloading media..."
        )
        placeholder = self._text(chat_id, caption + f"\n\n⏳ <i>{progress}</i>", reply_to_message_id=message_id)[0]
        try:
            return self._upload_fallback(chat_id, message_id, payload, caption, placeholder, reply_markup=reply_markup)
        except TelegramDeliveryUncertainError as error:
            raise TelegramDeliveryUncertainError((placeholder, *error.receipts)) from None
        except TelegramRejectedError:
            raise TelegramDeliveryUncertainError((placeholder,)) from None

    def _upload_fallback(
        self,
        chat_id: int,
        message_id: int,
        payload: RichMediaPayload,
        caption: str,
        placeholder: int,
        *,
        reply_markup: str | None = None,
    ) -> tuple[int, ...]:
        contents = self._downloader.download_batch(
            payload.media_items,
            on_compression=functools.partial(self._notify_compression, chat_id, caption, placeholder),
        )
        items = [
            MediaItem(item.url, item.is_video, data)
            for item, data in zip(payload.media_items, contents, strict=True)
            if isinstance(data, bytes)
        ]
        if not items:
            logger.error("Worker media download fallback produced no usable media")
            reason = (
                "Media is too large, view on original resource."
                if MediaFailure.TOO_LARGE in contents
                else "Failed to download media."
            )
            return self._text(
                chat_id,
                caption + f"\n\n⚠️ <i>{reason}</i>",
                message_id=placeholder,
                reply_markup=reply_markup,
            )
        try:
            if len(items) == 1:
                return self._edit_media(chat_id, placeholder, items[0], caption, reply_markup=reply_markup)
            receipts = self._send_items(chat_id, message_id, items, caption)
        except TelegramRejectedError as exc:
            logger.exception("Telegram rejected worker media upload (%s)", exc.reason)
            text = (
                "Media is too large, view on original resource."
                if exc.reason == TelegramRejection.TOO_LARGE
                else ("Failed to upload media.")
            )
            return self._text(
                chat_id,
                caption + f"\n\n⚠️ <i>{text}</i>",
                message_id=placeholder,
                reply_markup=reply_markup,
            )
        with suppress(TelegramRejectedError, TelegramDeliveryUncertainError):
            self._api.call(TelegramMethod.DELETE_MESSAGE, {"chat_id": chat_id, "message_id": placeholder})
        return receipts

    def deliver(
        self,
        chat_id: int,
        message_id: int,
        payload: RichMediaPayload,
        platform_name: str,
        sender_label: str | None,
        *,
        reply_markup: str | None = None,
    ) -> tuple[int, ...]:
        """Return minimal message receipts; uncertain acceptance always stops the delivery sequence.

        Args:
            chat_id: Canonical Telegram chat ID to deliver to.
            message_id: Source message ID to reply to.
            payload: Rich media payload to deliver.
            platform_name: Human-readable provider platform label.
            sender_label: Transient decrypted sender display label.
            reply_markup: Optional serialized inline keyboard markup JSON.

        Returns:
            Message IDs of every delivered or placeholder message.

        Raises:
            ValueError: When the chat or source message identity is missing.
            TelegramDeliveryUncertainError: When acceptance is uncertain after some
                messages were delivered.
            TelegramRejectedError: When Telegram rejects the delivery and nothing was
                sent yet.
        """
        if chat_id == 0:
            msg = "Delivery requires canonical chat and source message identities."
            raise ValueError(msg)
        if message_id <= 0:
            msg = "Delivery requires canonical chat and source message identities."
            raise ValueError(msg)
        total_text = build_caption(payload, sender_label, platform_name)
        if not payload.media_items:
            chunks = split_html_message(total_text)
            receipts: list[int] = []
            for idx, chunk in enumerate(chunks):
                markup = reply_markup if idx == len(chunks) - 1 else None
                receipts.extend(self._text(chat_id, chunk, reply_markup=markup))
            return tuple(receipts)
        plan = plan_caption(payload, sender_label, platform_name, total_text)
        preceding = ()
        if plan.separate_text is not None:
            chunks = split_html_message(plan.separate_text)
            preceding_receipts: list[int] = []
            for idx, chunk in enumerate(chunks):
                markup = reply_markup if idx == len(chunks) - 1 else None
                preceding_receipts.extend(self._text(chat_id, chunk, reply_markup=markup))
            preceding = tuple(preceding_receipts)
            reply_markup = None
        try:
            media_receipts = self._deliver_media(chat_id, message_id, payload, plan.caption, reply_markup=reply_markup)
            if reply_markup is not None and len(payload.media_items) > 1 and media_receipts:
                extra = self._text(chat_id, "🌐", reply_to_message_id=media_receipts[0], reply_markup=reply_markup)
                return preceding + media_receipts + extra
            return preceding + media_receipts
        except TelegramDeliveryUncertainError as error:
            raise TelegramDeliveryUncertainError((*preceding, *error.receipts)) from None
        except TelegramRejectedError:
            if preceding:
                raise TelegramDeliveryUncertainError(preceding) from None
            raise

    def _deliver_media(
        self,
        chat_id: int,
        message_id: int,
        payload: RichMediaPayload,
        caption: str,
        *,
        reply_markup: str | None = None,
    ) -> tuple[int, ...]:
        if any(item.requires_download and item.data is None for item in payload.media_items):
            return self._fallback(chat_id, message_id, payload, caption, reply_markup=reply_markup)
        if any(
            item.is_video and item.data is not None and len(item.data) > TELEGRAM_VIDEO_BYTES
            for item in payload.media_items
        ):
            return self._fallback(chat_id, message_id, payload, caption, reply_markup=reply_markup)
        try:
            return self._send_items(chat_id, message_id, payload.media_items, caption, reply_markup=reply_markup)
        except TelegramRejectedError as exc:
            if exc.reason == TelegramRejection.FETCH:
                return self._fallback(chat_id, message_id, payload, caption, reply_markup=reply_markup)
            if exc.reason == TelegramRejection.TOO_LARGE:
                if any(item.is_video and item.data is None for item in payload.media_items):
                    return self._fallback(chat_id, message_id, payload, caption, reply_markup=reply_markup)
                logger.exception("Telegram rejected oversized worker media")
                return self._text(
                    chat_id,
                    caption + "\n\n⚠️ <i>Media is too large, view on original resource.</i>",
                    reply_markup=reply_markup,
                )
            raise
