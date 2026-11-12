"""Synchronous whisper.cpp speech recognition engine using an anonymous-pipe helper."""

import json
import os
import select
import struct
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

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
from hovorunv2.infrastructure.speech.model_store import SpeechModelStore
from hovorunv2.infrastructure.speech.process_io import (
    IO_POLL_SECONDS,
    PIPE_CHUNK_BYTES,
    exchange_decoder,
    never_cancelled,
    require_time,
    stop_process,
)

SAMPLE_RATE = 16_000
MAX_AUDIO_SECONDS = 120
MAX_PCM_BYTES = SAMPLE_RATE * MAX_AUDIO_SECONDS * 4
MAX_COMPRESSED_AUDIO_BYTES = 10_000_000
MAX_OUTPUT_BYTES = 128_000
HELPER_PROTOCOL_VERSION = 2
ACCEPTANCE_ATTEMPTS = 2
TRUNCATION_NOTICE = "\n[Transcript truncated at the output limit.]"
MEMORY_LIMIT_BYTES = 2 * 1024**3
FFMPEG_TIMEOUT_SECONDS = 15


def _decoder_environment() -> dict[str, str]:
    """Pass minimal environment variables without exposing application secrets."""
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "OMP_NUM_THREADS": "2",
        "OPENBLAS_NUM_THREADS": "2",
    }


def decode_audio_to_pcm(
    audio_data: bytes, deadline: float | None = None, *, cancelled: Callable[[], bool] = never_cancelled
) -> bytes:
    """Decode private audio in an isolated, byte/time-bounded FFmpeg child.

    Args:
        audio_data: Bounded compressed audio.
        deadline: Optional Unix request deadline; standalone decoding retains its fixed decoder timeout.
        cancelled: Current cancellation predicate.

    Returns:
        Bounded mono 16 kHz float32 PCM.

    Raises:
        SpeechRecognitionError: If decoding, authorization, input or time bounds fail.
    """
    if not audio_data or len(audio_data) > MAX_COMPRESSED_AUDIO_BYTES:
        raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
    remaining = FFMPEG_TIMEOUT_SECONDS if deadline is None else min(FFMPEG_TIMEOUT_SECONDS, deadline - time.time())
    cutoff = time.monotonic() + remaining
    require_time(cutoff, cancelled)
    command = [
        "/usr/bin/setpriv",
        "--pdeathsig",
        "KILL",
        "/usr/bin/prlimit",
        f"--as={MEMORY_LIMIT_BYTES}",
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


class WhisperCppEngine(SpeechRecognitionPort):
    """Manage a supervised local whisper.cpp helper process over anonymous pipes."""

    def __init__(
        self,
        store: SpeechModelStore,
        *,
        helper_path: Path | None = None,
        idle_timeout_seconds: int = 300,
    ) -> None:
        SpeechLimits(model_idle_seconds=idle_timeout_seconds)
        self._store = store
        self._helper_path = helper_path or (Path(__file__).parent / "whisper_helper")
        self._idle_timeout_seconds = idle_timeout_seconds
        self._owner = os.getpid()
        self._process: subprocess.Popen[bytes] | None = None
        self._closed = False

    def recognize(
        self, audio: bytes, deadline: float, *, cancelled: Callable[[], bool] = never_cancelled
    ) -> SpeechResult:
        """Decode and recognize audio using the supervised whisper helper within deadline.

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
        cutoff = time.monotonic() + deadline - time.time()
        require_time(cutoff, cancelled)
        try:
            pcm = decode_audio_to_pcm(audio, deadline, cancelled=cancelled)
            return self._recognize_pcm(pcm, cutoff, cancelled)
        except SpeechRecognitionError:
            self._cleanup_helper()
            raise
        except OSError:
            self._cleanup_helper()
            raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED) from None
        except Exception:
            self._cleanup_helper()
            raise

    def _verify_active_ownership(self) -> None:
        if self._closed or os.getpid() != self._owner:
            msg = "Engine is closed or accessed from another process"
            raise ValueError(msg)

    def _ensure_helper(self, deadline: float, cancelled: Callable[[], bool]) -> subprocess.Popen[bytes]:
        if self._process is not None and self._process.poll() is None:
            return self._process

        self._cleanup_helper()
        require_time(deadline, cancelled)
        model_dir = self._store.require_ready()
        model_path = model_dir / "ggml-small-q8_0.bin"
        if not model_path.is_file():
            raise SpeechRecognitionError(SpeechFailure.MODEL_UNAVAILABLE)

        available = sorted(os.sched_getaffinity(0))[:2]
        command = [
            "/usr/bin/prlimit",
            f"--as={MEMORY_LIMIT_BYTES}",
            "--core=0",
            "/usr/bin/taskset",
            "-c",
            ",".join(map(str, available)),
            str(self._helper_path),
            str(model_path),
            str(self._idle_timeout_seconds * 1000),
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
        except OSError:
            raise SpeechRecognitionError(SpeechFailure.MODEL_UNAVAILABLE) from None

        self._process = process
        ready_payload = self._read_result_payload(deadline, cancelled)
        if (
            ready_payload.get("status") != "ready"
            or ready_payload.get("network_blocked") is not True
            or ready_payload.get("protocol") != HELPER_PROTOCOL_VERSION
        ):
            self._cleanup_helper()
            raise SpeechRecognitionError(SpeechFailure.MODEL_UNAVAILABLE)
        return process

    def _accept_request(self, deadline: float, cancelled: Callable[[], bool]) -> subprocess.Popen[bytes]:
        """Replace an idle-exited helper only before any private audio has been dispatched."""
        for attempt in range(ACCEPTANCE_ATTEMPTS):
            try:
                helper = self._request_acceptance(deadline, cancelled)
            except OSError:
                self._cleanup_helper()
                if attempt + 1 == ACCEPTANCE_ATTEMPTS:
                    raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED) from None
            except SpeechRecognitionError as error:
                self._cleanup_helper()
                if error.reason != SpeechFailure.INFERENCE_FAILED or attempt + 1 == ACCEPTANCE_ATTEMPTS:
                    raise
            else:
                return helper
        raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED)

    def _request_acceptance(self, deadline: float, cancelled: Callable[[], bool]) -> subprocess.Popen[bytes]:
        helper = self._ensure_helper(deadline, cancelled)
        if helper.stdin is None:
            raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED)
        require_time(deadline, cancelled)
        os.write(helper.stdin.fileno(), struct.pack("<I", 0))
        response = self._read_result_payload(deadline, cancelled)
        if response.get("status") != "accepted":
            raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED)
        return helper

    def _recognize_pcm(self, pcm: bytes, deadline: float, cancelled: Callable[[], bool]) -> SpeechResult:
        helper = self._accept_request(deadline, cancelled)
        if helper.stdin is None:
            self._cleanup_helper()
            raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED)

        payload = struct.pack("<I", len(pcm)) + pcm
        offset = 0
        while offset < len(payload):
            require_time(deadline, cancelled)
            if select.select([], [helper.stdin], [], IO_POLL_SECONDS)[1]:
                offset += os.write(helper.stdin.fileno(), payload[offset : offset + PIPE_CHUNK_BYTES])

        result_dict = self._read_result_payload(deadline, cancelled)
        return self._translate_result(result_dict)

    def _read_exact(self, length: int, deadline: float, cancelled: Callable[[], bool]) -> bytes:
        if self._process is None or self._process.stdout is None:
            raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED)
        output = bytearray()
        while len(output) < length:
            require_time(deadline, cancelled)
            if not select.select([self._process.stdout], [], [], IO_POLL_SECONDS)[0]:
                if self._process.poll() is not None:
                    self._cleanup_helper()
                    raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED)
                continue
            chunk = os.read(self._process.stdout.fileno(), length - len(output))
            if not chunk:
                self._cleanup_helper()
                raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED)
            output.extend(chunk)
        return bytes(output)

    def _read_result_payload(self, deadline: float, cancelled: Callable[[], bool]) -> dict[str, object]:
        raw_len = self._read_exact(4, deadline, cancelled)
        length = struct.unpack("<I", raw_len)[0]
        if length > MAX_OUTPUT_BYTES:
            self._cleanup_helper()
            raise SpeechRecognitionError(SpeechFailure.OUTPUT_LIMIT)
        body = self._read_exact(length, deadline, cancelled)
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError, UnicodeDecodeError:
            self._cleanup_helper()
            raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED) from None
        if not isinstance(payload, dict):
            self._cleanup_helper()
            raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED)
        return payload

    def _translate_result(self, payload: dict[str, object]) -> SpeechResult:
        status = payload.get("status")
        if status == "no_speech":
            return NoSpeech()
        if status == "transcript":
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED)
            text = text.strip()
            if len(text) > MAX_TRANSCRIPT_CHARS:
                text = text[: MAX_TRANSCRIPT_CHARS - len(TRUNCATION_NOTICE)] + TRUNCATION_NOTICE
            raw_lang = payload.get("language")
            if raw_lang is not None and not isinstance(raw_lang, str):
                raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED)
            language = raw_lang
            return Transcript(text=text, language=language)
        if status == "invalid_audio":
            raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
        if status == "output_limit":
            raise SpeechRecognitionError(SpeechFailure.OUTPUT_LIMIT)
        raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED)

    def _cleanup_helper(self) -> None:
        process = self._process
        self._process = None
        if process is not None:
            stop_process(process)

    def close(self) -> None:
        """Stop and reap the helper in its owning process; reject inherited access.

        Raises:
            ValueError: If called from another process.
        """
        if os.getpid() != self._owner:
            msg = "Engine cannot be shared across processes"
            raise ValueError(msg)
        self._cleanup_helper()
        self._closed = True
