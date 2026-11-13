"""Deliver speech through one confirmed, strictly source-bound Telegram response."""

import html
import json
import time
from collections.abc import Callable
from pathlib import PurePosixPath

from hovorunv2.application.jobs.http_ports import HTTPFiles, HTTPForm, TelegramDeliveryUncertainError
from hovorunv2.application.speech.contracts import NoSpeech, SpeechFailure, SpeechRecognitionError, SpeechResult
from hovorunv2.application.speech.policies import MAX_AUDIO_BYTES
from hovorunv2.application.speech.ports import SpeechAudioPort, SyncTelegramSpeechPort
from hovorunv2.interface.telegram.transport.sync_api import NativeTelegramAPI, TelegramMethod, extract_message_ids

MAX_MESSAGE_CHARACTERS = 4096
SPEECH_FAILURE_TEXT = {
    SpeechFailure.INVALID_INPUT: "This audio is unsupported or exceeds the speech limits.",
    SpeechFailure.DOWNLOAD_FAILED: "The audio could not be downloaded from Telegram. Please try again later.",
    SpeechFailure.DECODE_FAILED: "This audio could not be decoded.",
    SpeechFailure.MODEL_UNAVAILABLE: "Speech recognition is unavailable. The model must be prepared by an operator.",
    SpeechFailure.INFERENCE_FAILED: "Speech recognition could not finish. Please try again later.",
    SpeechFailure.OUTPUT_LIMIT: "The transcript exceeded the supported output limit.",
    SpeechFailure.DEADLINE_EXCEEDED: "This transcription request expired before it could finish.",
    SpeechFailure.CANCELLED: "This transcription request was cancelled.",
}


class NativeSpeechTransport(SyncTelegramSpeechPort):
    """Use a dedicated bounded HTTP client and preserve explicit delivery uncertainty."""

    def __init__(self, api: NativeTelegramAPI, audio: SpeechAudioPort) -> None:
        self._api = api
        self._audio = audio

    def download_audio(self, file_id: str, deadline: float, *, cancelled: Callable[[], bool]) -> bytes:
        """Require valid file metadata and bounded private audio before its original deadline.

        Args:
            file_id: Private Telegram file reference obtained from encrypted input.
            deadline: Original Unix deadline.
            cancelled: Current request cancellation predicate.

        Returns:
            Nonempty encoded audio within the speech byte cap.

        Raises:
            SpeechRecognitionError: If metadata, download, or deadline validation fails.
        """
        if time.time() >= deadline:
            raise SpeechRecognitionError(SpeechFailure.DEADLINE_EXCEEDED)
        try:
            raw = self._audio.fetch_metadata(file_id, deadline, cancelled=cancelled)
            path = self._require_file_path(raw)
            audio = self._audio.fetch_file(path, deadline, cancelled=cancelled)
        except SpeechRecognitionError:
            raise
        except Exception:
            raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT) from None
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
        if time.time() >= deadline:
            raise SpeechRecognitionError(SpeechFailure.DEADLINE_EXCEEDED)
        return audio

    def _require_file_path(self, raw: object) -> str:
        if not isinstance(raw, dict):
            raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
        path, size = raw.get("file_path"), raw.get("file_size")
        if not isinstance(path, str) or not path or PurePosixPath(path).is_absolute():
            raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
        if any(part == ".." for part in PurePosixPath(path).parts) or any(
            mark in path for mark in (":", "?", "#", "%", "\\")
        ):
            raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
        if size is not None and (type(size) is not int or not 0 < size <= MAX_AUDIO_BYTES):
            raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
        return path

    def deliver_speech(
        self, chat_id: int, source_message_id: int, thread_id: int | None, result: SpeechResult | SpeechFailure
    ) -> int:
        """Require one acknowledged text or document reply, without fallback into another route.

        Args:
            chat_id: Exact accepted destination.
            source_message_id: Original audio message to reply to.
            thread_id: Exact source topic, when present.
            result: Private transcript, no-speech outcome, or safe failure code.

        Returns:
            Acknowledged message identifier.

        Raises:
            TelegramDeliveryUncertainError: If Telegram does not acknowledge exactly one message.
        """
        data: HTTPForm = {
            "chat_id": chat_id,
            "reply_parameters": json.dumps({"message_id": source_message_id, "allow_sending_without_reply": False}),
        }
        fields: dict[str, str | int | float | bool] = dict(data)
        if thread_id is not None:
            fields["message_thread_id"] = thread_id
        method, content, files = self._render(result)
        fields.update(content)
        ids = extract_message_ids(self._api.call(method, data=fields, files=files))
        if len(ids) != 1:
            raise TelegramDeliveryUncertainError(ids)
        return ids[0]

    def _render(self, result: SpeechResult | SpeechFailure) -> tuple[TelegramMethod, HTTPForm, HTTPFiles | None]:
        if isinstance(result, SpeechFailure):
            return TelegramMethod.SEND_MESSAGE, {"text": SPEECH_FAILURE_TEXT[result]}, None
        if isinstance(result, NoSpeech):
            return TelegramMethod.SEND_MESSAGE, {"text": "No speech detected."}, None
        header = f"<b>Transcript</b> ({html.escape(result.language or 'und')}):\n\n"
        text = header + html.escape(result.text)
        if len(text) <= MAX_MESSAGE_CHARACTERS:
            return TelegramMethod.SEND_MESSAGE, {"text": text, "parse_mode": "HTML"}, None
        return (
            TelegramMethod.SEND_DOCUMENT,
            {"caption": "Speech transcript"},
            {"document": ("transcript.txt", result.text.encode("utf-8"), "text/plain; charset=utf-8")},
        )
