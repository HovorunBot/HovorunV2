"""Caption text normalization utilities."""

import re


def clean_caption_text(raw_text: str, *, strip_hashtags: bool = True) -> str:
    """Normalize caption text whitespace and optionally strip hashtag tokens.

    Args:
        raw_text: Raw caption text to normalize.
        strip_hashtags: Whether to remove hashtag tokens before collapsing whitespace.

    Returns:
        Whitespace-collapsed caption text, with hashtags removed when requested.
    """
    cleaned = re.sub(r"#\w+", "", raw_text) if strip_hashtags else raw_text
    return re.sub(r"\s+", " ", cleaned).strip()
