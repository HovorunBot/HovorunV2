"""Synchronous CTranslate2 Whisper speech recognition engine with bounded FFmpeg decoding."""

import os
import subprocess
import time
from collections.abc import Callable, Sequence
from functools import cache
from pathlib import Path
from typing import Protocol

lazy import ctranslate2
lazy import numpy as np

from hovorunv2.application.speech.contracts import (
    MAX_TRANSCRIPT_CHARS,
    NoSpeech,
    SpeechFailure,
    SpeechRecognitionError,
    SpeechResult,
    Transcript,
)
from hovorunv2.application.speech.policies import SpeechLimits
from hovorunv2.application.speech.ports import SpeechRecognitionPort
from hovorunv2.application.usage.budgets import Clock, WallClock
from hovorunv2.infrastructure.speech.model_store import SpeechModelStore
from hovorunv2.infrastructure.speech.process_io import (
    exchange_decoder,
    never_cancelled,
    require_time,
    stop_process,
)

SAMPLE_RATE = 16_000
MAX_AUDIO_SECONDS = 120
MAX_PCM_BYTES = SAMPLE_RATE * MAX_AUDIO_SECONDS * 4
MAX_COMPRESSED_AUDIO_BYTES = 10_000_000
TRUNCATION_NOTICE = "\n[Transcript truncated at the output limit.]"
MEMORY_LIMIT_BYTES = 2 * 1024**3
FFMPEG_TIMEOUT_SECONDS = 15
FFMPEG_CPU_LIMIT_SECONDS = 30

INFERENCE_INTER_THREADS = 1
INFERENCE_INTRA_THREADS = 2
BEAM_SIZE = 2
NO_SPEECH_THRESHOLD = 0.6

WHISPER_N_FFT = 400
WHISPER_HOP_LENGTH = 160
WHISPER_CHUNK_SECONDS = 30
WHISPER_CHUNK_SAMPLES = SAMPLE_RATE * WHISPER_CHUNK_SECONDS
WHISPER_N_FRAMES = WHISPER_CHUNK_SAMPLES // WHISPER_HOP_LENGTH
WHISPER_N_MELS = 80
SPECIAL_TOKEN_START_ID = 50_257


class WhisperGenerationResultProtocol(Protocol):
    """Minimal structural contract for one CTranslate2 Whisper generation result."""

    @property
    def sequences_ids(self) -> Sequence[Sequence[int]]:
        """Return generated token ID sequences."""
        ...

    @property
    def no_speech_prob(self) -> float:
        """Return estimated probability of no speech."""
        ...


class WhisperModelProtocol(Protocol):
    """Structural contract for CTranslate2 Whisper model instances."""

    def detect_language(self, features: ctranslate2.StorageView) -> Sequence[Sequence[tuple[str, float]]]:
        """Detect language probabilities from log-mel features."""
        ...

    def generate(
        self,
        features: ctranslate2.StorageView,
        prompts: list[list[str]],
        *,
        beam_size: int = 5,
        return_no_speech_prob: bool = False,
    ) -> Sequence[WhisperGenerationResultProtocol]:
        """Generate token sequences from log-mel features and prompt tokens."""
        ...

    def unload_model(self) -> None:
        """Release model weights from memory."""
        ...


def _decoder_environment() -> dict[str, str]:
    """Pass minimal environment variables without exposing application secrets."""
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "OMP_NUM_THREADS": "2",
        "OPENBLAS_NUM_THREADS": "2",
    }


def decode_audio_to_pcm(
    audio_data: bytes,
    deadline: float | None = None,
    *,
    cancelled: Callable[[], bool] = never_cancelled,
    clock: Clock | None = None,
) -> bytes:
    """Decode private audio in an isolated, byte/time-bounded FFmpeg child.

    Args:
        audio_data: Bounded compressed audio.
        deadline: Optional Unix request deadline; standalone decoding retains its fixed decoder timeout.
        cancelled: Current cancellation predicate.
        clock: Optional clock used for deadline checks.

    Returns:
        Bounded mono 16 kHz float32 PCM.

    Raises:
        SpeechRecognitionError: If decoding, authorization, input or time bounds fail.
    """
    if not audio_data or len(audio_data) > MAX_COMPRESSED_AUDIO_BYTES:
        raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
    now = (clock or WallClock()).now()
    remaining = FFMPEG_TIMEOUT_SECONDS if deadline is None else min(FFMPEG_TIMEOUT_SECONDS, deadline - now)
    cutoff = time.monotonic() + remaining
    require_time(cutoff, cancelled)
    command = [
        "/usr/bin/setpriv",
        "--pdeathsig",
        "KILL",
        "/usr/bin/prlimit",
        f"--as={MEMORY_LIMIT_BYTES}",
        f"--cpu={FFMPEG_CPU_LIMIT_SECONDS}",
        "--core=0",
        "/usr/bin/ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        "1",
        "-filter_threads",
        "1",
        "-protocol_whitelist",
        "pipe",
        "-i",
        "pipe:0",
        "-t",
        str(MAX_AUDIO_SECONDS + 1),
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "f32le",
        "pipe:1",
    ]
    try:
        process = subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_decoder_environment(),
            start_new_session=True,
            bufsize=0,
        )
        try:
            pcm = exchange_decoder(process, audio_data, MAX_PCM_BYTES, cutoff, cancelled)
        finally:
            stop_process(process)
    except OSError, subprocess.TimeoutExpired:
        raise SpeechRecognitionError(SpeechFailure.DECODE_FAILED) from None
    if len(pcm) % 4:
        raise SpeechRecognitionError(SpeechFailure.DECODE_FAILED)
    return pcm


@cache
def _slaney_mel_filterbank() -> np.ndarray:
    """Build the 80-bin Slaney-normalized Whisper mel filterbank."""
    f_min = 0.0
    f_max = SAMPLE_RATE / 2.0
    f_sp = 200.0 / 3.0
    min_log_hz = 1000.0
    min_log_mel = (min_log_hz - f_min) / f_sp
    logstep = np.log(6.4) / 27.0

    def _hz_to_mel(freqs: np.ndarray) -> np.ndarray:
        mels = (freqs - f_min) / f_sp
        log_mask = freqs >= min_log_hz
        mels[log_mask] = min_log_mel + np.log(freqs[log_mask] / min_log_hz) / logstep
        return mels

    def _mel_to_hz(mels: np.ndarray) -> np.ndarray:
        freqs = f_min + f_sp * mels
        log_mask = mels >= min_log_mel
        freqs[log_mask] = min_log_hz * np.exp(logstep * (mels[log_mask] - min_log_mel))
        return freqs

    n_freqs = WHISPER_N_FFT // 2 + 1
    fft_freqs = np.linspace(0.0, f_max, n_freqs, dtype=np.float64)
    min_mel = float(_hz_to_mel(np.array([f_min]))[0])
    max_mel = float(_hz_to_mel(np.array([f_max]))[0])
    mel_points = np.linspace(min_mel, max_mel, WHISPER_N_MELS + 2, dtype=np.float64)
    hz_points = _mel_to_hz(mel_points)
    f_diff = np.diff(hz_points)
    ramps = hz_points[:, None] - fft_freqs[None, :]

    weights = np.zeros((WHISPER_N_MELS, n_freqs), dtype=np.float64)
    for idx in range(WHISPER_N_MELS):
        lower = -ramps[idx] / f_diff[idx]
        upper = ramps[idx + 2] / f_diff[idx + 1]
        weights[idx] = np.maximum(0.0, np.minimum(lower, upper))

    enorm = 2.0 / (hz_points[2 : WHISPER_N_MELS + 2] - hz_points[:WHISPER_N_MELS])
    weights *= enorm[:, None]
    return weights.astype(np.float32)


@cache
def _hann_window() -> np.ndarray:
    """Build a periodic Hann window matching Whisper STFT framing."""
    return np.hanning(WHISPER_N_FFT + 1)[:-1].astype(np.float32)


def _compute_log_mel_spectrogram(chunk: np.ndarray) -> ctranslate2.StorageView:
    """Convert a 30-second mono 16 kHz float32 chunk into CTranslate2 Whisper features."""
    if len(chunk) < WHISPER_CHUNK_SAMPLES:
        padded_audio = np.pad(chunk, (0, WHISPER_CHUNK_SAMPLES - len(chunk)))
    else:
        padded_audio = chunk[:WHISPER_CHUNK_SAMPLES]
    pad = WHISPER_N_FFT // 2
    reflected = np.pad(padded_audio, (pad, pad), mode="reflect")
    frames = np.lib.stride_tricks.sliding_window_view(reflected, WHISPER_N_FFT)[::WHISPER_HOP_LENGTH][:WHISPER_N_FRAMES]
    magnitudes = np.abs(np.fft.rfft(frames * _hann_window(), axis=-1)).astype(np.float32) ** 2
    mel_spec = _slaney_mel_filterbank() @ magnitudes.T
    log_spec = np.log10(np.maximum(mel_spec, 1e-10))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    log_spec = (log_spec + 4.0) / 4.0
    features = np.ascontiguousarray(log_spec[np.newaxis, :, :], dtype=np.float32)
    return ctranslate2.StorageView.from_array(features)


@cache
def _gpt2_unicode_to_bytes() -> dict[str, int]:
    """Return the inverse GPT-2 byte-level BPE character-to-byte table."""
    ascii_printable = list(range(ord("!"), ord("~") + 1))
    latin_upper = list(range(ord("¡"), ord("¬") + 1))
    latin_ext = list(range(ord("®"), ord("ÿ") + 1))
    byte_values = ascii_printable + latin_upper + latin_ext
    codepoints = byte_values[:]
    offset = 0
    for byte in range(256):
        if byte not in byte_values:
            byte_values.append(byte)
            codepoints.append(256 + offset)
            offset += 1
    return {chr(cp): byte for byte, cp in zip(byte_values, codepoints, strict=True)}


def _decode_token_ids(token_ids: Sequence[int], vocabulary: Sequence[str]) -> str:
    """Decode generated Whisper token IDs using vocabulary.txt and GPT-2 byte-level BPE."""
    unicode_to_byte = _gpt2_unicode_to_bytes()
    pieces = [
        vocabulary[token_id] for token_id in token_ids if 0 <= token_id < min(SPECIAL_TOKEN_START_ID, len(vocabulary))
    ]
    joined = "".join(pieces)
    raw_bytes = bytes(unicode_to_byte[ch] for ch in joined if ch in unicode_to_byte)
    return raw_bytes.decode("utf-8", errors="replace").strip()


def _load_ctranslate2_whisper(model_dir: Path) -> WhisperModelProtocol:
    """Load a CTranslate2 INT8 Whisper model on CPU with bounded threads."""
    return ctranslate2.models.Whisper(
        str(model_dir),
        device="cpu",
        compute_type="int8",
        inter_threads=INFERENCE_INTER_THREADS,
        intra_threads=INFERENCE_INTRA_THREADS,
    )


class CTranslate2WhisperEngine(SpeechRecognitionPort):
    """Run local INT8 Whisper speech recognition via CTranslate2 with idle unloading."""

    def __init__(
        self,
        store: SpeechModelStore,
        *,
        idle_timeout_seconds: int = 300,
        clock: Clock | None = None,
        model_factory: Callable[[Path], WhisperModelProtocol] | None = None,
    ) -> None:
        SpeechLimits(model_idle_seconds=idle_timeout_seconds)
        self._store = store
        self._idle_timeout_seconds = idle_timeout_seconds
        self._clock = clock or WallClock()
        self._model_factory = model_factory or _load_ctranslate2_whisper
        self._owner = os.getpid()
        self._model: WhisperModelProtocol | None = None
        self._vocabulary: list[str] | None = None
        self._last_used_at: float | None = None
        self._closed = False

    def recognize(
        self, audio: bytes, deadline: float, *, cancelled: Callable[[], bool] = never_cancelled
    ) -> SpeechResult:
        """Decode and recognize audio using CTranslate2 Whisper within deadline.

        Args:
            audio: Encoded audio bytes.
            deadline: Original Unix execution deadline.
            cancelled: Live request cancellation predicate.

        Returns:
            Transcript or NoSpeech result.

        Raises:
            SpeechRecognitionError: If cancelled, timed out, or operational failure occurs.
        """
        self._verify_active_ownership()
        cutoff = time.monotonic() + deadline - self._clock.now()
        require_time(cutoff, cancelled)
        pcm = decode_audio_to_pcm(audio, deadline, cancelled=cancelled, clock=self._clock)
        try:
            return self._recognize_pcm(pcm, cutoff, cancelled)
        except SpeechRecognitionError:
            raise
        except OSError, RuntimeError:
            self._unload_model()
            raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED) from None

    def unload_idle(self) -> bool:
        """Unload the Whisper model when idle past the configured timeout.

        Returns:
            True if a loaded model was unloaded, False otherwise.
        """
        self._verify_active_ownership()
        if self._model is None or self._last_used_at is None:
            return False
        if self._clock.now() - self._last_used_at < self._idle_timeout_seconds:
            return False
        self._unload_model()
        return True

    def close(self) -> None:
        """Unload model resources in the owning process; reject inherited access.

        Raises:
            ValueError: If called from another process.
        """
        if os.getpid() != self._owner:
            msg = "Engine cannot be shared across processes"
            raise ValueError(msg)
        self._unload_model()
        self._closed = True

    def _verify_active_ownership(self) -> None:
        if self._closed or os.getpid() != self._owner:
            msg = "Engine is closed or accessed from another process"
            raise ValueError(msg)

    def _load(self) -> tuple[WhisperModelProtocol, list[str]]:
        if self._model is not None and self._vocabulary is not None:
            return self._model, self._vocabulary
        model_dir = self._store.require_ready()
        vocab_path = model_dir / "vocabulary.txt"
        if not vocab_path.is_file():
            raise SpeechRecognitionError(SpeechFailure.MODEL_UNAVAILABLE)
        vocabulary = vocab_path.read_text(encoding="utf-8").splitlines()
        try:
            model = self._model_factory(model_dir)
        except OSError, RuntimeError:
            raise SpeechRecognitionError(SpeechFailure.MODEL_UNAVAILABLE) from None
        self._model = model
        self._vocabulary = vocabulary
        self._last_used_at = self._clock.now()
        return model, vocabulary

    def _unload_model(self) -> None:
        model = self._model
        self._model = None
        self._vocabulary = None
        self._last_used_at = None
        if model is not None:
            model.unload_model()

    def _recognize_pcm(self, pcm: bytes, cutoff: float, cancelled: Callable[[], bool]) -> SpeechResult:
        require_time(cutoff, cancelled)
        samples = np.frombuffer(pcm, dtype=np.float32)
        if samples.size == 0:
            return NoSpeech()
        model, vocabulary = self._load()
        language_token: str | None = None
        detected_language: str | None = None
        segments: list[str] = []
        for start in range(0, int(samples.size), WHISPER_CHUNK_SAMPLES):
            require_time(cutoff, cancelled)
            features = _compute_log_mel_spectrogram(samples[start : start + WHISPER_CHUNK_SAMPLES])
            if language_token is None:
                language_token, detected_language = self._detect_language(model, features)
            require_time(cutoff, cancelled)
            prompt = ["<|startoftranscript|>", language_token, "<|transcribe|>", "<|notimestamps|>"]
            results = model.generate(features, [prompt], beam_size=BEAM_SIZE, return_no_speech_prob=True)
            if not results:
                continue
            result = results[0]
            if result.no_speech_prob > NO_SPEECH_THRESHOLD:
                continue
            token_ids = result.sequences_ids[0] if result.sequences_ids else ()
            chunk_text = _decode_token_ids(token_ids, vocabulary)
            if chunk_text:
                segments.append(chunk_text)

        self._last_used_at = self._clock.now()
        full_text = " ".join(segments).strip()
        if not full_text:
            return NoSpeech()
        if len(full_text) > MAX_TRANSCRIPT_CHARS:
            full_text = full_text[: MAX_TRANSCRIPT_CHARS - len(TRUNCATION_NOTICE)] + TRUNCATION_NOTICE
        return Transcript(text=full_text, language=detected_language)

    def _detect_language(
        self, model: WhisperModelProtocol, features: ctranslate2.StorageView
    ) -> tuple[str, str | None]:
        detections = model.detect_language(features)
        if not detections or not detections[0]:
            raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED)
        lang_token = detections[0][0][0]
        if lang_token.startswith("<|") and lang_token.endswith("|>"):
            return lang_token, lang_token[2:-2]
        return lang_token, None
