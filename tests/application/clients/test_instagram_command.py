"""Tests for Instagram rich media command."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import instaloader
import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.clients.instagram import InstagramService
from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.media.errors import BrowserContinuationRequired, MediaExtractionError
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command
from tests.utils.sync_browser import FakeSyncBrowser
from tests.utils.sync_http import FakeSyncHTTP, HTTPFixture, HTTPReply


@pytest.fixture
async def instagram_command(test_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Instagram RichMediaCommand instance."""
    return await get_media_command(test_container, CommandName.INSTAGRAM)


@pytest.mark.slow
@pytest.mark.vcr
async def test_handle_instagram_post(instagram_command: RichMediaCommand, test_container: AsyncContainer) -> None:
    """Test handling an Instagram post using real extraction and VCR."""
    chat_id = 789
    url = "https://www.instagram.com/p/DYPIeHDoZ-w/"
    message = create_private_media_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    await instagram_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert kwargs["chat_id"] == chat_id
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "instagram.com" in caption


@pytest.mark.vcr
async def test_handle_instagram_reel(instagram_command: RichMediaCommand, test_container: AsyncContainer) -> None:
    """Test handling an Instagram reel using real extraction and VCR."""
    chat_id = 789
    url = "https://www.instagram.com/reels/DVMKxZHggx7/"
    message = create_private_media_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    await instagram_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "instagram.com" in caption


@pytest.mark.browser
@pytest.mark.slow
async def test_instagram_browser_fallback_uses_real_dom_and_media(test_container: AsyncContainer) -> None:
    """Real browser extraction downloads loopback media without replacing an internal strategy/service."""
    service = await test_container.get(InstagramService)
    with HTTPFixture({"/image.jpg": HTTPReply(b"synthetic image")}) as media:
        document = (
            '<html><head><meta property="og:title" content="Synthetic fallback">'
            f'<meta property="og:image" content="{media.url("/image.jpg")}">'
            "</head><body><main>Synthetic fallback</main></body></html>"
        )
        with HTTPFixture({"/post": HTTPReply(document.encode(), headers={"Content-Type": "text/html"})}) as server:
            payload = await service.extract_fallback(server.url("/post"))
            assert payload is not None
            assert "Synthetic fallback" in payload.content
            assert payload.media_items[0].data == b"synthetic image"
            assert payload.media_items[0].url == media.url("/image.jpg")


@pytest.mark.unit
def test_instagram_sync_media_stage_extractor() -> None:
    """Test that yt-dlp success completes in media stage without browser continuation."""
    service = InstagramService.sync()
    url = "https://www.instagram.com/reel/C1234567890/"
    http = FakeSyncHTTP()

    expected_payload = RichMediaPayload(
        author_name="Instagram Creator",
        author_handle="creator",
        author_url="https://www.instagram.com/creator/",
        content="Sync reel video",
        footer_text="❤️ 100",
        original_url=url,
        media_items=[MediaItem(url="https://scontent.cdninstagram.com/vid.mp4", is_video=True)],
    )

    class ReelExtractor(NativeMediaExtractor):
        def extract(self, url: str, cookies: dict[str, str] | None = None) -> RichMediaPayload:
            return expected_payload

    payload = service.extract_media_sync(url, http, ReelExtractor())
    assert payload == expected_payload


@pytest.mark.unit
def test_instagram_sync_media_stage_instaloader() -> None:
    """Test that when yt-dlp misses, instaloader extracts post directly in media stage."""
    service = InstagramService.sync()
    url = "https://www.instagram.com/p/C9876543210/"
    http = FakeSyncHTTP()

    class FailingExtractor(NativeMediaExtractor):
        def extract(self, url: str, cookies: dict[str, str] | None = None) -> RichMediaPayload:
            msg = "yt-dlp miss"
            raise MediaExtractionError(msg)

    mock_post = MagicMock(spec=instaloader.Post)
    mock_post.caption = "Instaloader post content"
    mock_post.owner_profile.full_name = "Insta Full Name"
    mock_post.owner_username = "instauser"
    mock_post.likes = 42
    mock_post.comments = 5
    mock_post.video_view_count = None
    mock_post.typename = "GraphImage"
    mock_post.is_video = False
    mock_post.url = "https://scontent.cdninstagram.com/photo.jpg"

    with patch("instaloader.Post.from_shortcode", return_value=mock_post):
        payload = service.extract_media_sync(url, http, FailingExtractor())

    assert payload is not None
    assert payload.author_name == "Insta Full Name"
    assert payload.author_handle == "instauser"
    assert payload.content == "Instaloader post content"
    assert len(payload.media_items) == 1
    assert payload.media_items[0].url == "https://scontent.cdninstagram.com/photo.jpg"


@pytest.mark.unit
def test_instagram_sync_media_stage_requires_browser_continuation() -> None:
    """Test that when yt-dlp and instaloader miss, BrowserContinuationRequired is raised."""
    service = InstagramService.sync()
    url = "https://www.instagram.com/p/C9876543210/"
    http = FakeSyncHTTP()

    class FailingExtractor(NativeMediaExtractor):
        def extract(self, url: str, cookies: dict[str, str] | None = None) -> RichMediaPayload:
            msg = "yt-dlp miss"
            raise MediaExtractionError(msg)

    with (
        patch("instaloader.Post.from_shortcode", side_effect=instaloader.exceptions.InstaloaderException("blocked")),
        pytest.raises(BrowserContinuationRequired),
    ):
        service.extract_media_sync(url, http, FailingExtractor())


@pytest.mark.unit
def test_instagram_sync_browser_stage_extraction() -> None:
    """Test browser-stage extraction using SyncBrowserPort."""
    service = InstagramService.sync()
    url = "https://www.instagram.com/p/C9876543210/"
    http = FakeSyncHTTP()

    expected_payload = RichMediaPayload(
        author_name="Browser Creator",
        author_handle="browsercreator",
        author_url="https://www.instagram.com/browsercreator/",
        content="Browser post content",
        footer_text="Instagram Post",
        original_url=url,
        media_items=[MediaItem(url="https://scontent.cdninstagram.com/browser.jpg", is_video=False)],
    )

    browser = FakeSyncBrowser(extract_payloads={url: (expected_payload, [b"image_bytes"])})
    payload = service.extract_browser_media_sync(url, http, browser)
    assert payload.author_name == "Browser Creator"
    assert payload.media_items[0].data == b"image_bytes"


@pytest.mark.unit
def test_instagram_sync_browser_stage_fallback_to_og() -> None:
    """Test that browser-stage falls back to OpenGraph metadata when browser yields no payload."""
    service = InstagramService.sync()
    url = "https://www.instagram.com/p/C9876543210/"
    browser = FakeSyncBrowser()
    html = (
        "<!DOCTYPE html><html><head>"
        '<meta property="og:title" content="Instagram Creator on Instagram: Beautiful View">'
        '<meta property="og:description" content="Beautiful View description">'
        '<meta property="og:image" content="https://scontent.cdninstagram.com/og_photo.jpg">'
        "</head><body></body></html>"
    )
    http = FakeSyncHTTP(text_responses={url: html})

    payload = service.extract_browser_media_sync(url, http, browser)
    assert payload is not None
    assert payload.author_name == "Instagram Creator"
    assert "Beautiful View" in payload.content
    assert payload.media_items[0].url == "https://scontent.cdninstagram.com/og_photo.jpg"
