"""Tests for Bluesky rich media command."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.clients.bluesky import BlueskyService
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command
from tests.utils.sync_http import FakeSyncHTTP


@pytest.fixture
async def bluesky_command(test_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Bluesky RichMediaCommand instance."""
    return await get_media_command(test_container, CommandName.BLUESKY)


@pytest.mark.vcr
async def test_handle_bluesky_post(bluesky_command: RichMediaCommand, test_container: AsyncContainer) -> None:
    """Test handling a Bluesky post using real extraction and VCR."""
    chat_id = 789
    url = "https://bsky.app/profile/josiahcohen.bsky.social/post/3mlmib6xdbc2a"
    message = create_private_media_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    await bluesky_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "bsky.app" in caption


@pytest.mark.vcr
async def test_handle_bluesky_quote_with_media(
    bluesky_command: RichMediaCommand, test_container: AsyncContainer
) -> None:
    """Test handling a Bluesky quote post with media using real extraction and VCR."""
    chat_id = 789
    url = "https://bsky.app/profile/hikkifox.bsky.social/post/3mlnxm3on6s2x"
    message = create_private_media_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    await bluesky_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "bsky.app" in caption


@pytest.mark.unit
def test_bluesky_sync_extraction() -> None:
    """Test synchronous worker extraction for Bluesky post."""
    service = BlueskyService.sync()
    http = FakeSyncHTTP(
        json_responses={
            "public.api.bsky.app/xrpc/app.bsky.actor.getProfile": {
                "did": "did:plc:12345abcdef",
            },
            "public.api.bsky.app/xrpc/app.bsky.feed.getPostThread": {
                "thread": {
                    "post": {
                        "author": {
                            "displayName": "Bluesky Author",
                            "handle": "author.bsky.social",
                        },
                        "record": {
                            "text": "Hello Bluesky sync worker!",
                        },
                        "replyCount": 3,
                        "repostCount": 7,
                        "likeCount": 42,
                        "embed": {
                            "$type": "app.bsky.embed.images#view",
                            "images": [
                                {
                                    "fullsize": "https://cdn.bsky.app/img/full.jpg",
                                    "alt": "Bluesky photo",
                                }
                            ],
                        },
                    }
                }
            },
        }
    )
    url = "https://bsky.app/profile/author.bsky.social/post/3mlmib6xdbc2a"
    payload = service.extract_sync(url, http)
    assert payload is not None
    assert payload.author_name == "Bluesky Author"
    assert payload.author_handle == "author.bsky.social"
    assert payload.content == "Hello Bluesky sync worker!"
    assert len(payload.media_items) == 1
    assert payload.media_items[0].url == "https://cdn.bsky.app/img/full.jpg"
    assert payload.media_items[0].is_video is False
    assert "💬 3" in payload.footer_text
    assert "🔄 7" in payload.footer_text
    assert "❤️ 42" in payload.footer_text
