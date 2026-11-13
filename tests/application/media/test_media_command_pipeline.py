"""Consolidated integration tests for RichMediaCommand queueing, split toggles, and worker delivery."""

import pytest
from aiogram.methods import SendMessage, SendVideo
from dishka import AsyncContainer

from hovorunv2.application.features.command_service import CommandService
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.jobs.feature_jobs import JobOutcome
from hovorunv2.domain.enums import CommandName
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command
from tests.utils.sync_http import FakeSyncHTTP
from tests.utils.telegram.recording_delivery import drain_and_run_media
from tests.utils.telegram.session import RecordingBot


async def test_media_command_queues_extracts_and_delivers(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """RichMediaCommand approves, queues, extracts via sync HTTP, and delivers to RecordingBot."""
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
    await (await test_container.get(ChatStatusService)).approve(chat_id)
    await authorize_private_media(test_container, chat_id)
    command = await get_media_command(test_container, CommandName.REDDIT)

    http = FakeSyncHTTP(text_responses={vx_url: mock_html})
    message = create_private_media_message(url, chat_id=chat_id).as_(recording_bot)
    await command.handle(message, recording_bot)

    outcome, _calls = await drain_and_run_media(test_container, recording_bot=recording_bot, http=http)
    assert outcome == JobOutcome.COMPLETE
    assert len(recording_bot.session.calls) == 1
    call = recording_bot.session.calls[0]
    call_media = getattr(call, "media", None)
    caption = call_media[0].caption if isinstance(call_media, list) else getattr(call, "caption", "")
    assert "Awesome Gallery Post" in caption
    assert "reddit_user (r/pics)" in caption
    assert "⬆️ 1.5K" in caption


@pytest.mark.parametrize(
    ("parent_command", "disabled_split", "skipped_url", "allowed_url"),
    [
        (
            CommandName.TWITTER,
            CommandName.TWITTER_POST,
            "https://x.com/user/status/1111111111",
            "https://x.com/user/status/2222222222",
        ),
        (
            CommandName.REDDIT,
            CommandName.REDDIT_VIDEO,
            "https://www.reddit.com/r/videos/comments/1xyz99/funny_clip/",
            None,
        ),
        (
            CommandName.YOUTUBE,
            CommandName.YOUTUBE_VIDEO,
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            None,
        ),
    ],
)
async def test_media_command_respects_split_feature_toggles(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
    parent_command: CommandName,
    disabled_split: CommandName,
    skipped_url: str,
    allowed_url: str | None,
) -> None:
    """Disabling a split media sub-feature skips matching content while keeping sibling types active."""
    chat_id = 789
    await (await test_container.get(ChatStatusService)).approve(chat_id)
    await authorize_private_media(test_container, chat_id)
    commands = await test_container.get(CommandService)
    await commands.enable_command(chat_id, parent_command)
    await commands.disable_command(chat_id, disabled_split)
    command = await get_media_command(test_container, parent_command)

    http = FakeSyncHTTP(
        json_responses={
            "1111111111": {
                "text": "Just a text and image tweet",
                "user_name": "Test User",
                "user_screen_name": "testuser",
                "retweets": 10,
                "likes": 50,
                "replies": 2,
                "media_extended": [{"url": "https://pbs.twimg.com/media/img1.jpg", "type": "image"}],
            },
            "2222222222": {
                "text": "Video tweet",
                "user_name": "Test User",
                "user_screen_name": "testuser",
                "retweets": 20,
                "likes": 100,
                "replies": 5,
                "media_extended": [{"url": "https://video.twimg.com/vid1.mp4", "type": "video"}],
            },
        },
        text_responses={
            "https://vxreddit.com/r/videos/comments/1xyz99/funny_clip/": (
                "<!doctype html><html><head>"
                '<meta property="og:site_name" content="u/video_poster on r/videos - ⬆️ 500 | 💬 10" />'
                '<meta property="og:title" content="Funny Clip" />'
                '<meta property="og:video" content="https://v.redd.it/xyz/DASH_720.mp4" />'
                "</head></html>"
            ),
        },
    )

    msg_skipped = create_private_media_message(skipped_url, chat_id=chat_id, message_id=12345).as_(recording_bot)
    await command.handle(msg_skipped, recording_bot)
    outcome_skipped, _ = await drain_and_run_media(test_container, recording_bot=recording_bot, http=http)
    assert outcome_skipped == JobOutcome.COMPLETE
    assert len(recording_bot.session.calls) == 0

    if allowed_url is not None:
        msg_allowed = create_private_media_message(allowed_url, chat_id=chat_id, message_id=12346).as_(recording_bot)
        await command.handle(msg_allowed, recording_bot)
        outcome_allowed, _ = await drain_and_run_media(test_container, recording_bot=recording_bot, http=http)
        assert outcome_allowed == JobOutcome.COMPLETE
        assert len(recording_bot.session.calls) == 1
        assert isinstance(recording_bot.session.calls[0], (SendMessage, SendVideo))
