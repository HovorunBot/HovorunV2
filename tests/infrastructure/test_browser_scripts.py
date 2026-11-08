"""Verify configured provider scripts through standard JavaScript evaluation."""

import pytest

from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.provider_config import INSTAGRAM_JS_EXTRACTOR
from hovorunv2.infrastructure.browser.bounded_tab import BoundedBrowserTab
from hovorunv2.infrastructure.browser.browser_process import OwnedChromium
from tests.utils.sync_http import HTTPFixture, HTTPReply

pytestmark = pytest.mark.browser


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (
            (
                '<meta property="og:video" content="https://media.example.invalid/video.mp4">'
                '<meta property="og:image" content="https://media.example.invalid/image.jpg">'
                '<meta property="og:title" content="Synthetic title">'
                '<meta property="og:description" content="Synthetic description">'
            ),
            {
                "video": "https://media.example.invalid/video.mp4",
                "image": "https://media.example.invalid/image.jpg",
                "title": "Synthetic title",
                "description": "Synthetic description",
                "likes": None,
                "comments": None,
                "views": None,
            },
        ),
        (
            (
                "<script>window.__additionalDataLoaded = {feed: {items: [{"
                'video_url: "https://media.example.invalid/video.mp4",'
                'display_url: "https://media.example.invalid/image.jpg",'
                'caption: {text: "Synthetic caption"}, like_count: 12, comment_count: 3, view_count: 45'
                "}]}};</script>"
            ),
            {
                "video": "https://media.example.invalid/video.mp4",
                "image": "https://media.example.invalid/image.jpg",
                "title": "Synthetic caption",
                "description": "",
                "likes": 12,
                "comments": 3,
                "views": 45,
            },
        ),
    ],
)
def test_instagram_script_returns_metadata_under_standard_evaluation(
    document: str, expected: dict[str, object]
) -> None:
    """Extract metadata without relying on an SDK to invoke bare function declarations."""
    reply = HTTPReply(f"<html><head>{document}</head><body></body></html>".encode())
    with HTTPFixture({"/post": reply}) as server:
        browser = OwnedChromium()
        try:
            page = browser.start()
            process, profile = browser.process, browser.profile_path
            assert process is not None
            assert profile is not None
            tab = BoundedBrowserTab(page.new_tab(), ExtractionDeadline.after(15))
            tab.get(server.url("/post"), timeout=10)
            result = tab.run_js(f"return (function() {{\n{INSTAGRAM_JS_EXTRACTOR}\n}})()")
            assert result == expected
            tab.close()
        finally:
            browser.close()
        assert process.poll() is not None
        assert not profile.exists()
