"""Tests for YouTube rich media command."""

import json

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.clients.youtube import YoutubeService
from hovorunv2.application.features.command_service import CommandService
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.jobs.feature_jobs import JobOutcome
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command
from tests.utils.sync_http import FakeSyncHTTP
from tests.utils.telegram.recording_delivery import drain_and_run_media


@pytest.fixture
async def youtube_command(test_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the YouTube RichMediaCommand instance."""
    return await get_media_command(test_container, CommandName.YOUTUBE)


async def test_handle_youtube_community_post(
    youtube_command: RichMediaCommand, test_container: AsyncContainer, recording_bot: Bot
) -> None:
    """Test extracting and sending a YouTube Community image+text post."""
    chat_id = 456
    url = "https://www.youtube.com/post/Ugkx1234567890abcdef"

    yt_initial_data = {
        "contents": {
            "backstagePostThreadRenderer": {
                "post": {
                    "backstagePostRenderer": {
                        "authorText": {"runs": [{"text": "Tech Channel"}]},
                        "authorEndpoint": {"commandMetadata": {"webCommandMetadata": {"url": "/@TechChannel"}}},
                        "contentText": {"runs": [{"text": "New community update with photo!"}]},
                        "voteCount": {"simpleText": "4.2K"},
                        "backstageAttachment": {
                            "backstageImageRenderer": {
                                "image": {
                                    "thumbnails": [
                                        {"url": "https://yt3.ggpht.com/small.jpg"},
                                        {"url": "https://yt3.ggpht.com/highres.jpg"},
                                    ]
                                }
                            }
                        },
                    }
                }
            }
        }
    }
    html_body = f"<html><script>var ytInitialData = {json.dumps(yt_initial_data)};</script></html>"
    http = FakeSyncHTTP(text_responses={url: html_body})

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    message = create_private_media_message(url, chat_id=chat_id).as_(recording_bot)

    await youtube_command.handle(message, recording_bot)

    outcome, _calls = await drain_and_run_media(test_container, recording_bot=recording_bot, http=http)
    assert outcome == JobOutcome.COMPLETE
    assert len(recording_bot.session.calls) == 1
    call = recording_bot.session.calls[0]
    caption = getattr(call, "caption", "") or getattr(call, "text", "")
    assert "Tech Channel" in caption
    assert "New community update with photo!" in caption
    assert "❤️ 4.2K" in caption


async def test_youtube_split_video_vs_post_toggles(
    youtube_command: RichMediaCommand, test_container: AsyncContainer, recording_bot: Bot
) -> None:
    """Test that YouTube video vs community post toggles filter independently."""
    chat_id = 654
    video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    chat_status_service = await test_container.get(ChatStatusService)
    command_service = await test_container.get(CommandService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)
    await command_service.enable_command(chat_id, CommandName.YOUTUBE)
    await command_service.disable_command(chat_id, CommandName.YOUTUBE_VIDEO)

    message = create_private_media_message(video_url, chat_id=chat_id).as_(recording_bot)

    http = FakeSyncHTTP()
    await youtube_command.handle(message, recording_bot)

    outcome, _calls = await drain_and_run_media(test_container, recording_bot=recording_bot, http=http)
    assert outcome == JobOutcome.COMPLETE
    assert len(recording_bot.session.calls) == 0


async def test_handle_youtube_community_post_canonicalization(
    youtube_command: RichMediaCommand, test_container: AsyncContainer, recording_bot: Bot
) -> None:
    """Test that channel community URLs canonicalize to post URL."""
    chat_id = 789
    url = "https://www.youtube.com/channel/UC12345/community?lb=Ugkx9876543210zyx"
    post_url = "https://www.youtube.com/post/Ugkx9876543210zyx"

    yt_initial_data = {
        "contents": {
            "backstagePostThreadRenderer": {
                "post": {
                    "backstagePostRenderer": {
                        "authorText": {"runs": [{"text": "Science Channel"}]},
                        "contentText": {"runs": [{"text": "Canonical community test"}]},
                        "backstageAttachment": {
                            "backstageImageRenderer": {
                                "image": {"thumbnails": [{"url": "https://yt3.ggpht.com/img.jpg"}]}
                            }
                        },
                    }
                }
            }
        }
    }
    html_body = f"<html><script>var ytInitialData = {json.dumps(yt_initial_data)};</script></html>"
    http = FakeSyncHTTP(text_responses={post_url: html_body})

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    message = create_private_media_message(url, chat_id=chat_id).as_(recording_bot)

    await youtube_command.handle(message, recording_bot)

    outcome, _calls = await drain_and_run_media(test_container, recording_bot=recording_bot, http=http)
    assert outcome == JobOutcome.COMPLETE
    assert len(recording_bot.session.calls) == 1
    assert any(post_url in str(req.get("url", "")) for req in http.requests)


async def test_youtube_community_post_skips_ytdlp(
    youtube_command: RichMediaCommand, test_container: AsyncContainer, recording_bot: Bot
) -> None:
    """Test that failed community post extraction skips yt-dlp fallback."""
    chat_id = 999
    url = "https://www.youtube.com/post/UgkxInvalidPost12345"

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    http = FakeSyncHTTP()
    message = create_private_media_message(url, chat_id=chat_id).as_(recording_bot)

    await youtube_command.handle(message, recording_bot)

    outcome, _calls = await drain_and_run_media(test_container, recording_bot=recording_bot, http=http)
    assert outcome == JobOutcome.FAILED
    assert len(recording_bot.session.calls) == 0


@pytest.mark.unit
def test_youtube_sync_community_post_extraction() -> None:
    """Test synchronous worker extraction for YouTube Community post."""
    service = YoutubeService.sync()
    post_url = "https://www.youtube.com/post/Ugkx1234567890abcdef"
    yt_initial_data = {
        "contents": {
            "backstagePostThreadRenderer": {
                "post": {
                    "backstagePostRenderer": {
                        "authorText": {"runs": [{"text": "Tech Creator"}]},
                        "authorEndpoint": {"commandMetadata": {"webCommandMetadata": {"url": "/@TechCreator"}}},
                        "contentText": {"runs": [{"text": "Sync worker update!"}]},
                        "voteCount": {"simpleText": "1.2K"},
                        "backstageAttachment": {
                            "backstageImageRenderer": {
                                "image": {
                                    "thumbnails": [
                                        {"url": "https://yt3.ggpht.com/sync_img.jpg"},
                                    ]
                                }
                            }
                        },
                    }
                }
            }
        }
    }
    html_body = f"<html><script>var ytInitialData = {json.dumps(yt_initial_data)};</script></html>"
    http = FakeSyncHTTP(text_responses={post_url: html_body})

    payload = service.extract_sync(post_url, http)
    assert payload is not None
    assert payload.author_name == "Tech Creator"
    assert payload.author_handle == "TechCreator"
    assert payload.content == "Sync worker update!"
    assert len(payload.media_items) == 1
    assert payload.media_items[0].url == "https://yt3.ggpht.com/sync_img.jpg"
    assert "❤️ 1.2K" in payload.footer_text


@pytest.mark.unit
def test_youtube_sync_video_url_skips_direct_and_uses_extractor() -> None:
    """Test that YouTube video URLs skip direct HTML extraction and delegate to NativeMediaExtractor."""
    service = YoutubeService.sync()
    video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    http = FakeSyncHTTP()

    expected_payload = RichMediaPayload(
        author_name="Rick Astley",
        author_handle="RickAstleyVEVO",
        author_url="https://youtube.com/@RickAstleyVEVO",
        content="Never Gonna Give You Up",
        footer_text="👁️ 1B",
        original_url=video_url,
        media_items=[MediaItem(url="https://googlevideo.com/videoplayback", is_video=True)],
    )

    class DummyExtractor(NativeMediaExtractor):
        def extract(self, url: str, cookies: dict[str, str] | None = None) -> RichMediaPayload:
            return expected_payload

    # Direct extraction returns None for standard video URLs
    assert service.extract_sync(video_url, http) is None

    # Multi-tier extraction delegates to generic extractor
    payload = service.extract_media_sync(video_url, http, DummyExtractor())
    assert payload == expected_payload
