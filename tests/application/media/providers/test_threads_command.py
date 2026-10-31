"""Tests for Threads rich media command."""

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.jobs.feature_jobs import JobOutcome
from hovorunv2.application.media.errors import BrowserContinuationRequired, MediaExtractionError
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.application.media.providers.threads import ThreadsService
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command
from tests.utils.sync_browser import FakeSyncBrowser
from tests.utils.sync_http import FakeSyncHTTP
from tests.utils.telegram.recording_delivery import drain_and_run_media

EXPECTED_MEDIA_COUNT: int = 1


@pytest.fixture
async def threads_command(test_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Threads RichMediaCommand instance."""
    return await get_media_command(test_container, CommandName.THREADS)


@pytest.mark.slow
@pytest.mark.vcr
async def test_handle_threads_post(
    threads_command: RichMediaCommand, test_container: AsyncContainer, recording_bot: Bot
) -> None:
    """Test handling a Threads post using real extraction and VCR through staged continuation."""
    chat_id = 456
    url = "https://www.threads.net/@maksymtarkivskyy/post/DYOeEaaD-vR"
    message = create_private_media_message(url, chat_id=chat_id).as_(recording_bot)

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    html_content = """
    <html>
    <head>
        <meta property="og:title" content="Maksym Tarkivskyy (@maksymtarkivskyy) on Threads"/>
        <meta property="og:description" content="Чому це відбувається? Чи є цьому наукове пояснення? 👀"/>
    </head>
    <body>
        <article>
            <div dir="auto">Чому це відбувається? Чи є цьому наукове пояснення? 👀</div>
            <img src="https://scontent.cdninstagram.com/media_img1.jpg"/>
        </article>
        <script type="application/json">
        {
            "code": "DYOeEaaD-vR",
            "like_count": 100,
            "direct_reply_count": 5,
            "repost_count": 2
        }
        </script>
    </body>
    </html>
    """
    fake_browser = FakeSyncBrowser(html_responses={url: html_content})

    await threads_command.handle(message, recording_bot)

    outcome, _calls = await drain_and_run_media(test_container, recording_bot=recording_bot, browser=fake_browser)
    assert outcome == JobOutcome.COMPLETE
    assert len(recording_bot.session.calls) == 1
    call = recording_bot.session.calls[0]
    caption = getattr(call, "caption", "") or getattr(call, "text", "")
    assert "Mock User" in caption
    assert "threads.net" in caption


@pytest.mark.unit
def test_threads_sync_media_stage_requires_browser_continuation() -> None:
    """Test that Threads in media stage raises BrowserContinuationRequired when yt-dlp misses."""
    service = ThreadsService.sync()
    url = "https://www.threads.net/@user/post/DYOeEaaD-vR"
    http = FakeSyncHTTP()

    class FailingExtractor(NativeMediaExtractor):
        def extract(self, url: str, cookies: dict[str, str] | None = None) -> RichMediaPayload:
            msg = "yt-dlp miss"
            raise MediaExtractionError(msg)

    with pytest.raises(BrowserContinuationRequired):
        service.extract_media_sync(url, http, FailingExtractor())


@pytest.mark.unit
def test_threads_sync_browser_stage_extraction() -> None:
    """Test Threads post extraction in browser stage via SyncBrowserPort."""
    service = ThreadsService.sync()
    url = "https://www.threads.net/@sync_author/post/DYOeEaaD-vR"
    http = FakeSyncHTTP()

    html_content = """
    <html>
    <head>
        <meta property="og:title" content="Sync Author (@sync_author) on Threads"/>
        <meta property="og:description" content="Threads browser post text content"/>
    </head>
    <body>
        <article>
            <div dir="auto">Threads browser post text content</div>
            <img src="https://scontent.cdninstagram.com/media_img1.jpg"/>
        </article>
        <script type="application/json">
        {
            "code": "DYOeEaaD-vR",
            "like_count": 850,
            "direct_reply_count": 30,
            "repost_count": 12
        }
        </script>
    </body>
    </html>
    """
    browser = FakeSyncBrowser(html_responses={url: html_content})

    payload = service.extract_browser_media_sync(url, http, browser)
    assert payload is not None
    assert payload.author_handle == "sync_author"
    assert "Threads browser post text content" in payload.content
    assert len(payload.media_items) == 1
    assert payload.media_items[0].url == "https://scontent.cdninstagram.com/media_img1.jpg"
    assert "❤️ 850" in payload.footer_text
    assert "💬 30" in payload.footer_text


@pytest.mark.unit
def test_threads_sync_browser_stage_fallback_to_og() -> None:
    """Test that browser stage falls back to OpenGraph metadata when browser extraction yields no media."""
    service = ThreadsService.sync()
    url = "https://www.threads.net/@sync_author/post/DYOeEaaD-vR"

    # Browser returns empty article without media
    empty_html = "<html><body><article></article></body></html>"
    browser = FakeSyncBrowser(html_responses={url: empty_html})

    og_html = """
    <!DOCTYPE html><html><head>
    <meta property="og:title" content="OG Author on Threads"/>
    <meta property="og:description" content="OpenGraph Content description"/>
    <meta property="og:image" content="https://threads.net/og_image.jpg"/>
    </head><body></body></html>
    """
    http = FakeSyncHTTP(text_responses={url: og_html})

    payload = service.extract_browser_media_sync(url, http, browser)
    assert payload is not None
    assert payload.author_name == "OG Author"
    assert payload.content == "OpenGraph Content description"
    assert len(payload.media_items) == 1
    assert payload.media_items[0].url == "https://threads.net/og_image.jpg"
