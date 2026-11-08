"""Real yt-dlp transport and required extraction result contracts."""

import asyncio
from http import HTTPStatus

import pytest
import yt_dlp_ejs
from yt_dlp.globals import supported_js_runtimes

from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.extractor import NativeMediaExtractor, process_extraction_info
from tests.utils.sync_http import HTTPFixture, HTTPReply


def test_extraction_metadata_preserves_short_caption_and_metrics_policy() -> None:
    """Pure shared parsing retains title fallback, source identity and media facts."""
    source = "https://synthetic.invalid/shorts/example"
    payload = process_extraction_info(
        {
            "title": "Synthetic title",
            "description": "Short desc",
            "uploader": "Synthetic uploader",
            "uploader_id": "@synthetic",
            "formats": [
                {
                    "url": "https://synthetic.invalid/hls.m3u8",
                    "ext": "mp4",
                    "vcodec": "avc1",
                    "acodec": "mp4a",
                    "protocol": "m3u8_native",
                    "height": 1920,
                },
                {
                    "url": "https://synthetic.invalid/direct.mp4",
                    "ext": "mp4",
                    "vcodec": "avc1",
                    "acodec": "mp4a",
                    "protocol": "https",
                    "height": 640,
                },
            ],
            "like_count": 100,
            "view_count": 1000,
        },
        source,
    )
    assert payload.author_name == "Synthetic uploader"
    assert payload.author_handle == "synthetic"
    assert payload.content == "Synthetic title"
    assert payload.is_video
    assert payload.media_items[0].url == "https://synthetic.invalid/direct.mp4"
    assert payload.original_url == source
    assert payload.footer_text == "❤️ 100 | 👁️ 1K"


def test_ytdlp_default_js_runtime_and_ejs_solver_are_available() -> None:
    """Default yt-dlp configuration has a supported JS runtime and challenge solver scripts."""
    assert yt_dlp_ejs.version
    runtime = supported_js_runtimes.value["deno"]()
    assert runtime.info is not None
    assert runtime.info.supported is True


@pytest.mark.parametrize("raw", [None, {}, [], "invalid", {"unknown": 1}])
def test_required_extraction_rejects_unusable_metadata(raw: object) -> None:
    """Missing provider facts cannot become an optional miss or a placeholder-only successful payload."""
    with pytest.raises(MediaExtractionError, match="unusable metadata"):
        process_extraction_info(raw, "https://synthetic.invalid/post")


async def test_native_extraction_uses_real_bounded_backend() -> None:
    """Native worker parses the real loopback HTTP media endpoint without replacing internal operations."""
    with HTTPFixture({"/asset.mp4": HTTPReply(b"synthetic media", headers={"Content-Type": "video/mp4"})}) as server:
        source = server.url("/asset.mp4")
        native = await asyncio.to_thread(NativeMediaExtractor().extract, source)
        assert native.media_items[0].url == source
        assert native.original_url == source


async def test_backend_failure_is_semantic_and_sanitized() -> None:
    """Unavailable HTTP input yields a safe required-strategy error instead of None."""
    with HTTPFixture({"/missing.mp4": HTTPReply(status=HTTPStatus.NOT_FOUND)}) as server:
        source = server.url("/missing.mp4")
        with pytest.raises(MediaExtractionError) as native:
            await asyncio.to_thread(NativeMediaExtractor().extract, source)
        assert str(native.value) == "Media extraction failed."
        assert native.value.__cause__ is None
