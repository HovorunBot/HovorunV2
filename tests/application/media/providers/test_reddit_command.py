"""Tests for Reddit rich media extraction."""

import pytest

from hovorunv2.application.media.providers.reddit import RedditService
from tests.utils.sync_http import FakeSyncHTTP

EXPECTED_GALLERY_COUNT: int = 2


@pytest.mark.unit
def test_reddit_sync_extraction() -> None:
    """Test synchronous worker extraction for Reddit gallery post."""
    service = RedditService.sync()
    url = "https://www.reddit.com/r/pics/comments/1abcde/sync_gallery_test/"
    vx_url = "https://vxreddit.com/r/pics/comments/1abcde/sync_gallery_test/"
    mock_html = """
    <!doctype html>
    <html lang="en">
        <head>
            <meta property="og:site_name" content="u/sync_user on r/pics - ⬆️ 3000 | 💬 150" />
            <meta property="og:title" content="Sync Gallery Title" />
            <meta property="og:image" content="https://i.redd.it/sync1.jpg" />
            <meta property="og:image" content="https://i.redd.it/sync2.jpg" />
        </head>
    </html>
    """
    http = FakeSyncHTTP(text_responses={vx_url: mock_html})

    payload = service.extract_sync(url, http)
    assert payload is not None
    assert payload.author_name == "sync_user (r/pics)"
    assert payload.author_handle == "sync_user"
    assert payload.content == "Sync Gallery Title"
    assert len(payload.media_items) == 2
    assert payload.media_items[0].url == "https://i.redd.it/sync1.jpg"
    assert payload.media_items[1].url == "https://i.redd.it/sync2.jpg"
    assert "⬆️ 3K" in payload.footer_text
    assert "💬 150" in payload.footer_text
