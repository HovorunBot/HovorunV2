"""Verify local CTranslate2 Whisper speech recognition engine, decoding, and failure boundaries."""

import hashlib
import time
from collections.abc import Sequence
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
    SPEECH_MODEL_FILES,
    ModelFile,
    SpeechModelHTTPClient,
    SpeechModelStore,
)
from hovorunv2.infrastructure.speech.whisper_engine import (
    CTranslate2WhisperEngine,
    decode_audio_to_pcm,
)
from tests.utils.runtime import ManualClock
from tests.utils.sync_http import HTTPFixture

pytestmark = pytest.mark.subprocess
MODEL_TEST_SECONDS = 120


def _bytes_to_gpt2_token(raw: bytes) -> str:
    """Encode raw UTF-8 bytes into GPT-2 byte-level BPE unicode characters."""
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    byte_map = dict(zip(bs, (chr(c) for c in cs), strict=True))
    return "".join(byte_map[b] for b in raw)


def _prepare_synthetic_model_dir(directory: Path) -> tuple[ModelFile, ...]:
    """Create a minimal verified model directory for deterministic engine tests."""
    directory.mkdir(parents=True, exist_ok=True)
    vocab_lines = [
        _bytes_to_gpt2_token("Привіт".encode()),
        _bytes_to_gpt2_token(" світ".encode()),
        _bytes_to_gpt2_token(b"Hello"),
        _bytes_to_gpt2_token(b" world"),
    ]
    # Pad up to index 50257 so special tokens start at 50257
    vocab_lines.extend(["x"] * (50257 - len(vocab_lines)))
    vocab_lines.extend(
        ["<|endoftext|>", "<|startoftranscript|>", "<|uk|>", "<|en|>", "<|transcribe|>", "<|notimestamps|>"]
    )
    files = {
        "config.json": b"{}",
        "model.bin": b"synthetic-ct2-weights",
        "tokenizer.json": b"{}",
        "vocabulary.txt": ("\n".join(vocab_lines) + "\n").encode("utf-8"),
    }
    artifacts: list[ModelFile] = []
    for name, content in files.items():
        (directory / name).write_bytes(content)
        artifacts.append(ModelFile(name, len(content), hashlib.sha256(content).hexdigest()))
    return tuple(artifacts)


@dataclass
class _FakeGenerationResult:
    sequences: list[list[str]]
    sequences_ids: list[list[int]]
    no_speech_prob: float = 0.0


class _FakeWhisperModel:
    """Deterministic test double for ctranslate2.models.Whisper."""

    def __init__(
        self,
        *,
        lang_tag: str = "<|uk|>",
        token_ids: list[int] | None = None,
        no_speech_prob: float = 0.05,
    ) -> None:
        self.lang_tag = lang_tag
        self.token_ids = [0, 1] if token_ids is None else token_ids
        self.no_speech_prob = no_speech_prob
        self.unloaded = False
        self.generate_calls = 0
        self.last_feature_shape: Sequence[int] | None = None

    def detect_language(self, features: object) -> list[list[tuple[str, float]]]:
        self.last_feature_shape = getattr(features, "shape", None)
        return [[(self.lang_tag, 0.99)]]

    def generate(
        self,
        features: object,
        prompts: list[list[str]],
        *,
        beam_size: int = 2,
        return_no_speech_prob: bool = True,
        **kwargs: object,
    ) -> list[_FakeGenerationResult]:
        self.generate_calls += 1
        self.last_feature_shape = getattr(features, "shape", None)
        return [
            _FakeGenerationResult(
                sequences=[[]],
                sequences_ids=[self.token_ids],
                no_speech_prob=self.no_speech_prob,
            )
        ]

    def unload_model(self) -> None:
        self.unloaded = True


def test_decode_audio_rejects_empty_or_invalid() -> None:
    """Empty or garbage audio raises INVALID_INPUT or DECODE_FAILED."""
    with pytest.raises(SpeechRecognitionError) as exc:
        decode_audio_to_pcm(b"")
    assert exc.value.reason == SpeechFailure.INVALID_INPUT

    with pytest.raises(SpeechRecognitionError) as exc:
        decode_audio_to_pcm(b"not a valid audio file header")
    assert exc.value.reason == SpeechFailure.DECODE_FAILED


@pytest.mark.parametrize("filename", ["speech_uk.ogg", "speech_en.ogg", "speech_uk_short.ogg"])
def test_decode_audio_decodes_valid_ogg(filename: str) -> None:
    """Valid audio fixture decodes to 16kHz mono 32-bit float PCM."""
    fixture_path = Path(__file__).parents[2] / "utils" / filename
    data = fixture_path.read_bytes()
    pcm = decode_audio_to_pcm(data)
    assert len(pcm) > 0
    assert len(pcm) % 4 == 0


def test_engine_closed_or_cross_process_rejected(tmp_path: Path) -> None:
    """Closed engine rejects recognition attempts."""
    with SpeechModelHTTPClient() as http:
        store = SpeechModelStore(tmp_path / "models", http)
        engine = CTranslate2WhisperEngine(store)
        engine.close()
        with pytest.raises(ValueError, match="closed or accessed"):
            engine.recognize(b"dummy", time.time() + 10)


def test_missing_baseline_model_never_downloads_during_a_user_request(tmp_path: Path) -> None:
    """Missing prepared assets fail locally without a first-request network download."""
    with (
        HTTPFixture({}) as server,
        SpeechModelHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        engine = CTranslate2WhisperEngine(SpeechModelStore(tmp_path / "models", http, base_url=server.url("")))
        try:
            audio = (Path(__file__).parents[2] / "utils" / "silence.ogg").read_bytes()
            with pytest.raises(SpeechRecognitionError) as failure:
                engine.recognize(audio, time.time() + 10)
            assert failure.value.reason == SpeechFailure.MODEL_UNAVAILABLE
            assert server.requests == []
        finally:
            engine.close()


def test_engine_transcribes_valid_audio_and_handles_no_speech(tmp_path: Path) -> None:
    """Engine computes mel features, decodes BPE UTF-8 tokens, and returns NoSpeech on high no_speech_prob."""
    artifacts = _prepare_synthetic_model_dir(tmp_path / "models")
    clock = ManualClock(1_000.0)
    fake_model = _FakeWhisperModel(lang_tag="<|uk|>", token_ids=[0, 1], no_speech_prob=0.05)

    with SpeechModelHTTPClient() as http:
        store = SpeechModelStore(tmp_path / "models", http, artifacts=artifacts)
        engine = CTranslate2WhisperEngine(store, clock=clock, model_factory=lambda _path: fake_model)
        try:
            audio = (Path(__file__).parents[2] / "utils" / "speech_uk.ogg").read_bytes()
            result = engine.recognize(audio, clock.now() + 30)
            assert isinstance(result, Transcript)
            assert result.text == "Привіт світ"
            assert result.language == "uk"
            assert fake_model.last_feature_shape == [1, 80, 3000]

            fake_model.no_speech_prob = 0.95
            silence = (Path(__file__).parents[2] / "utils" / "silence.ogg").read_bytes()
            assert isinstance(engine.recognize(silence, clock.now() + 30), NoSpeech)
        finally:
            engine.close()
        assert fake_model.unloaded is True


def test_corrupt_audio_preserves_loaded_model_for_subsequent_requests(tmp_path: Path) -> None:
    """Decode failure on corrupt user audio does not unload or reload the Whisper model."""
    artifacts = _prepare_synthetic_model_dir(tmp_path / "models")
    clock = ManualClock(1_000.0)
    load_count = 0
    models: list[_FakeWhisperModel] = []

    def _factory(_path: Path) -> _FakeWhisperModel:
        nonlocal load_count
        load_count += 1
        model = _FakeWhisperModel(lang_tag="<|uk|>", token_ids=[0, 1])
        models.append(model)
        return model

    with SpeechModelHTTPClient() as http:
        store = SpeechModelStore(tmp_path / "models", http, artifacts=artifacts)
        engine = CTranslate2WhisperEngine(store, clock=clock, model_factory=_factory)
        try:
            valid_audio = (Path(__file__).parents[2] / "utils" / "speech_uk_short.ogg").read_bytes()
            first = engine.recognize(valid_audio, clock.now() + 30)
            assert isinstance(first, Transcript)
            assert load_count == 1

            with pytest.raises(SpeechRecognitionError) as failure:
                engine.recognize(b"not valid audio", clock.now() + 30)
            assert failure.value.reason == SpeechFailure.DECODE_FAILED
            assert models[0].unloaded is False

            second = engine.recognize(valid_audio, clock.now() + 30)
            assert isinstance(second, Transcript)
            assert load_count == 1
        finally:
            engine.close()


def test_engine_enforces_deadline_and_cancellation(tmp_path: Path) -> None:
    """Expired deadline or cancellation raises before invoking inference."""
    artifacts = _prepare_synthetic_model_dir(tmp_path / "models")
    clock = ManualClock(1_000.0)
    fake_model = _FakeWhisperModel()

    with SpeechModelHTTPClient() as http:
        store = SpeechModelStore(tmp_path / "models", http, artifacts=artifacts)
        engine = CTranslate2WhisperEngine(store, clock=clock, model_factory=lambda _path: fake_model)
        try:
            audio = (Path(__file__).parents[2] / "utils" / "speech_uk_short.ogg").read_bytes()
            with pytest.raises(SpeechRecognitionError) as expired:
                engine.recognize(audio, clock.now() - 1.0)
            assert expired.value.reason == SpeechFailure.DEADLINE_EXCEEDED

            with pytest.raises(SpeechRecognitionError) as cancelled:
                engine.recognize(audio, clock.now() + 30, cancelled=lambda: True)
            assert cancelled.value.reason == SpeechFailure.CANCELLED
            assert fake_model.generate_calls == 0
        finally:
            engine.close()


def test_engine_unloads_idle_model_after_timeout(tmp_path: Path) -> None:
    """Idle model is retained before timeout and unloaded once idle_timeout_seconds elapses."""
    artifacts = _prepare_synthetic_model_dir(tmp_path / "models")
    clock = ManualClock(1_000.0)
    models: list[_FakeWhisperModel] = []

    def _factory(_path: Path) -> _FakeWhisperModel:
        model = _FakeWhisperModel(lang_tag="<|en|>", token_ids=[2, 3])
        models.append(model)
        return model

    with SpeechModelHTTPClient() as http:
        store = SpeechModelStore(tmp_path / "models", http, artifacts=artifacts)
        engine = CTranslate2WhisperEngine(store, idle_timeout_seconds=60, clock=clock, model_factory=_factory)
        try:
            assert engine.unload_idle() is False
            audio = (Path(__file__).parents[2] / "utils" / "speech_en.ogg").read_bytes()
            result = engine.recognize(audio, clock.now() + 30)
            assert isinstance(result, Transcript)
            assert result.text == "Hello world"
            assert len(models) == 1

            clock.value += 59.0
            assert engine.unload_idle() is False
            assert models[0].unloaded is False

            clock.value += 2.0
            assert engine.unload_idle() is True
            assert models[0].unloaded is True
            assert engine.unload_idle() is False

            # Subsequent request reloads a fresh model instance.
            result2 = engine.recognize(audio, clock.now() + 30)
            assert isinstance(result2, Transcript)
            assert len(models) == 2
        finally:
            engine.close()


def test_pinned_ctranslate2_model_artifacts_are_complete() -> None:
    """Default speech model inventory pins all CTranslate2 Whisper int8 artifacts with SHA-256."""
    names = {artifact.name for artifact in SPEECH_MODEL_FILES}
    assert names == {"config.json", "model.bin", "tokenizer.json", "vocabulary.txt"}
    for artifact in SPEECH_MODEL_FILES:
        assert artifact.size > 0
        assert len(artifact.sha256) == 64
