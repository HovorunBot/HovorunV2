"""Tests for Twitter rich media command."""

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage, SendVideo
from dishka import AsyncContainer

from hovorunv2.application.clients.twitter import TwitterService
from hovorunv2.application.features.command_service import CommandService
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.jobs.feature_jobs import JobOutcome
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command
from tests.utils.sync_http import FakeSyncHTTP
from tests.utils.telegram.recording_delivery import drain_and_run_media


@pytest.fixture
async def twitter_command(test_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Twitter RichMediaCommand instance."""
    return await get_media_command(test_container, CommandName.TWITTER)


@pytest.mark.vcr
async def test_handle_twitter_post(
    twitter_command: RichMediaCommand, test_container: AsyncContainer, recording_bot: Bot
) -> None:
    """Test handling a real Twitter post using VCR through queued native execution."""
    chat_id = 456
    url = "https://x.com/elonmusk/status/2053586034743705773"
    message = create_private_media_message(url, chat_id=chat_id).as_(recording_bot)

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    await twitter_command.handle(message, recording_bot)

    outcome, _calls = await drain_and_run_media(test_container, recording_bot=recording_bot)
    assert outcome == JobOutcome.COMPLETE
    assert len(recording_bot.session.calls) == 1
    call = recording_bot.session.calls[0]
    caption = getattr(call, "text", None) or getattr(call, "caption", "")
    assert "Elon Musk" in caption
    assert "❤️" in caption
    assert "🔁" in caption


async def test_twitter_split_video_vs_post_toggles(
    twitter_command: RichMediaCommand, test_container: AsyncContainer, recording_bot: Bot
) -> None:
    """Test that disabling twitter_post skips text/image tweets while keeping video tweets enabled."""
    chat_id = 789
    text_tweet_url = "https://x.com/user/status/1111111111"
    video_tweet_url = "https://x.com/user/status/2222222222"

    chat_status_service = await test_container.get(ChatStatusService)
    command_service = await test_container.get(CommandService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)
    await command_service.enable_command(chat_id, CommandName.TWITTER)
    await command_service.disable_command(chat_id, CommandName.TWITTER_POST)

    text_tweet_json = {
        "text": "Just a text and image tweet",
        "user_name": "Test User",
        "user_screen_name": "testuser",
        "retweets": 10,
        "likes": 50,
        "replies": 2,
        "media_extended": [{"url": "https://pbs.twimg.com/media/img1.jpg", "type": "image"}],
    }
    video_tweet_json = {
        "text": "Video tweet",
        "user_name": "Test User",
        "user_screen_name": "testuser",
        "retweets": 20,
        "likes": 100,
        "replies": 5,
        "media_extended": [{"url": "https://video.twimg.com/vid1.mp4", "type": "video"}],
    }
    fake_http = FakeSyncHTTP(
        json_responses={
            "1111111111": text_tweet_json,
            "2222222222": video_tweet_json,
        }
    )

    msg_post = create_private_media_message(text_tweet_url, chat_id=chat_id).as_(recording_bot)
    await twitter_command.handle(msg_post, recording_bot)
    outcome_post, _ = await drain_and_run_media(test_container, recording_bot=recording_bot, http=fake_http)
    assert outcome_post == JobOutcome.COMPLETE
    assert len(recording_bot.session.calls) == 0

    msg_video = create_private_media_message(video_tweet_url, chat_id=chat_id, message_id=12346).as_(recording_bot)
    await twitter_command.handle(msg_video, recording_bot)
    outcome_video, _ = await drain_and_run_media(test_container, recording_bot=recording_bot, http=fake_http)
    assert outcome_video == JobOutcome.COMPLETE
    assert len(recording_bot.session.calls) == 1
    call = recording_bot.session.calls[0]
    assert isinstance(call, (SendMessage, SendVideo))


@pytest.mark.unit
def test_twitter_sync_extraction() -> None:
    """Test synchronous worker extraction for Twitter posts."""
    service = TwitterService.sync()
    http = FakeSyncHTTP(
        json_responses={
            "api.vxtwitter.com/i/status/12345": {
                "text": "Hello world from sync Twitter!",
                "user_name": "Sync User",
                "user_screen_name": "syncuser",
                "retweets": 15,
                "likes": 200,
                "replies": 8,
                "media_extended": [{"url": "https://video.twimg.com/sync_vid.mp4", "type": "video"}],
            }
        }
    )
    url = "https://x.com/syncuser/status/12345"
    payload = service.extract_sync(url, http)
    assert payload is not None
    assert payload.author_name == "Sync User"
    assert payload.author_handle == "syncuser"
    assert payload.content == "Hello world from sync Twitter!"
    assert len(payload.media_items) == 1
    assert payload.media_items[0].url == "https://video.twimg.com/sync_vid.mp4"
    assert payload.media_items[0].is_video is True
    assert "🔁 15" in payload.footer_text
    assert "❤️ 200" in payload.footer_text


@pytest.mark.unit
def test_twitter_sync_quote_tweet() -> None:
    """Test synchronous worker extraction for Twitter quote tweets."""
    service = TwitterService.sync()
    http = FakeSyncHTTP(
        json_responses={
            "api.vxtwitter.com/i/status/999": {
                "text": "My commentary https://t.co/quote123",
                "qrtURL": "https://t.co/quote123",
                "user_name": "Commenter",
                "user_screen_name": "commenter",
                "retweets": 0,
                "likes": 5,
                "replies": 0,
                "media_extended": [],
                "qrt": {
                    "text": "Original tweet",
                    "user_name": "Original Author",
                    "user_screen_name": "orig",
                    "media_extended": [{"url": "https://pbs.twimg.com/img.jpg", "type": "image"}],
                },
            }
        }
    )
    payload = service.extract_sync("https://twitter.com/commenter/status/999", http)
    assert payload is not None
    assert payload.content == "My commentary"
    assert payload.quoted_payload is not None
    assert payload.quoted_payload.content == "Original tweet"
    assert payload.quoted_payload.author_handle == "orig"
    assert len(payload.quoted_payload.media_items) == 1
