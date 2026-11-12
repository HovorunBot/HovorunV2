"""Verify local whisper.cpp speech recognition engine, decoding, and failure boundaries."""

import json
import os
import select
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from hovorunv2.application.speech.contracts import (
    NoSpeech,
    SpeechFailure,
    SpeechRecognitionError,
    Transcript,
)
from hovorunv2.infrastructure.speech.model_store import (
    SpeechModelHTTPClient,
    SpeechModelStore,
)
from hovorunv2.infrastructure.speech.whisper_engine import (
    WhisperCppEngine,
    decode_audio_to_pcm,
)
from tests.utils.sync_http import HTTPFixture

pytestmark = pytest.mark.subprocess
MODEL_TEST_SECONDS = 120


def test_decode_audio_rejects_empty_or_invalid() -> None:
    """Empty or garbage audio raises INVALID_INPUT or DECODE_FAILED."""
    with pytest.raises(SpeechRecognitionError) as exc:
        decode_audio_to_pcm(b"")
    assert exc.value.reason == SpeechFailure.INVALID_INPUT

    with pytest.raises(SpeechRecognitionError) as exc:
        decode_audio_to_pcm(b"not a valid audio file header")
    assert exc.value.reason == SpeechFailure.DECODE_FAILED


def test_decode_audio_decodes_valid_ogg() -> None:
    """Valid audio fixture decodes to 16kHz mono 32-bit float PCM."""
    fixture_path = Path(__file__).parent.parent.parent / "utils" / "speech_uk.ogg"
    if not fixture_path.is_file():
        pytest.skip("speech_uk.ogg fixture not found")
    data = fixture_path.read_bytes()
    pcm = decode_audio_to_pcm(data)
    assert len(pcm) > 0
    assert len(pcm) % 4 == 0


def test_engine_closed_or_cross_process_rejected(tmp_path: Path) -> None:
    """Closed engine rejects recognition attempts."""
    with SpeechModelHTTPClient() as http:
        store = SpeechModelStore(tmp_path / "models", http)
        engine = WhisperCppEngine(store)
        engine.close()
        with pytest.raises(ValueError, match="closed or accessed"):
            engine.recognize(b"dummy", time.time() + 10)


@pytest.mark.parametrize("language", ["uk", "en"])
def test_engine_offline_multilingual_recognition(language: str) -> None:
    """Recognize public Ukrainian and English speech offline with automatic language detection."""
    store_path = os.environ.get("HOVORUN_SPEECH_TEST_MODEL_DIR")
    if store_path is None:
        pytest.skip("Set HOVORUN_SPEECH_TEST_MODEL_DIR for native integration")
    helper = Path(os.environ.get("HOVORUN_SPEECH_TEST_HELPER", "/usr/local/bin/hovorun-whisper-helper"))
    with HTTPFixture({}) as server, SpeechModelHTTPClient() as http:
        engine = WhisperCppEngine(SpeechModelStore(Path(store_path), http), helper_path=helper, idle_timeout_seconds=1)
        try:
            audio = (Path("tests/utils") / f"speech_{language}.ogg").read_bytes()
            result = engine.recognize(audio, time.time() + MODEL_TEST_SECONDS)
            assert isinstance(result, Transcript)
            assert result.language == language
            assert result.text.strip()
            silence = Path("tests/utils/silence.ogg").read_bytes()
            assert isinstance(engine.recognize(silence, time.time() + MODEL_TEST_SECONDS), NoSpeech)
            # No incoming traffic is required for native model memory to be released.
            children = _native_children()
            assert len(children) == 1
            _wait_for_no_children()
            assert isinstance(engine.recognize(silence, time.time() + MODEL_TEST_SECONDS), NoSpeech)
            assert _native_children() != children
            assert server.requests == []
        finally:
            engine.close()
        assert _native_children() == set()


def _native_children() -> set[str]:
    helper_name = Path(os.environ.get("HOVORUN_SPEECH_TEST_HELPER", "/usr/local/bin/hovorun-whisper-helper")).name[:15]
    children = Path(f"/proc/self/task/{os.getpid()}/children").read_text().split()
    return {pid for pid in children if Path(f"/proc/{pid}/comm").read_text().strip() == helper_name}


def _wait_for_no_children() -> None:
    cutoff = time.monotonic() + 5
    while time.monotonic() < cutoff:
        # Reap a naturally exited child before checking whether model memory is gone.
        live = [pid for pid in _native_children() if "State:\tZ" not in Path(f"/proc/{pid}/status").read_text()]
        if not live:
            return
        time.sleep(0.05)
    pytest.fail("The idle helper retained live model memory")


@dataclass
class TimedCancellation:
    """Revoke a real native operation after a fixed elapsed interval."""

    cutoff: float

    def __call__(self) -> bool:
        """Report whether revocation has become effective."""
        return time.monotonic() >= self.cutoff


@pytest.mark.parametrize("cancel", [False, True])
def test_native_work_stops_before_deadline_or_cancel_returns(cancel: bool) -> None:
    """Deadline expiry and cancellation stop native inference before ownership can be released."""
    directory = os.environ.get("HOVORUN_SPEECH_TEST_MODEL_DIR")
    if directory is None:
        pytest.skip("Prepared native model required")
    helper = Path(os.environ.get("HOVORUN_SPEECH_TEST_HELPER", "/usr/local/bin/hovorun-whisper-helper"))
    audio = Path("tests/utils/speech_uk.ogg").read_bytes()
    with SpeechModelHTTPClient() as http:
        engine = WhisperCppEngine(SpeechModelStore(Path(directory), http), helper_path=helper)
        try:
            deadline = time.time() + (30 if cancel else 0.3)
            predicate = TimedCancellation(time.monotonic() + (0.3 if cancel else 30))
            with pytest.raises(SpeechRecognitionError) as failure:
                engine.recognize(audio, deadline, cancelled=predicate)
            assert failure.value.reason == (SpeechFailure.CANCELLED if cancel else SpeechFailure.DEADLINE_EXCEEDED)
            assert _native_children() == set()
            # A subsequent unrelated request remains usable after confirmed cleanup.
            assert isinstance(
                engine.recognize(Path("tests/utils/silence.ogg").read_bytes(), time.time() + MODEL_TEST_SECONDS),
                NoSpeech,
            )
        finally:
            engine.close()


def test_missing_baseline_model_never_downloads_during_a_user_request(tmp_path: Path) -> None:
    """Missing prepared assets fail locally without a first-request network download."""
    with (
        HTTPFixture({}) as server,
        SpeechModelHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        engine = WhisperCppEngine(SpeechModelStore(tmp_path / "models", http, base_url=server.url("")))
        try:
            audio = (Path(__file__).parents[2] / "utils" / "silence.ogg").read_bytes()
            with pytest.raises(SpeechRecognitionError) as failure:
                engine.recognize(audio, time.time() + 10)
            assert failure.value.reason == SpeechFailure.MODEL_UNAVAILABLE
            assert server.requests == []
        finally:
            engine.close()


def _protocol_response(process: subprocess.Popen[bytes]) -> dict[str, object]:
    stdout = process.stdout
    assert stdout is not None
    assert select.select([stdout], [], [], 10)[0]
    header = stdout.read(4)
    assert len(header) == 4
    length = struct.unpack("<I", header)[0]
    assert 0 < length < 128_000
    value = json.loads(stdout.read(length))
    assert isinstance(value, dict)
    return value


def test_accepted_request_cannot_expire_during_audio_dispatch() -> None:
    """Idle teardown never interrupts an acknowledged native request waiting for its private audio."""
    directory = os.environ.get("HOVORUN_SPEECH_TEST_MODEL_DIR")
    if directory is None:
        pytest.skip("Prepared native model required")
    helper = os.environ.get("HOVORUN_SPEECH_TEST_HELPER", "/usr/local/bin/hovorun-whisper-helper")
    with subprocess.Popen(  # noqa: S603
        [helper, str(Path(directory) / "ggml-small-q8_0.bin"), "1000"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "OMP_NUM_THREADS": "2"},
        bufsize=0,
    ) as process:
        try:
            assert _protocol_response(process)["protocol"] == 2
            stdin = process.stdin
            assert stdin is not None
            stdin.write(struct.pack("<I", 0))
            assert _protocol_response(process)["status"] == "accepted"
            time.sleep(2)
            assert process.poll() is None
            pcm = bytes(16_000 * 4)
            stdin.write(struct.pack("<I", len(pcm)))
            stdin.write(pcm)
            assert _protocol_response(process)["status"] == "no_speech"
            assert process.wait(timeout=5) == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
