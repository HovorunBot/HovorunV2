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
    """Carry post text and media, retaining video classification when extraction fails."""

    author_name: str
    author_handle: str
    author_url: str
    content: str
    footer_text: str = ""
    original_url: str = ""
    media_items: list[MediaItem] = field(default_factory=list)
    quoted_payload: RichMediaPayload | None = None
    video_unavailable: bool = False

    @property
    def is_video(self) -> bool:
        """Return whether the post contains video, including unavailable video media."""
        return self.video_unavailable or any(item.is_video for item in self.media_items)
