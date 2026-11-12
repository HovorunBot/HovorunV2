"""Bound private subprocess pipes and prove child termination before releasing work."""

import math
import os
import select
import signal
import subprocess
import time
from collections.abc import Callable

from hovorunv2.application.speech.contracts import SpeechCleanupUncertainError, SpeechFailure, SpeechRecognitionError

IO_POLL_SECONDS = 0.05
PIPE_CHUNK_BYTES = 4096
STOP_SECONDS = 2


def never_cancelled() -> bool:
    """Return the default cancellation state for standalone decoder probes."""
    return False


def require_time(deadline: float, cancelled: Callable[[], bool]) -> None:
    """Require a live monotonic deadline and authorization before further effects.

    Args:
        deadline: Process-local monotonic cutoff.
        cancelled: Current request cancellation predicate.

    Raises:
        SpeechRecognitionError: If work has expired or been cancelled.
    """
    if not math.isfinite(deadline) or time.monotonic() >= deadline:
        raise SpeechRecognitionError(SpeechFailure.DEADLINE_EXCEEDED)
    if cancelled():
        raise SpeechRecognitionError(SpeechFailure.CANCELLED)


def stop_process(process: subprocess.Popen[bytes]) -> None:
    """Stop and reap a private process group, then close its pipe endpoints.

    Args:
        process: Child process created with a new session.

    """
    try:
        if process.poll() is None:
            _signal_and_wait(process, signal.SIGTERM)
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _signal_and_wait(process: subprocess.Popen[bytes], first_signal: signal.Signals) -> None:
    try:
        os.killpg(process.pid, first_signal)
        process.wait(timeout=STOP_SECONDS)
    except ProcessLookupError:
        process.wait(timeout=STOP_SECONDS)
    except OSError, subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=STOP_SECONDS)
        except OSError, subprocess.TimeoutExpired:
            raise SpeechCleanupUncertainError from None


def exchange_decoder(
    process: subprocess.Popen[bytes], data: bytes, limit: int, deadline: float, cancelled: Callable[[], bool]
) -> bytes:
    """Stream bounded input/output concurrently and discard all child diagnostic output.

    Args:
        process: Owned decoder process with anonymous pipes.
        data: Bounded encoded audio input.
        limit: Maximum decoded output bytes.
        deadline: Monotonic execution cutoff.
        cancelled: Current cancellation predicate.

    Returns:
        Bounded decoded PCM bytes.

    Raises:
        SpeechRecognitionError: If the pipe protocol, bounds or decoder fails.
    """
    stdin, stdout = process.stdin, process.stdout
    if stdin is None or stdout is None:
        raise SpeechRecognitionError(SpeechFailure.DECODE_FAILED)
    output = bytearray()
    offset = 0
    while True:
        require_time(deadline, cancelled)
        readable, writable, _exceptional = select.select(
            [stdout], [stdin] if not stdin.closed else [], [], IO_POLL_SECONDS
        )
        if readable:
            chunk = os.read(stdout.fileno(), PIPE_CHUNK_BYTES)
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > limit:
                raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
        if writable:
            offset += os.write(stdin.fileno(), data[offset : offset + PIPE_CHUNK_BYTES])
            if offset == len(data):
                stdin.close()
    if process.wait(timeout=STOP_SECONDS) != 0 or not output:
        raise SpeechRecognitionError(SpeechFailure.DECODE_FAILED)
    return bytes(output)
