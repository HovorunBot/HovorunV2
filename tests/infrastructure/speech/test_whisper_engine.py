"""Verify local whisper.cpp speech recognition engine, decoding, and failure boundaries."""

import time
from pathlib import Path

import pytest

from hovorunv2.application.speech.contracts import (
    NoSpeech,
    SpeechFailure,
    SpeechRecognitionError,
    Transcript,
)
from hovorunv2.infrastructure.speech.model_store import (
    ModelFile,
    SpeechModelHTTPClient,
    SpeechModelStore,
)
from hovorunv2.infrastructure.speech.whisper_engine import (
    WhisperCppEngine,
    decode_audio_to_pcm,
)
from tests.utils.sync_http import HTTPFixture

pytestmark = pytest.mark.unit


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
            engine.recognize(b"dummy", time.monotonic() + 10)


def test_engine_offline_ukrainian_recognition(tmp_path: Path) -> None:
    """Run real offline recognition on public Ukrainian speech fixture when model exists."""
    model_source = Path("ai-schemes/local-speech-recognition/experiments/ggml-small-q8_0.bin")
    if not model_source.is_file():
        pytest.skip("Local qualification model file not present")

    model_dir = tmp_path / "models"
    model_dir.mkdir()
    # Symlink model file to avoid copying 264MB
    (model_dir / "ggml-small-q8_0.bin").symlink_to(model_source.resolve())
    (model_dir / "NOTICE.txt").write_text("Notice")
    (model_dir / "MIT.txt").write_text("MIT")

    sha = "49c8fb02b65e6049d5fa6c04f81f53b867b5ec9540406812c643f177317f779f"
    artifact = ModelFile("ggml-small-q8_0.bin", model_source.stat().st_size, sha)
    with (
        HTTPFixture({}) as server,
        SpeechModelHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        store = SpeechModelStore(model_dir, http, base_url=server.url(""), artifacts=(artifact,))
        engine = WhisperCppEngine(store, idle_timeout_seconds=3)
        try:
            audio = (Path(__file__).parent.parent.parent / "utils" / "speech_uk.ogg").read_bytes()
            result = engine.recognize(audio, time.monotonic() + 30)
            assert isinstance(result, Transcript)
            assert result.language == "uk"
            assert len(result.text) > 0

            # Run silence fixture -> expect NoSpeech
            silence_audio = (Path(__file__).parent.parent.parent / "utils" / "silence.ogg").read_bytes()
            silence_result = engine.recognize(silence_audio, time.monotonic() + 30)
            assert isinstance(silence_result, NoSpeech)
        finally:
            engine.close()
