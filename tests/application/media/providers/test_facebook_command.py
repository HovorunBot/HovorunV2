"""Tests for Facebook rich media command."""

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.jobs.feature_jobs import JobOutcome
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.application.media.providers.base import FACEBOOK_CONFIG, BaseMediaClient
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command
from tests.utils.sync_http import FakeSyncHTTP
from tests.utils.telegram.recording_delivery import drain_and_run_media


@pytest.fixture
async def facebook_command(test_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Facebook RichMediaCommand instance."""
    return await get_media_command(test_container, CommandName.FACEBOOK)


@pytest.mark.vcr
async def test_handle_facebook_photo_post(
    facebook_command: RichMediaCommand, test_container: AsyncContainer, recording_bot: Bot
) -> None:
    """Test handling a Facebook photo post using real extraction and VCR."""
    chat_id = 789
    url = "https://www.facebook.com/share/p/1B37pk4bUf/"
    message = create_private_media_message(url, chat_id=chat_id).as_(recording_bot)

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    await facebook_command.handle(message, recording_bot)

    outcome, _calls = await drain_and_run_media(test_container, recording_bot=recording_bot)
    assert outcome == JobOutcome.COMPLETE
    assert len(recording_bot.session.calls) == 1
    call = recording_bot.session.calls[0]
    caption = getattr(call, "caption", "") or getattr(call, "text", "")
    assert "Mock User" in caption
    assert "facebook.com" in caption


@pytest.mark.vcr
async def test_handle_facebook_video_fallback(
    facebook_command: RichMediaCommand, test_container: AsyncContainer, recording_bot: Bot
) -> None:
    """Test handling a Facebook video using real extraction and VCR (yt-dlp fallback)."""
    chat_id = 789
    url = "https://www.facebook.com/share/v/1NBDyTuecs/"
    message = create_private_media_message(url, chat_id=chat_id, message_id=12346).as_(recording_bot)

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    await facebook_command.handle(message, recording_bot)

    outcome, _calls = await drain_and_run_media(test_container, recording_bot=recording_bot)
    assert outcome == JobOutcome.COMPLETE
    assert len(recording_bot.session.calls) == 1
    call = recording_bot.session.calls[0]
    caption = getattr(call, "caption", "") or getattr(call, "text", "")
    assert "Mock User" in caption
    assert "facebook.com" in caption


@pytest.mark.unit
def test_facebook_sync_metadata_extraction() -> None:
    """Test synchronous OpenGraph metadata extraction for Facebook."""
    client = BaseMediaClient.for_worker(FACEBOOK_CONFIG)
    html = (
        "<!DOCTYPE html><html><head>"
        '<meta property="og:title" content="Facebook Creator - Inspiring Post">'
        '<meta property="og:description" content="This is an inspiring Facebook post content.">'
        '<meta property="og:image" content="https://scontent.facebook.com/photo123.jpg">'
        "</head><body></body></html>"
    )
    url = "https://www.facebook.com/share/p/1B37pk4bUf/"
    http = FakeSyncHTTP(text_responses={url: html})

    payload = client.extract_metadata_sync(url, http)
    assert payload is not None
    assert payload.author_name == "Facebook Creator"
    assert "Inspiring Post" in payload.content or "inspiring Facebook post" in payload.content
    assert len(payload.media_items) == 1
    assert payload.media_items[0].url == "https://scontent.facebook.com/photo123.jpg"
    assert payload.media_items[0].is_video is False


@pytest.mark.unit
def test_facebook_sync_extract_media_tier_fallback() -> None:
    """Test multi-tier extraction falling back to Tier 4 OpenGraph when generic extractor fails."""
    client = BaseMediaClient.for_worker(FACEBOOK_CONFIG)
    html = (
        "<!DOCTYPE html><html><head>"
        '<meta property="og:title" content="Facebook User">'
        '<meta property="og:description" content="Fallback description text">'
        '<meta property="og:image" content="https://scontent.facebook.com/img.jpg">'
        "</head><body></body></html>"
    )
    url = "https://www.facebook.com/share/p/1B37pk4bUf/"
    http = FakeSyncHTTP(text_responses={url: html})

    class FailingExtractor(NativeMediaExtractor):
        def extract(self, url: str, cookies: dict[str, str] | None = None) -> RichMediaPayload:
            msg = "generic failure"
            raise MediaExtractionError(msg)

    payload = client.extract_media_sync(url, http, FailingExtractor())
    assert payload is not None
    assert payload.author_name == "Facebook User"
    assert payload.media_items[0].url == "https://scontent.facebook.com/img.jpg"
