"""Define private speech results and safe operational failure reasons."""

from dataclasses import dataclass, field
from enum import StrEnum

from hovorunv2.domain.languages import UNDETERMINED_LANGUAGE, normalize_language_code

MAX_TRANSCRIPT_CHARS = 32_000


@dataclass(frozen=True, slots=True)
class Transcript:
    """Carry nonempty private text without including its contents in representations."""

    text: str = field(repr=False)
    language: str | None = None

    def __post_init__(self) -> None:
        """Validate usable text and normalize genuinely optional language metadata.

        Raises:
            ValueError: If text is blank, exceeds the output limit, or has an invalid language tag.
        """
        if not self.text.strip():
            msg = "A transcript requires nonempty text."
            raise ValueError(msg)
        if len(self.text) > MAX_TRANSCRIPT_CHARS:
            msg = "Transcript exceeds the speech output limit."
            raise ValueError(msg)
        if self.language is not None:
            language = normalize_language_code(self.language)
            object.__setattr__(self, "language", None if language == UNDETERMINED_LANGUAGE else language)


@dataclass(frozen=True, slots=True)
class NoSpeech:
    """Represent a successfully processed recording with no usable speech."""


type SpeechResult = Transcript | NoSpeech


class SpeechFailure(StrEnum):
    """Allow only operational codes in exceptions, diagnostics and user feedback."""

    INVALID_INPUT = "invalid_input"
    DOWNLOAD_FAILED = "download_failed"
    DECODE_FAILED = "decode_failed"
    MODEL_UNAVAILABLE = "model_unavailable"
    INFERENCE_FAILED = "inference_failed"
    OUTPUT_LIMIT = "output_limit"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CANCELLED = "cancelled"


class SpeechRecognitionError(Exception):
    """Report recognition failure without retaining raw input or dependency error values."""

    def __init__(self, reason: SpeechFailure) -> None:
        """Require a fixed reason and construct a safe exception message.

        Args:
            reason: Allowlisted operational failure code.

        Raises:
            TypeError: If the reason is not a typed speech failure code.
        """
        if not isinstance(reason, SpeechFailure):
            msg = "Speech failure requires an allowlisted reason."
            raise TypeError(msg)
        self.reason = reason
        super().__init__(f"Speech recognition failed: {reason.value}.")


class SpeechCleanupUncertainError(Exception):
    """Retain execution ownership when private helper termination cannot be proven."""

    def __init__(self) -> None:
        super().__init__("Speech process cleanup could not be verified.")
