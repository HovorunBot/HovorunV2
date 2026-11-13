"""Verify paragraph, sentence, and token-aware document segmentation and atomic assembly."""

import pytest

from hovorunv2.application.translation.document_segmentation import (
    MAX_DOCUMENT_CHARACTERS,
    assemble_translated_document,
    plan_document_segments,
)
from hovorunv2.application.translation.ports import TranslationError

pytestmark = pytest.mark.unit


def test_plan_and_assemble_preserves_blank_lines_and_sentence_order() -> None:
    """Split long paragraphs at sentence boundaries and preserve blank lines on reassembly."""
    document = "First sentence. Second sentence. Third sentence.\n\nFinal paragraph."
    planned = plan_document_segments(document, 32)
    assert len(planned) == 3
    assert len(planned[0].segments) == 2
    assert planned[1].segments == ()
    assert planned[2].segments == ("Final paragraph.",)

    translated = ["Перше. Друге.", "Третє.", "Фінальний абзац."]
    assert assemble_translated_document(planned, translated).splitlines() == [
        "Перше. Друге. Третє.",
        "",
        "Фінальний абзац.",
    ]


def test_custom_token_counter_and_unbroken_token_split() -> None:
    """Honor custom token-counting callbacks and split unbroken tokens cleanly."""
    planned = plan_document_segments("abcdefghij", 4, measure_fn=lambda text: len(text) * 2)
    assert [segment for line in planned for segment in line.segments] == ["ab", "cd", "ef", "gh", "ij"]


def test_assemble_rejects_empty_segments_mismatches_and_unbounded_expansion() -> None:
    """Reject partial, empty, or runaway-expanded segment outputs."""
    planned = plan_document_segments("Short line.", 50)
    with pytest.raises(TranslationError, match="mismatch"):
        assemble_translated_document(planned, [])
    with pytest.raises(TranslationError, match="empty segment"):
        assemble_translated_document(planned, ["   "])
    with pytest.raises(TranslationError, match="bounded expansion"):
        assemble_translated_document(planned, ["X" * 300])

    with pytest.raises(ValueError, match="character limit"):
        plan_document_segments("A" * (MAX_DOCUMENT_CHARACTERS + 1), 100)


def test_consecutive_oversized_sentences_preserve_token_join_separators() -> None:
    """Keep mid-token empty separators aligned when consecutive oversized sentences are split."""
    text = "abcdefghij. klmnopqrst."
    planned = plan_document_segments(text, 5)
    flat_segments = [segment for line in planned for segment in line.segments]
    assert assemble_translated_document(planned, flat_segments) == text
