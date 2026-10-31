"""OpenGraph and Twitter metadata parsing utilities."""

import html
import re


def extract_og_metadata(html_content: str) -> dict[str, str]:
    """Extract OpenGraph and Twitter Card metadata from an HTML document.

    Args:
        html_content: Raw HTML document text.

    Returns:
        Mapping of lowercase metadata key to its decoded value, preserving first occurrence.
    """
    metadata = {}
    for meta in re.finditer(r"<meta\s+([^>]+)>", html_content, re.IGNORECASE):
        body = meta.group(1)
        key_m = re.search(r'(?:property|name)=["\'](?:og:|twitter:)?([^"\']+)["\']', body, re.IGNORECASE)
        val_m = re.search(r'content=["\']([^"\']+)["\']', body, re.IGNORECASE)

        if key_m and val_m:
            key = key_m.group(1).lower()
            val = html.unescape(val_m.group(1))
            if key not in metadata:
                metadata[key] = val
    return metadata
