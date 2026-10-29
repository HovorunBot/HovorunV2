"""Real yt-dlp transport and required extraction result contracts."""

import asyncio
from http import HTTPStatus

import pytest

from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.extractor import MediaExtractor, NativeMediaExtractor, process_extraction_info
from tests.utils.sync_http import HTTPFixture, HTTPReply


def test_extraction_metadata_preserves_short_caption_and_metrics_policy() -> None:
    """Pure shared parsing retains title fallback, source identity and media facts."""
    source = "https://synthetic.invalid/shorts/example"
    payload = process_extraction_info(
        {
            "title": "Synthetic title",
            "description": "Short desc",
            "uploader": "Synthetic uploader",
            "uploader_id": "synthetic",
            "url": "https://synthetic.invalid/asset.mp4",
            "like_count": 100,
            "view_count": 1000,
        },
        source,
    )
    assert payload.author_name == "Synthetic uploader"
    assert payload.content == "Synthetic title"
    assert payload.is_video
    assert payload.original_url == source
    assert payload.footer_text == "❤️ 100 | 👁️ 1K"


@pytest.mark.parametrize("raw", [None, {}, [], "invalid", {"unknown": 1}])
def test_required_extraction_rejects_unusable_metadata(raw: object) -> None:
    """Missing provider facts cannot become an optional miss or a placeholder-only successful payload."""
    with pytest.raises(MediaExtractionError, match="unusable metadata"):
        process_extraction_info(raw, "https://synthetic.invalid/post")


async def test_native_and_async_extraction_use_real_bounded_backend() -> None:
    """Both runtimes parse the same real loopback HTTP media endpoint without replacing internal operations."""
    with HTTPFixture({"/asset.mp4": HTTPReply(b"synthetic media", headers={"Content-Type": "video/mp4"})}) as server:
        source = server.url("/asset.mp4")
        native = await asyncio.to_thread(NativeMediaExtractor().extract, source)
        asynchronous = await MediaExtractor().extract(source)
        assert native.media_items[0].url == source
        assert asynchronous.media_items[0].url == source
        assert native.original_url == asynchronous.original_url == source


async def test_backend_failure_is_semantic_and_sanitized_in_both_runtimes() -> None:
    """Unavailable HTTP input yields a safe required-strategy error instead of None."""
    with HTTPFixture({"/missing.mp4": HTTPReply(status=HTTPStatus.NOT_FOUND)}) as server:
        source = server.url("/missing.mp4")
        with pytest.raises(MediaExtractionError) as native:
            await asyncio.to_thread(NativeMediaExtractor().extract, source)
        with pytest.raises(MediaExtractionError) as asynchronous:
            await MediaExtractor().extract(source)
        assert str(native.value) == str(asynchronous.value) == "Media extraction failed."
        assert native.value.__cause__ is None
        assert asynchronous.value.__cause__ is None
