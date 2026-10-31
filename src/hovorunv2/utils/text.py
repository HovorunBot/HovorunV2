"""Caption text normalization utilities."""

import re


def clean_caption_text(raw_text: str, *, strip_hashtags: bool = True) -> str:
    """Normalize whitespace and optionally strip hashtags from caption text."""
    cleaned = re.sub(r"#\w+", "", raw_text) if strip_hashtags else raw_text
    return re.sub(r"\s+", " ", cleaned).strip()
