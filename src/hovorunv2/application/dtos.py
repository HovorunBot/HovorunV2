"""Data Transfer Objects for the application."""

from dataclasses import dataclass, field


@dataclass
class RichMediaPayload:
    """Standard payload for rich media responses."""

    author_name: str
    author_handle: str
    author_url: str
    content: str
    footer_text: str = ""
    original_url: str = ""
    media_urls: list[str] = field(default_factory=list)
    is_video: bool = False
