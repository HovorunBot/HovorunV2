"""Tests for Reddit rich media command."""

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.clients.reddit import RedditService
from hovorunv2.application.features.command_service import CommandService
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.jobs.feature_jobs import JobOutcome
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command
from tests.utils.sync_http import FakeSyncHTTP
from tests.utils.telegram.recording_delivery import drain_and_run_media

EXPECTED_GALLERY_COUNT: int = 2


@pytest.fixture
async def reddit_command(test_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Reddit RichMediaCommand instance."""
    return await get_media_command(test_container, CommandName.REDDIT)


async def test_handle_reddit_gallery_post(
    reddit_command: RichMediaCommand, test_container: AsyncContainer, recording_bot: Bot
) -> None:
    """Test handling a Reddit multi-image gallery post."""
    chat_id = 456
    url = "https://www.reddit.com/r/pics/comments/1abcde/gallery_test/"
    vx_url = "https://vxreddit.com/r/pics/comments/1abcde/gallery_test/"

    mock_html = """
    <!doctype html>
    <html lang="en">
        <head>
            <meta property="og:site_name" content="u/reddit_user on r/pics - ⬆️ 1500 | 💬 42" />
            <meta property="og:title" content="Awesome Gallery Post" />
            <meta property="og:image" content="https://i.redd.it/img1.jpg" />
            <meta property="og:image" content="https://i.redd.it/img2.jpg" />
        </head>
    </html>
    """

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    http = FakeSyncHTTP(text_responses={vx_url: mock_html})
    message = create_private_media_message(url, chat_id=chat_id).as_(recording_bot)

    await reddit_command.handle(message, recording_bot)

    outcome, _calls = await drain_and_run_media(test_container, recording_bot=recording_bot, http=http)
    assert outcome == JobOutcome.COMPLETE
    assert len(recording_bot.session.calls) == 1
    call = recording_bot.session.calls[0]
    caption = call.media[0].caption if hasattr(call, "media") else getattr(call, "caption", "")
    assert "Awesome Gallery Post" in caption
    assert "reddit_user (r/pics)" in caption
    assert "⬆️ 1.5K" in caption


async def test_handle_reddit_video_post_respects_split_toggle(
    reddit_command: RichMediaCommand, test_container: AsyncContainer, recording_bot: Bot
) -> None:
    """Test that Reddit video post is skipped when reddit_video is disabled."""
    chat_id = 987
    url = "https://www.reddit.com/r/videos/comments/1xyz99/funny_clip/"
    vx_url = "https://vxreddit.com/r/videos/comments/1xyz99/funny_clip/"

    mock_html = """
    <!doctype html>
    <html lang="en">
        <head>
            <meta property="og:site_name" content="u/video_poster on r/videos - ⬆️ 500 | 💬 10" />
            <meta property="og:title" content="Funny Clip" />
            <meta property="og:video" content="https://v.redd.it/xyz/DASH_720.mp4" />
            <meta property="og:image" content="https://external-preview.redd.it/thumb.jpg" />
        </head>
    </html>
    """

    chat_status_service = await test_container.get(ChatStatusService)
    command_service = await test_container.get(CommandService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)
    await command_service.enable_command(chat_id, CommandName.REDDIT)
    await command_service.disable_command(chat_id, CommandName.REDDIT_VIDEO)

    http = FakeSyncHTTP(text_responses={vx_url: mock_html})
    msg1 = create_private_media_message(url, chat_id=chat_id).as_(recording_bot)

    await reddit_command.handle(msg1, recording_bot)

    outcome1, _ = await drain_and_run_media(test_container, recording_bot=recording_bot, http=http)
    assert outcome1 == JobOutcome.COMPLETE
    assert len(recording_bot.session.calls) == 0

    await command_service.enable_command(chat_id, CommandName.REDDIT_VIDEO)
    msg2 = create_private_media_message(url, chat_id=chat_id, message_id=12346).as_(recording_bot)
    await reddit_command.handle(msg2, recording_bot)

    outcome2, _ = await drain_and_run_media(test_container, recording_bot=recording_bot, http=http)
    assert outcome2 == JobOutcome.COMPLETE
    assert len(recording_bot.session.calls) == 1


@pytest.mark.vcr
async def test_handle_reddit_post_vcr(
    reddit_command: RichMediaCommand, test_container: AsyncContainer, recording_bot: Bot
) -> None:
    """Test handling a real Reddit post via vxReddit using VCR."""
    chat_id = 457
    url = "https://www.reddit.com/r/DotA2/comments/1xbq3id/please_bring_back_blood_mist_aghs_for_seeker_and/"
    message = create_private_media_message(url, chat_id=chat_id, message_id=12347).as_(recording_bot)

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    await reddit_command.handle(message, recording_bot)

    outcome, _calls = await drain_and_run_media(test_container, recording_bot=recording_bot)
    assert outcome == JobOutcome.COMPLETE
    assert len(recording_bot.session.calls) == 1
    call = recording_bot.session.calls[0]
    caption = call.media[0].caption if hasattr(call, "media") else getattr(call, "caption", "")
    assert "Lycanthrope- (r/DotA2)" in caption
    assert "Please bring back blood mist aghs for seeker" in caption


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
