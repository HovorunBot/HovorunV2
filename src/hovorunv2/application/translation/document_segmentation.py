"""Pure paragraph, sentence, and token-aware document segmentation for whole-document translation."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from hovorunv2.application.translation.ports import TranslationError

DEFAULT_GOOGLE_SEGMENT_CHARS: Final = 4000
DEFAULT_LOCAL_SEGMENT_TOKENS: Final = 128
MAX_DOCUMENT_CHARACTERS: Final = 65536
MAX_SEGMENT_COUNT: Final = 256
MAX_EXPANSION_RATIO: Final = 6.0

_SENTENCE_SPLIT_RE: Final = re.compile(r"(?<=[.!?…;])\s+")


@dataclass(frozen=True, slots=True)
class DocumentLine:
    """One original line in a document with its segmented translatable pieces."""

    raw_line: str
    segments: tuple[str, ...]
    separators: tuple[str, ...]


def _measure_units(text: str, measure_fn: Callable[[str], int] | None) -> int:
    return len(text) if measure_fn is None else measure_fn(text)


def _split_oversized_piece(
    text: str,
    max_units: int,
    measure_fn: Callable[[str], int] | None,
) -> tuple[list[str], list[str]]:
    words = text.split(" ")
    if len(words) == 1:
        return _split_single_token(text, max_units, measure_fn)

    segments: list[str] = []
    separators: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if _measure_units(candidate, measure_fn) <= max_units:
            current = candidate
            continue
        if current:
            segments.append(current)
            separators.append(" ")
        if _measure_units(word, measure_fn) <= max_units:
            current = word
        else:
            token_parts, token_seps = _split_single_token(word, max_units, measure_fn)
            segments.extend(token_parts[:-1])
            separators.extend(token_seps)
            current = token_parts[-1]
    if current:
        segments.append(current)
    return segments, separators


def _split_single_token(
    token: str,
    max_units: int,
    measure_fn: Callable[[str], int] | None,
) -> tuple[list[str], list[str]]:
    if _measure_units(token, measure_fn) <= max_units:
        return [token], []
    parts: list[str] = []
    separators: list[str] = []
    start = 0
    while start < len(token):
        end = len(token)
        while end > start + 1 and _measure_units(token[start:end], measure_fn) > max_units:
            end = start + max(1, (end - start) // 2)
        while end < len(token) and _measure_units(token[start : end + 1], measure_fn) <= max_units:
            end += 1
        parts.append(token[start:end])
        if end < len(token):
            separators.append("")
        start = end
    return parts, separators


def split_line_into_segments(
    line: str,
    max_units: int,
    *,
    measure_fn: Callable[[str], int] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split a non-empty line into bounded segments at sentence, word, or token boundaries.

    Args:
        line: Non-blank line text.
        max_units: Maximum units (characters or tokens) per segment.
        measure_fn: Optional measurement function; defaults to character count.

    Returns:
        Tuple of ``(segments, join_separators)`` preserving source order.
    """
    if _measure_units(line, measure_fn) <= max_units:
        return (line,), ()

    sentences = [part for part in _SENTENCE_SPLIT_RE.split(line) if part]
    segments: list[str] = []
    separators: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = sentence if not current else f"{current} {sentence}"
        if _measure_units(candidate, measure_fn) <= max_units:
            current = candidate
            continue
        if current:
            segments.append(current)
            separators.append(" ")
            current = ""
        if _measure_units(sentence, measure_fn) <= max_units:
            current = sentence
            continue
        sub_parts, sub_seps = _split_oversized_piece(sentence, max_units, measure_fn)
        segments.extend(sub_parts[:-1])
        separators.extend(sub_seps)
        current = sub_parts[-1]
    if current:
        segments.append(current)
    return tuple(segments), tuple(separators[: max(0, len(segments) - 1)])


def plan_document_segments(
    text: str,
    max_units: int,
    *,
    measure_fn: Callable[[str], int] | None = None,
) -> list[DocumentLine]:
    """Plan line- and segment-level translation units while preserving blank lines.

    Args:
        text: Complete document text.
        max_units: Maximum units per segment.
        measure_fn: Optional measurement function; defaults to character count.

    Returns:
        Ordered list of ``DocumentLine`` records covering the full document.

    Raises:
        ValueError: If the document exceeds ``MAX_DOCUMENT_CHARACTERS``.
        TranslationError: If segmentation exceeds ``MAX_SEGMENT_COUNT``.
    """
    if len(text) > MAX_DOCUMENT_CHARACTERS:
        msg = "Document exceeds maximum character limit"
        raise ValueError(msg)

    planned: list[DocumentLine] = []
    total_segments = 0
    for raw_line in text.split("\n"):
        if not raw_line.strip():
            planned.append(DocumentLine(raw_line=raw_line, segments=(), separators=()))
            continue
        segments, separators = split_line_into_segments(raw_line, max_units, measure_fn=measure_fn)
        total_segments += len(segments)
        if total_segments > MAX_SEGMENT_COUNT:
            msg = "Document exceeds maximum segment count"
            raise TranslationError(msg)
        planned.append(DocumentLine(raw_line=raw_line, segments=segments, separators=separators))
    return planned


def assemble_translated_document(
    planned: list[DocumentLine],
    translated_segments: list[str],
) -> str:
    """Reassemble a complete document from all-success translated segments.

    Args:
        planned: Ordered document line plan from ``plan_document_segments``.
        translated_segments: Translated output for each non-empty segment in order.

    Returns:
        Complete reassembled document preserving original blank lines and order.

    Raises:
        TranslationError: If segment counts mismatch, any segment is empty, or expansion is unbounded.
    """
    expected = sum(len(line.segments) for line in planned)
    if len(translated_segments) != expected:
        msg = "Translated segment count mismatch"
        raise TranslationError(msg)

    cursor = 0
    rendered_lines: list[str] = []
    for line in planned:
        if not line.segments:
            rendered_lines.append(line.raw_line)
            continue
        count = len(line.segments)
        pieces = translated_segments[cursor : cursor + count]
        source_pieces = line.segments
        cursor += count
        cleaned: list[str] = []
        for source_piece, piece in zip(source_pieces, pieces, strict=True):
            stripped = piece.strip()
            if not stripped:
                msg = "Translation produced an empty segment"
                raise TranslationError(msg)
            if len(source_piece) > 0 and len(stripped) > max(256, int(len(source_piece) * MAX_EXPANSION_RATIO)):
                msg = "Translation output exceeded bounded expansion"
                raise TranslationError(msg)
            cleaned.append(stripped)
        if len(cleaned) == 1:
            rendered_lines.append(cleaned[0])
            continue
        joined = cleaned[0]
        for sep, part in zip(line.separators, cleaned[1:], strict=True):
            joined = f"{joined}{sep}{part}"
        rendered_lines.append(joined)
    return "\n".join(rendered_lines)
