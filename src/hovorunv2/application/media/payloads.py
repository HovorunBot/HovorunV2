"""Data Transfer Objects for the application."""

import json
from dataclasses import dataclass, field


@dataclass
class MediaItem:
    """Standardized media item with URL, type, and optional downloaded bytes."""

    url: str
    is_video: bool
    data: bytes | None = None
    requires_download: bool = False


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
    browser_download: bool = False

    @property
    def is_video(self) -> bool:
        """Return whether the post contains video, including unavailable video media."""
        return self.video_unavailable or any(item.is_video for item in self.media_items)


def pack_media_payload(payload: RichMediaPayload) -> bytes:
    """Serialize a retained candidate carrying URLs and text only, never media bytes.

    Args:
        payload: Usable partial candidate to retain across a stage handoff.

    Returns:
        Compact UTF-8 JSON representation accepted by `unpack_media_payload`.
    """
    content = {
        "author_name": payload.author_name,
        "author_handle": payload.author_handle,
        "author_url": payload.author_url,
        "content": payload.content,
        "footer_text": payload.footer_text,
        "original_url": payload.original_url,
        "video_unavailable": payload.video_unavailable,
        "browser_download": payload.browser_download,
        "quoted_payload": pack_media_payload(payload.quoted_payload).decode() if payload.quoted_payload else None,
        "media_items": [
            {"url": item.url, "is_video": item.is_video, "requires_download": item.requires_download}
            for item in payload.media_items
        ],
    }
    return json.dumps(content, separators=(",", ":")).encode()


def unpack_media_payload(data: bytes) -> RichMediaPayload:
    """Reconstruct a retained candidate, rejecting malformed content as a safe failure.

    Args:
        data: Serialized candidate previously produced by `pack_media_payload`.

    Returns:
        Retained usable partial candidate.

    Raises:
        TypeError: If the decoded candidate has an invalid structure.
    """
    content = json.loads(data)
    if not isinstance(content, dict):
        msg = "Retained candidate is malformed."
        raise TypeError(msg)
    raw_items = content.get("media_items")
    if not isinstance(raw_items, list):
        msg = "Retained candidate media is malformed."
        raise TypeError(msg)
    raw_quote = content.get("quoted_payload")
    if raw_quote is not None and not isinstance(raw_quote, str):
        msg = "Retained quoted candidate is malformed."
        raise TypeError(msg)
    media_items = [
        MediaItem(
            str(item["url"]), bool(item["is_video"]), requires_download=bool(item.get("requires_download", False))
        )
        for item in raw_items
        if isinstance(item, dict) and item.get("url")
    ]
    return RichMediaPayload(
        author_name=str(content["author_name"]),
        author_handle=str(content["author_handle"]),
        author_url=str(content["author_url"]),
        content=str(content["content"]),
        footer_text=str(content["footer_text"]),
        original_url=str(content["original_url"]),
        media_items=media_items,
        video_unavailable=bool(content["video_unavailable"]),
        browser_download=bool(content.get("browser_download", False)),
        quoted_payload=unpack_media_payload(raw_quote.encode()) if raw_quote is not None else None,
    )
