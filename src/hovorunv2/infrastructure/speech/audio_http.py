"""Bound authenticated speech downloads without logging token-bearing URLs."""

import json
import logging
import time
from collections.abc import Callable

import requests

from hovorunv2.application.speech.contracts import SpeechFailure, SpeechRecognitionError
from hovorunv2.application.speech.policies import MAX_AUDIO_BYTES
from hovorunv2.application.speech.ports import SpeechAudioPort
from hovorunv2.infrastructure.http_transport.native_http import HTTP_CHUNK_BYTES, NativeHTTPClient

AUDIO_READ_TIMEOUT_SECONDS = 5
AUDIO_TOTAL_TIMEOUT_SECONDS = 30
MAX_METADATA_BYTES = 64_000
logger = logging.getLogger(__name__)


class SpeechAudioHTTPClient(NativeHTTPClient, SpeechAudioPort):
    """Own a nonredirecting, capped Telegram audio connection in one worker child."""

    def __init__(
        self, token: str, *, api_base: str = "https://api.telegram.org", allowed_hosts: frozenset[str] = frozenset()
    ) -> None:
        super().__init__(
            max_bytes=MAX_AUDIO_BYTES,
            max_redirects=0,
            allowed_hosts=allowed_hosts,
            read_timeout=AUDIO_READ_TIMEOUT_SECONDS,
            total_timeout=AUDIO_TOTAL_TIMEOUT_SECONDS,
        )
        self._file_base = api_base.rstrip("/") + "/file/bot" + token + "/"
        self._metadata_url = api_base.rstrip("/") + "/bot" + token + "/getFile"

    def fetch_metadata(self, file_id: str, deadline: float, *, cancelled: Callable[[], bool]) -> dict[str, object]:
        """Read bounded file metadata with the same deadline and cancellation checks as audio.

        Args:
            file_id: Private accepted Telegram file reference.
            deadline: Original Unix deadline.
            cancelled: Current cancellation predicate.

        Returns:
            Validated successful Telegram metadata object.

        Raises:
            SpeechRecognitionError: If reading, authorization, deadline or metadata validation fails.
        """
        cutoff = min(deadline, time.time() + AUDIO_TOTAL_TIMEOUT_SECONDS)
        self._require_live(cutoff, cancelled)
        try:
            with self._request("POST", self._metadata_url, data={"file_id": file_id}) as response:
                response.raise_for_status()
                body = self._read_response(response, cutoff, cancelled, MAX_METADATA_BYTES)
            payload = json.loads(body)
        except SpeechRecognitionError:
            raise
        except Exception as error:
            logger.warning("Speech metadata download failed: %s", type(error).__name__)
            raise SpeechRecognitionError(SpeechFailure.DOWNLOAD_FAILED) from None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise SpeechRecognitionError(SpeechFailure.DOWNLOAD_FAILED)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise SpeechRecognitionError(SpeechFailure.DOWNLOAD_FAILED)
        return result

    def fetch_file(self, file_path: str, deadline: float, *, cancelled: Callable[[], bool]) -> bytes:
        """Retrieve capped private audio while checking the original deadline and current cancellation.

        Args:
            file_path: Validated relative Telegram file path.
            deadline: Original Unix execution deadline.
            cancelled: Current authorization and cancellation predicate.

        Returns:
            Bounded encoded audio bytes.

        Raises:
            SpeechRecognitionError: If authorization, transport or resource bounds fail.
        """
        cutoff = min(deadline, time.time() + AUDIO_TOTAL_TIMEOUT_SECONDS)
        self._require_live(cutoff, cancelled)
        try:
            with self._request("GET", self._file_base + file_path) as response:
                response.raise_for_status()
                return self._read_response(response, cutoff, cancelled, MAX_AUDIO_BYTES)
        except SpeechRecognitionError:
            raise
        except Exception as error:
            logger.warning("Speech audio download failed: %s", type(error).__name__)
            raise SpeechRecognitionError(SpeechFailure.DOWNLOAD_FAILED) from None

    def _read_response(
        self, response: requests.Response, cutoff: float, cancelled: Callable[[], bool], limit: int
    ) -> bytes:
        length = response.headers.get("Content-Length")
        if length is not None and length.isdecimal() and int(length) > limit:
            raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
        output = bytearray()
        while True:
            self._require_live(cutoff, cancelled)
            chunk = response.raw.read1(HTTP_CHUNK_BYTES, decode_content=True)
            self._require_live(cutoff, cancelled)
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > limit:
                raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
        return bytes(output)

    def _require_live(self, cutoff: float, cancelled: Callable[[], bool]) -> None:
        if time.time() >= cutoff:
            raise SpeechRecognitionError(SpeechFailure.DEADLINE_EXCEEDED)
        if cancelled():
            raise SpeechRecognitionError(SpeechFailure.CANCELLED)
