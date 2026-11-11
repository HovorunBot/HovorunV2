"""Bound speech work and derive trigger selection from a single chat mode."""

import math
from dataclasses import dataclass

from hovorunv2.application.speech.contracts import MAX_TRANSCRIPT_CHARS
from hovorunv2.domain.enums import CommandName, SpeechMode

MAX_AUDIO_SECONDS = 120
MAX_AUDIO_BYTES = 10_000_000
MAX_COMPUTE_THREADS = 2
DEFAULT_MODEL_IDLE_SECONDS = 300
SPEECH_POLICY = "speech"
SPEECH_BUCKET = "family:speech"


def speech_selected(mode: SpeechMode, feature: str) -> bool:
    """Resolve one speech feature from the three-state configuration.

    Args:
        mode: Stored chat speech mode.
        feature: Speech feature whose trigger is being evaluated.

    Returns:
        Whether the mode selects the requested feature.

    Raises:
        TypeError: If the mode is not typed.
        ValueError: If the feature does not belong to speech recognition.
    """
    if not isinstance(mode, SpeechMode):
        msg = "Speech selection requires a typed mode."
        raise TypeError(msg)
    if feature == CommandName.TRANSCRIBE:
        return mode != SpeechMode.OFF
    if feature == CommandName.TRANSCRIBE_AUTO:
        return mode == SpeechMode.MANUAL_AND_AUTOMATIC
    msg = "Speech selection requires a speech feature."
    raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SpeechLimits:
    """Validate operator limits against the qualified CPU operating envelope."""

    audio_seconds: int = MAX_AUDIO_SECONDS
    audio_bytes: int = MAX_AUDIO_BYTES
    transcript_chars: int = MAX_TRANSCRIPT_CHARS
    compute_threads: int = MAX_COMPUTE_THREADS
    model_idle_seconds: float = DEFAULT_MODEL_IDLE_SECONDS

    def __post_init__(self) -> None:
        """Reject unbounded inputs, unqualified resource increases and invalid idle intervals.

        Raises:
            ValueError: If a limit is invalid or exceeds its qualified bound.
        """
        limits = (
            (self.audio_seconds, MAX_AUDIO_SECONDS),
            (self.audio_bytes, MAX_AUDIO_BYTES),
            (self.transcript_chars, MAX_TRANSCRIPT_CHARS),
            (self.compute_threads, MAX_COMPUTE_THREADS),
        )
        for value, ceiling in limits:
            if type(value) is not int or not 0 < value <= ceiling:
                msg = "Speech limits require positive integers within the qualified bounds."
                raise ValueError(msg)
        if isinstance(self.model_idle_seconds, bool) or not math.isfinite(self.model_idle_seconds):
            msg = "Speech model idle timeout must be finite and positive."
            raise ValueError(msg)
        if self.model_idle_seconds <= 0:
            msg = "Speech model idle timeout must be finite and positive."
            raise ValueError(msg)
