"""Synchronous whisper.cpp speech recognition engine using an anonymous-pipe helper."""

import json
import os
import select
import signal
import struct
import subprocess
import time
from pathlib import Path

from hovorunv2.application.speech.contracts import (
    MAX_TRANSCRIPT_CHARS,
    NoSpeech,
    SpeechFailure,
    SpeechRecognitionError,
    SpeechResult,
    Transcript,
)
from hovorunv2.application.speech.ports import SpeechRecognitionPort
from hovorunv2.infrastructure.speech.model_store import SpeechModelStore

SAMPLE_RATE = 16_000
MAX_AUDIO_SECONDS = 120
MAX_PCM_BYTES = SAMPLE_RATE * MAX_AUDIO_SECONDS * 4
MAX_COMPRESSED_AUDIO_BYTES = 10_000_000
MAX_OUTPUT_BYTES = 128_000
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


def decode_audio_to_pcm(audio_data: bytes) -> bytes:
    """Decode untrusted audio bytes to 16kHz mono 32-bit float PCM via bounded FFmpeg.

    Args:
        audio_data: Compressed input audio bytes.

    Returns:
        Raw 32-bit float PCM bytes.

    Raises:
        SpeechRecognitionError: If input is invalid, oversized, or decoding fails.
    """
    if not audio_data:
        raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
    if len(audio_data) > MAX_COMPRESSED_AUDIO_BYTES:
        raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)

    command = [
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
        result = subprocess.run(  # noqa: S603
            command,
            input=audio_data,
            capture_output=True,
            env=_decoder_environment(),
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise SpeechRecognitionError(SpeechFailure.DECODE_FAILED) from None
    except OSError:
        raise SpeechRecognitionError(SpeechFailure.DECODE_FAILED) from None

    if result.returncode != 0:
        raise SpeechRecognitionError(SpeechFailure.DECODE_FAILED)
    pcm = result.stdout
    if len(pcm) > MAX_PCM_BYTES:
        raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
    if not pcm or len(pcm) % 4 != 0:
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
        self._store = store
        self._helper_path = helper_path or (Path(__file__).parent / "whisper_helper")
        self._idle_timeout_seconds = idle_timeout_seconds
        self._owner = os.getpid()
        self._process: subprocess.Popen[bytes] | None = None
        self._closed = False

    def recognize(self, audio: bytes, deadline: float) -> SpeechResult:
        """Decode and recognize audio using the supervised whisper helper within deadline.

        Args:
            audio: Encoded audio bytes.
            deadline: Absolute monotonic deadline timestamp.

        Returns:
            Transcript or NoSpeech result.

        Raises:
            SpeechRecognitionError: If cancelled, timed out, or operational failure occurs.
        """
        self._verify_active_ownership()
        if time.monotonic() >= deadline:
            raise SpeechRecognitionError(SpeechFailure.DEADLINE_EXCEEDED)

        pcm = decode_audio_to_pcm(audio)
        return self._recognize_pcm(pcm, deadline)

    def _verify_active_ownership(self) -> None:
        if self._closed or os.getpid() != self._owner:
            msg = "Engine is closed or accessed from another process"
            raise ValueError(msg)

    def _ensure_helper(self) -> subprocess.Popen[bytes]:
        if self._process is not None and self._process.poll() is None:
            return self._process

        self._cleanup_helper()
        model_dir = self._store.ensure()
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
        ready_payload = self._read_result_payload(time.monotonic() + 30)
        if ready_payload.get("status") != "ready":
            self._cleanup_helper()
            raise SpeechRecognitionError(SpeechFailure.MODEL_UNAVAILABLE)
        return process

    def _recognize_pcm(self, pcm: bytes, deadline: float) -> SpeechResult:
        helper = self._ensure_helper()
        if helper.stdin is None:
            self._cleanup_helper()
            raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED)

        payload = struct.pack("<I", len(pcm)) + pcm
        offset = 0
        while offset < len(payload):
            if time.monotonic() >= deadline:
                self._cleanup_helper()
                raise SpeechRecognitionError(SpeechFailure.DEADLINE_EXCEEDED)
            if select.select([], [helper.stdin], [], 0.05)[1]:
                offset += os.write(helper.stdin.fileno(), payload[offset : offset + 4096])

        result_dict = self._read_result_payload(deadline)
        return self._translate_result(result_dict)

    def _read_exact(self, length: int, deadline: float) -> bytes:
        if self._process is None or self._process.stdout is None:
            raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED)
        output = bytearray()
        while len(output) < length:
            if time.monotonic() >= deadline:
                self._cleanup_helper()
                raise SpeechRecognitionError(SpeechFailure.DEADLINE_EXCEEDED)
            if not select.select([self._process.stdout], [], [], 0.05)[0]:
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

    def _read_result_payload(self, deadline: float) -> dict[str, object]:
        raw_len = self._read_exact(4, deadline)
        length = struct.unpack("<I", raw_len)[0]
        if length > MAX_OUTPUT_BYTES:
            self._cleanup_helper()
            raise SpeechRecognitionError(SpeechFailure.OUTPUT_LIMIT)
        body = self._read_exact(length, deadline)
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError, UnicodeDecodeError:
            self._cleanup_helper()
            raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED) from None

    def _translate_result(self, payload: dict[str, object]) -> SpeechResult:
        status = payload.get("status")
        if status == "no_speech":
            return NoSpeech()
        if status == "transcript":
            text = str(payload.get("text", "")).strip()
            if not text:
                return NoSpeech()
            if len(text) > MAX_TRANSCRIPT_CHARS:
                raise SpeechRecognitionError(SpeechFailure.OUTPUT_LIMIT)
            raw_lang = payload.get("language")
            language = str(raw_lang) if raw_lang is not None else None
            return Transcript(text=text, language=language)
        if status == "invalid_audio":
            raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
        if status == "output_limit":
            raise SpeechRecognitionError(SpeechFailure.OUTPUT_LIMIT)
        raise SpeechRecognitionError(SpeechFailure.INFERENCE_FAILED)

    def _cleanup_helper(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except OSError, subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
                except OSError:
                    pass
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()

    def close(self) -> None:
        """Shut down the helper process and mark engine closed."""
        if os.getpid() == self._owner:
            self._closed = True
            self._cleanup_helper()
