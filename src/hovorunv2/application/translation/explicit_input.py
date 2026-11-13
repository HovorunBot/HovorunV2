"""Contracts and validation helpers for explicit /translate and button-triggered requests."""

import hashlib
from typing import Final

from pydantic import BaseModel, ConfigDict

from hovorunv2.application.data.languages import LANGUAGES
from hovorunv2.application.translation.ports import TranslationScope
from hovorunv2.application.translation.translation_rules import resolve_model_language
from hovorunv2.domain.languages import normalize_language_code

MAX_COMMAND_ARGUMENTS: Final = 1


class ExplicitTranslationInput(BaseModel):
    """Encrypted worker payload for an explicit user translation request."""

    model_config = ConfigDict(frozen=True)

    text: str
    scope: TranslationScope
    source_chat_id: int
    source_message_id: int
    reply_to_message_id: int
    message_thread_id: int | None = None
    requested_target_lang: str | None = None
    ui_locale: str = "uk"


def translate_action_key(
    chat_id: int,
    source_message_id: int,
    scope: TranslationScope,
    text: str,
    target_lang: str,
    entity_id: int,
    generation: int,
) -> str:
    """Build a privacy-preserving deduplication key shared by authorized group members.

    Args:
        chat_id: Telegram chat identifier.
        source_message_id: Replied source message identifier.
        scope: Selected quote or full source scope.
        text: Scoped text to translate (hashed, never stored in the key).
        target_lang: Resolved effective target language code.
        entity_id: Internal chat/user entity identifier.
        generation: Entity generation counter.

    Returns:
        Deterministic 64-character hex deduplication key for AdmissionService.
    """
    payload = f"translate:{chat_id}:{source_message_id}:{entity_id}:{generation}:{scope.value}:{target_lang}:{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_translate_command_target(raw_text: str) -> str | None:
    """Parse an optional single target-language argument from ``/translate [target]``.

    Args:
        raw_text: Raw Telegram command message text.

    Returns:
        Optional normalized target language string, or ``None`` when omitted.

    Raises:
        ValueError: If extra arguments or an unsupported target language are supplied.
    """
    parts = raw_text.strip().split()
    if len(parts) <= 1:
        return None
    if len(parts) > 1 + MAX_COMMAND_ARGUMENTS:
        msg = "Expected at most one target language argument"
        raise ValueError(msg)
    return validate_explicit_target_language(parts[1])


def validate_explicit_target_language(raw_target: str | None) -> str | None:
    """Validate and normalize an explicit per-request target language code.

    Args:
        raw_target: Optional user-supplied target language argument.

    Returns:
        Normalized target language code, or ``None`` if omitted.

    Raises:
        ValueError: If the supplied code is empty or unsupported.
    """
    if raw_target is None:
        return None
    stripped = raw_target.strip()
    if not stripped:
        msg = "Target language must not be empty"
        raise ValueError(msg)
    normalized = normalize_language_code(stripped)
    if not normalized or normalized == "und":
        msg = f"Unsupported target language: {stripped}"
        raise ValueError(msg)
    base = normalized.split("-")[0]
    if base not in LANGUAGES and resolve_model_language(normalized) is None:
        msg = f"Unsupported target language: {stripped}"
        raise ValueError(msg)
    return normalized
