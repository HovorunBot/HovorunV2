"""Exercise the real native speech transport against synthetic external Telegram HTTP."""

import json
import time
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from hovorunv2.application.jobs.http_ports import TelegramDeliveryUncertainError
from hovorunv2.application.speech.contracts import NoSpeech, SpeechFailure, SpeechRecognitionError, Transcript
from hovorunv2.infrastructure.speech.audio_http import SpeechAudioHTTPClient
from hovorunv2.interface.telegram.transport.speech import NativeSpeechTransport
from hovorunv2.interface.telegram.transport.sync_api import NativeTelegramAPI
from tests.utils.sync_http import HTTPFixture, HTTPReply, json_reply
from tests.utils.telegram import BOT_CREDENTIAL


def test_valid_ukrainian_voice_download_preserves_audio() -> None:
    """Retrieve public Ukrainian Ogg bytes unchanged through Telegram metadata and file routes."""
    audio = Path("tests/utils/speech_uk.ogg").read_bytes()
    with (
        HTTPFixture(
            {
                f"/bot{BOT_CREDENTIAL}/getFile": json_reply(
                    {"ok": True, "result": {"file_path": "voice/synthetic.oga", "file_size": len(audio)}}
                ),
                f"/file/bot{BOT_CREDENTIAL}/voice/synthetic.oga": HTTPReply(body=audio),
            }
        ) as server,
        SpeechAudioHTTPClient(BOT_CREDENTIAL, api_base=server.url(""), allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        transport = NativeSpeechTransport(NativeTelegramAPI(http, BOT_CREDENTIAL, api_base=server.url("")), http)
        assert transport.download_audio("synthetic-private-id", time.time() + 10, cancelled=bool) == audio


@pytest.mark.parametrize("metadata_failure", [False, True])
def test_telegram_download_failure_does_not_reject_audio_format(
    metadata_failure: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """Report unavailable Telegram downloads separately from invalid recordings without leaking errors."""
    metadata_route = f"/bot{BOT_CREDENTIAL}/getFile"
    file_route = f"/file/bot{BOT_CREDENTIAL}/voice/synthetic.oga"
    rejected = HTTPReply(status=HTTPStatus.SERVICE_UNAVAILABLE, body=b"synthetic-private-error")
    with (
        HTTPFixture(
            {
                metadata_route: rejected
                if metadata_failure
                else json_reply({"ok": True, "result": {"file_path": "voice/synthetic.oga"}}),
                file_route: rejected,
                f"/bot{BOT_CREDENTIAL}/sendMessage": json_reply({"ok": True, "result": {"message_id": 91}}),
            }
        ) as server,
        SpeechAudioHTTPClient(BOT_CREDENTIAL, api_base=server.url(""), allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        transport = NativeSpeechTransport(NativeTelegramAPI(http, BOT_CREDENTIAL, api_base=server.url("")), http)
        with pytest.raises(SpeechRecognitionError) as failure:
            transport.download_audio("synthetic-private-id", time.time() + 10, cancelled=bool)
        assert failure.value.reason == SpeechFailure.DOWNLOAD_FAILED
        assert "synthetic-private-error" not in str(failure.value)
        assert BOT_CREDENTIAL not in str(failure.value)
        assert BOT_CREDENTIAL not in caplog.text
        assert "synthetic-private-error" not in caplog.text
        assert "synthetic-private-id" not in caplog.text
        assert transport.deliver_speech(-123, 45, None, failure.value.reason) == 91
        fields = parse_qs(server.requests[-1].body.decode())
        assert fields["text"] == ["The audio could not be downloaded from Telegram. Please try again later."]


@pytest.mark.parametrize("document", [False, True])
def test_unicode_transcript_is_one_strict_topic_bound_response(document: bool) -> None:
    """Preserve Unicode and escaping while preventing fallback after the original source disappears."""
    text = "Синтетичний <приклад> & текст." * (200 if document else 1)
    method = "sendDocument" if document else "sendMessage"
    with (
        HTTPFixture(
            {f"/bot{BOT_CREDENTIAL}/{method}": json_reply({"ok": True, "result": {"message_id": 91}})}
        ) as server,
        SpeechAudioHTTPClient(BOT_CREDENTIAL, api_base=server.url(""), allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        transport = NativeSpeechTransport(NativeTelegramAPI(http, BOT_CREDENTIAL, api_base=server.url("")), http)
        assert transport.deliver_speech(-123, 45, 6, Transcript(text, "uk")) == 91
        assert len(server.requests) == 1
        request = server.requests[0]
        if document:
            assert text.encode() in request.body
            assert b"transcript.txt" in request.body
            assert b"allow_sending_without_reply" in request.body
        else:
            fields = parse_qs(request.body.decode())
            assert fields["message_thread_id"] == ["6"]
            assert fields["chat_id"] == ["-123"]
            assert json.loads(fields["reply_parameters"][0]) == {
                "message_id": 45,
                "allow_sending_without_reply": False,
            }
            assert "&lt;приклад&gt; &amp;" in fields["text"][0]


@pytest.mark.parametrize("result", [NoSpeech(), Transcript("Synthetic transcript", "en")])
def test_unacknowledged_response_never_becomes_delivery_success(result: NoSpeech | Transcript) -> None:
    """An API response without a message identifier remains uncertain and is never retried."""
    with (
        HTTPFixture({f"/bot{BOT_CREDENTIAL}/sendMessage": json_reply({"ok": True, "result": {}})}) as server,
        SpeechAudioHTTPClient(BOT_CREDENTIAL, api_base=server.url(""), allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        transport = NativeSpeechTransport(NativeTelegramAPI(http, BOT_CREDENTIAL, api_base=server.url("")), http)
        with pytest.raises(TelegramDeliveryUncertainError):
            transport.deliver_speech(-123, 45, 6, result)
        assert len(server.requests) == 1


@pytest.mark.parametrize(
    "path", ["/voice/private.ogg", "../private", "voice/%2e%2e/private", "https://example.org/file"]
)
def test_untrusted_file_paths_are_rejected_before_audio_download(path: str) -> None:
    """Telegram metadata cannot redirect authenticated audio requests outside the expected file route."""
    with (
        HTTPFixture(
            {f"/bot{BOT_CREDENTIAL}/getFile": json_reply({"ok": True, "result": {"file_path": path}})}
        ) as server,
        SpeechAudioHTTPClient(BOT_CREDENTIAL, api_base=server.url(""), allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        transport = NativeSpeechTransport(NativeTelegramAPI(http, BOT_CREDENTIAL, api_base=server.url("")), http)
        with pytest.raises(SpeechRecognitionError) as failure:
            transport.download_audio("synthetic-private-id", time.time() + 10, cancelled=bool)
        assert failure.value.reason == SpeechFailure.INVALID_INPUT
        assert len(server.requests) == 1


def test_audio_download_rejects_oversized_declared_stream() -> None:
    """A streamed response cannot exceed the compressed audio cap even when input metadata lies."""
    route = f"/file/bot{BOT_CREDENTIAL}/voice/synthetic.ogg"
    with (
        HTTPFixture({route: HTTPReply(body=b"synthetic", headers={"Content-Length": "10000001"})}) as server,
        SpeechAudioHTTPClient(BOT_CREDENTIAL, api_base=server.url(""), allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        with pytest.raises(SpeechRecognitionError) as failure:
            http.fetch_file("voice/synthetic.ogg", time.time() + 10, cancelled=bool)
        assert failure.value.reason == SpeechFailure.INVALID_INPUT


def test_drip_fed_audio_cannot_extend_the_original_deadline() -> None:
    """Small continuous network reads must not renew the speech request's execution window."""
    route = f"/file/bot{BOT_CREDENTIAL}/voice/synthetic.ogg"
    response = HTTPReply(chunks=(b"x" * 1024,) * 20, chunk_delay_seconds=0.1)
    with (
        HTTPFixture({route: response}) as server,
        SpeechAudioHTTPClient(BOT_CREDENTIAL, api_base=server.url(""), allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        started = time.monotonic()
        with pytest.raises(SpeechRecognitionError) as failure:
            http.fetch_file("voice/synthetic.ogg", time.time() + 0.2, cancelled=bool)
        assert failure.value.reason == SpeechFailure.DEADLINE_EXCEEDED
        assert time.monotonic() - started < 1


def test_drip_fed_metadata_cannot_extend_the_original_deadline() -> None:
    """Fetching a Telegram file reference obeys the same original deadline as its audio stream."""
    route = f"/bot{BOT_CREDENTIAL}/getFile"
    response = HTTPReply(chunks=(b" ",) * 20, chunk_delay_seconds=0.1)
    with (
        HTTPFixture({route: response}) as server,
        SpeechAudioHTTPClient(BOT_CREDENTIAL, api_base=server.url(""), allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        transport = NativeSpeechTransport(NativeTelegramAPI(http, BOT_CREDENTIAL, api_base=server.url("")), http)
        started = time.monotonic()
        with pytest.raises(SpeechRecognitionError) as failure:
            transport.download_audio("synthetic-file-id", time.time() + 0.2, cancelled=bool)
        assert failure.value.reason == SpeechFailure.DEADLINE_EXCEEDED
        assert time.monotonic() - started < 1
