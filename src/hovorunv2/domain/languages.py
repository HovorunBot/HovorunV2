"""Normalize language identity independently of detectors and backend vocabularies."""

_LANGUAGE_ALIASES = {"iw": "he", "ji": "yi", "in": "id"}


def normalize_language_code(lang_code: str) -> str:
    """Canonicalize aliases and separators while preserving script and region subtags.

    Args:
        lang_code: Language identifier supplied by a detector or preference.

    Returns:
        Lowercase hyphen-separated identifier, or ``und`` for an empty identifier.
    """
    code = lang_code.strip().lower().replace("_", "-")
    if not code:
        return "und"
    language, separator, suffix = code.partition("-")
    return _LANGUAGE_ALIASES.get(language, language) + separator + suffix
