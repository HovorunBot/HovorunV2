"""Data Transfer Objects for the application."""

from dataclasses import dataclass, field


@dataclass
class MediaItem:
    """Standardized media item with URL and type."""

    url: str
    is_video: bool


@dataclass
class RichMediaPayload:
    """Standard payload for rich media responses."""

    author_name: str
    author_handle: str
    author_url: str
    content: str
    footer_text: str = ""
    original_url: str = ""
    media_items: list[MediaItem] = field(default_factory=list)
    quoted_payload: RichMediaPayload | None = None
    downloaded_bytes: list[bytes] | None = None

    @property
    def media_urls(self) -> list[str]:
        """Backward compatibility for media URLs."""
        return [item.url for item in self.media_items]

    @property
    def is_video(self) -> bool:
        """Backward compatibility: returns True if any item is a video."""
        return any(item.is_video for item in self.media_items)
