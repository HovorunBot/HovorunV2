"""Preserve split-stream audio across extraction and worker handoffs."""

import pytest

from hovorunv2.application.media.extractor import process_extraction_info
from hovorunv2.application.media.payloads import pack_media_payload, unpack_media_payload

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("selected_url", [False, True])
def test_split_stream_extraction_retains_source_for_merging(*, selected_url: bool) -> None:
    """Never expose a video-only rendition as the complete deliverable."""
    source = "https://synthetic.invalid/watch"
    video = {"url": "https://synthetic.invalid/video.mp4", "vcodec": "avc1", "acodec": "none", "ext": "mp4"}
    audio = {"url": "https://synthetic.invalid/audio.m4a", "vcodec": "none", "acodec": "mp4a", "ext": "m4a"}
    raw = {
        "title": "Synthetic split video",
        "webpage_url": source,
        "formats": [audio, video],
        "requested_formats": [video, audio],
    }
    if selected_url:
        raw.update(video)
    payload = process_extraction_info(raw, source)
    assert payload.media_items[0].url == source
    assert payload.media_items[0].is_video
    restored = unpack_media_payload(pack_media_payload(payload))
    assert restored.media_items == payload.media_items


def test_video_handoff_preserves_quoted_post_without_media_bytes() -> None:
    """A processing handoff must retain quoted captions without persisting downloaded buffers."""
    payload = process_extraction_info(
        {"title": "Outer caption", "url": "https://synthetic.invalid/video.mp4"},
        "https://synthetic.invalid/post",
    )
    quote = process_extraction_info({"title": "Quoted caption"}, "https://synthetic.invalid/quote")
    payload.quoted_payload = quote
    payload.media_items[0].data = b"private synthetic media bytes"
    restored = unpack_media_payload(pack_media_payload(payload))
    assert restored.quoted_payload == quote
    assert restored.media_items[0].data is None
