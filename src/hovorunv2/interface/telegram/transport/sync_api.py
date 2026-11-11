"""Synchronous Telegram Bot API client for native workers."""

import logging
from enum import StrEnum
from http import HTTPStatus

from hovorunv2.application.jobs.http_ports import (
    HTTPFiles,
    HTTPForm,
    HTTPTransportError,
    SyncHTTPPort,
    TelegramDeliveryUncertainError,
    TelegramRejectedError,
    TelegramRejection,
)

logger = logging.getLogger(__name__)


class TelegramMethod(StrEnum):
    """The intentionally narrow supported Bot API surface."""

    SEND_MESSAGE = "sendMessage"
    SEND_PHOTO = "sendPhoto"
    SEND_VIDEO = "sendVideo"
    SEND_DOCUMENT = "sendDocument"
    SEND_MEDIA_GROUP = "sendMediaGroup"
    EDIT_MESSAGE_TEXT = "editMessageText"
    EDIT_MESSAGE_MEDIA = "editMessageMedia"
    DELETE_MESSAGE = "deleteMessage"
    GET_CHAT_MEMBER = "getChatMember"
    GET_CHAT_ADMINISTRATORS = "getChatAdministrators"
    GET_FILE = "getFile"


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
        """Distinguish a definite Telegram rejection from an uncertain mutating request.

        Args:
            method: Telegram Bot API method to invoke.
            data: Optional form fields for the request.
            files: Optional multipart file fields for the request.

        Returns:
            Parsed API result object.

        Raises:
            TelegramDeliveryUncertainError: When transport fails or the response is
                not a definite, well-formed Telegram result.
        """
        operation = TelegramMethod(method)
        try:
            response = self._http.request_json("POST", self._base + operation.value, data=data, files=files)
        except HTTPTransportError:
            raise TelegramDeliveryUncertainError from None
        if (
            operation in {TelegramMethod.GET_CHAT_MEMBER, TelegramMethod.GET_CHAT_ADMINISTRATORS}
            and isinstance(response, dict)
            and response.get("ok") is False
        ):
            error_code = response.get("error_code")
            logger.warning(
                "Telegram membership API rejected verification: chat=%s administrators=%s error_code=%s",
                data.get("chat_id") if data is not None else None,
                operation == TelegramMethod.GET_CHAT_ADMINISTRATORS,
                error_code if type(error_code) is int else None,
            )
        return _api_result(response)

    def get_chat_member(self, chat_id: int, user_id: int) -> dict[str, object] | None:
        """Fetch member payload or return None on API rejection or transport failure.

        Args:
            chat_id: Telegram chat ID to query.
            user_id: Telegram user ID to fetch.

        Returns:
            Member payload, or None when the API rejects or transport fails.
        """
        try:
            result = self.call(TelegramMethod.GET_CHAT_MEMBER, data={"chat_id": chat_id, "user_id": user_id})
        except (TelegramRejectedError, TelegramDeliveryUncertainError) as error:
            logger.warning("Telegram getChatMember unavailable: chat=%d failure=%s", chat_id, type(error))
            return None
        else:
            if not isinstance(result, dict):
                logger.warning("Telegram getChatMember returned an invalid result: chat=%d", chat_id)
            return result if isinstance(result, dict) else None

    def get_chat_administrators(self, chat_id: int) -> list[object] | None:
        """Fetch admin list or return None on API rejection or transport failure.

        Args:
            chat_id: Telegram chat ID to query.

        Returns:
            Administrator list, or None when the API rejects or transport fails.
        """
        try:
            result = self.call(TelegramMethod.GET_CHAT_ADMINISTRATORS, data={"chat_id": chat_id})
        except (TelegramRejectedError, TelegramDeliveryUncertainError) as error:
            logger.warning("Telegram getChatAdministrators unavailable: chat=%d failure=%s", chat_id, type(error))
            return None
        else:
            if not isinstance(result, list):
                logger.warning("Telegram getChatAdministrators returned an invalid result: chat=%d", chat_id)
            return result if isinstance(result, list) else None

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: str | None = None,
        disable_web_page_preview: bool = True,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> int | None:
        """Send formatted text message and return message ID if delivered.

        Args:
            chat_id: Telegram chat ID to send to.
            text: Message text to send.
            parse_mode: Parsing mode for the message text.
            reply_markup: Optional serialized reply markup.
            disable_web_page_preview: Whether to disable link previews.
            reply_to_message_id: Optional message to reply to.
            message_thread_id: Optional Telegram forum topic thread ID.

        Returns:
            Delivered message ID, or None when the API rejects or transport fails.
        """
        data: dict[str, str | int | float | bool] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_markup is not None:
            data["reply_markup"] = reply_markup
        if reply_to_message_id is not None:
            data["reply_to_message_id"] = reply_to_message_id
        if message_thread_id is not None:
            data["message_thread_id"] = message_thread_id
        try:
            result = self.call(TelegramMethod.SEND_MESSAGE, data=data)
        except TelegramRejectedError, TelegramDeliveryUncertainError:
            return None
        else:
            return _require_message_id(result)

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Delete message, returning True if accepted by Telegram.

        Args:
            chat_id: Telegram chat ID of the message.
            message_id: Message ID to delete.

        Returns:
            True when Telegram accepts the deletion, False otherwise.
        """
        try:
            result = self.call(TelegramMethod.DELETE_MESSAGE, data={"chat_id": chat_id, "message_id": message_id})
        except TelegramRejectedError, TelegramDeliveryUncertainError:
            return False
        else:
            return result is True

    def edit_message_text(self, chat_id: int, message_id: int, text: str, *, parse_mode: str = "HTML") -> bool:
        """Edit text message, returning True if accepted.

        Args:
            chat_id: Telegram chat ID of the message.
            message_id: Message ID to edit.
            text: New message text.
            parse_mode: Parsing mode for the new text.

        Returns:
            True when Telegram accepts the edit, False otherwise.
        """
        try:
            result = self.call(
                TelegramMethod.EDIT_MESSAGE_TEXT,
                data={"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": parse_mode},
            )
        except TelegramRejectedError, TelegramDeliveryUncertainError:
            return False
        else:
            return isinstance(result, dict)

    def get_file(self, file_id: str) -> dict[str, object] | None:
        """Fetch file metadata including file_path."""
        try:
            result = self.call(TelegramMethod.GET_FILE, data={"file_id": file_id})
        except TelegramRejectedError, TelegramDeliveryUncertainError:
            return None
        return result if isinstance(result, dict) else None

    def download_file_bytes(self, file_path: str) -> bytes:
        """Stream and download file bytes from Telegram Bot API."""
        file_url = self._base.replace("/bot", "/file/bot") + file_path.lstrip("/")
        return self._http.download_bytes(file_url)

    def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        *,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> int | None:
        """Send text document attachment and return message ID if delivered."""
        data: dict[str, str | int | float | bool] = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        if reply_to_message_id is not None:
            data["reply_to_message_id"] = reply_to_message_id
        if message_thread_id is not None:
            data["message_thread_id"] = message_thread_id
        if message_thread_id is not None:
            data["message_thread_id"] = message_thread_id
        files: HTTPFiles = {"document": (filename, content, "text/plain; charset=utf-8")}
        try:
            result = self.call(TelegramMethod.SEND_DOCUMENT, data=data, files=files)
        except TelegramRejectedError, TelegramDeliveryUncertainError:
            return None
        return _require_message_id(result)


def _api_result(response: object) -> object:
    if not isinstance(response, dict) or type(response.get("ok")) is not bool:
        raise TelegramDeliveryUncertainError
    if response["ok"] is True:
        if "result" not in response:
            raise TelegramDeliveryUncertainError
        return response["result"]
    code, description = response.get("error_code"), response.get("description")
    if type(code) is not int or not isinstance(description, str):
        raise TelegramDeliveryUncertainError
    raise TelegramRejectedError(_rejection_reason(code, description))


def _rejection_reason(code: int, description: str) -> TelegramRejection:
    upper = description.upper()
    if code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE or any(
        text in upper for text in ("FILE IS TOO BIG", "REQUEST ENTITY TOO LARGE")
    ):
        return TelegramRejection.TOO_LARGE
    if code == HTTPStatus.BAD_REQUEST and any(text in upper for text in ("WEBPAGE_CURL_FAILED", "FAILED TO GET")):
        return TelegramRejection.FETCH
    return TelegramRejection.OTHER


def extract_message_ids(result: object) -> tuple[int, ...]:
    """Extract message identifiers from a raw Telegram API result object.

    Args:
        result: Decoded Telegram API response body.

    Returns:
        Tuple of integer message identifiers.

    Raises:
        TelegramDeliveryUncertainError: If response is missing valid message IDs.
    """
    values = result if isinstance(result, list) else [result]
    ids = []
    for value in values:
        try:
            ids.append(_require_message_id(value))
        except TelegramDeliveryUncertainError:
            raise TelegramDeliveryUncertainError(tuple(ids)) from None
    if not ids:
        raise TelegramDeliveryUncertainError
    return tuple(ids)


def _require_message_id(value: object) -> int:
    if not isinstance(value, dict):
        raise TelegramDeliveryUncertainError
    identifier = value.get("message_id")
    if type(identifier) is not int or identifier <= 0:
        raise TelegramDeliveryUncertainError
    return identifier
