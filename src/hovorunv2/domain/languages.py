"""Normalize language identities without coupling text and audio detectors."""

import re

_LANGUAGE_TAG = re.compile(r"[a-zA-Z]{2,3}(?:-[a-zA-Z]{4})?(?:-(?:[a-zA-Z]{2}|[0-9]{3}))?\Z")
_ALIASES = {"iw": "he", "ji": "yi", "in": "id"}
UNDETERMINED_LANGUAGE = "und"
_SCRIPT_CODE_LENGTH = 4


def normalize_language_code(value: str) -> str:
    """Normalize ISO language tags while preserving script and regional distinctions.

    This operation validates tag shape, not support by any detector or engine.
    It never infers a script or language from a user's interface preferences.

    Args:
        value: Language tag supplied by a capability adapter.

    Returns:
        Canonical casing with normalized separators and legacy aliases.

    Raises:
        ValueError: If the tag has an unsupported shape.
    """
    tag = value.strip().replace("_", "-")
    if _LANGUAGE_TAG.fullmatch(tag) is None:
        msg = "Language code must be a language tag with an optional script and region."
        raise ValueError(msg)
    language, *qualifiers = tag.split("-")
    language = _ALIASES.get(language.lower(), language.lower())
    return "-".join(
        (language, *(part.title() if len(part) == _SCRIPT_CODE_LENGTH else part.upper() for part in qualifiers))
    )
