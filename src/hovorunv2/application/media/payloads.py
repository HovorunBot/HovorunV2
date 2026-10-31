"""Data Transfer Objects for the application."""

from dataclasses import dataclass, field


@dataclass
class MediaItem:
    """Standardized media item with URL, type, and optional downloaded bytes."""

    url: str
    is_video: bool
    data: bytes | None = None


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

    @property
    def is_video(self) -> bool:
        """Backward compatibility: returns True if any item is a video."""
        return any(item.is_video for item in self.media_items)
