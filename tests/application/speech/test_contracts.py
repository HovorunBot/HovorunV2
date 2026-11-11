"""Verify typed speech outcomes, private representations and safe failures."""

from typing import cast

import pytest

from hovorunv2.application.speech.contracts import (
    MAX_TRANSCRIPT_CHARS,
    NoSpeech,
    SpeechFailure,
    SpeechRecognitionError,
    Transcript,
)

pytestmark = pytest.mark.unit


def test_ukrainian_transcript_preserves_text_and_hides_it_from_representations() -> None:
    """Keep Unicode content private while normalizing language identity."""
    text = "Синтетичний український приклад."
    transcript = Transcript(text, "UK")
    assert transcript.text == text
    assert transcript.language == "uk"
    assert text not in repr(transcript)
    assert text not in str(transcript)
    assert NoSpeech() != transcript


@pytest.mark.parametrize("text", ["", " \n ", "x" * (MAX_TRANSCRIPT_CHARS + 1)])
def test_unusable_or_unbounded_transcripts_raise_instead_of_returning_sentinels(text: str) -> None:
    """Require usable bounded text before it enters the speech result contract."""
    with pytest.raises(ValueError, match=r"transcript|Transcript"):
        Transcript(text)


@pytest.mark.parametrize("language", [None, "und"])
def test_successful_text_can_have_genuinely_unknown_language(language: str | None) -> None:
    """Keep detection uncertainty separate from recognition failure."""
    assert Transcript("Synthetic sample", language).language is None


@pytest.mark.parametrize("reason", list(SpeechFailure))
def test_operational_errors_contain_only_allowlisted_reasons(reason: SpeechFailure) -> None:
    """Expose no raw backend exception, audio or transcript in error representations."""
    error = SpeechRecognitionError(reason)
    assert error.reason is reason
    assert error.args == (f"Speech recognition failed: {reason.value}.",)


def test_untyped_failure_reason_is_rejected_without_echoing_it() -> None:
    """Prevent a backend error string from being used as a safe reason code."""
    with pytest.raises(TypeError, match="allowlisted"):
        SpeechRecognitionError(cast(SpeechFailure, "untrusted backend details"))
