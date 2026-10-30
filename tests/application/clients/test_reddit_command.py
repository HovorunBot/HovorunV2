"""Tests for Reddit rich media command."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command

EXPECTED_GALLERY_COUNT: int = 2


@pytest.fixture
async def reddit_command(test_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Reddit RichMediaCommand instance."""
    return await get_media_command(test_container, CommandName.REDDIT)


async def test_handle_reddit_gallery_post(reddit_command: RichMediaCommand, test_container: AsyncContainer) -> None:
    """Test handling a Reddit multi-image gallery post."""
    chat_id = 456
    url = "https://www.reddit.com/r/pics/comments/1abcde/gallery_test/"

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

    @asynccontextmanager
    async def mock_get(req_url: str, **_kwargs: Any) -> AsyncGenerator[MagicMock]:
        resp = MagicMock()
        resp.status = HTTPStatus.OK
        if "vxreddit.com" in req_url:
            resp.text = AsyncMock(return_value=mock_html)
        else:
            resp.json = AsyncMock(return_value=[[[""]], None, "en"])
        yield resp

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    message = create_private_media_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    with patch.object(aiohttp.ClientSession, "get", side_effect=mock_get):
        await reddit_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    media = kwargs["media"]
    assert len(media) == EXPECTED_GALLERY_COUNT
    assert "Awesome Gallery Post" in media[0].caption
    assert "reddit_user (r/pics)" in media[0].caption
    assert "⬆️ 1.5K" in media[0].caption


async def test_handle_reddit_video_post_respects_split_toggle(
    reddit_command: RichMediaCommand, test_container: AsyncContainer
) -> None:
    """Test that Reddit video post is skipped when reddit_video is disabled."""
    chat_id = 987
    url = "https://www.reddit.com/r/videos/comments/1xyz99/funny_clip/"

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

    @asynccontextmanager
    async def mock_get(req_url: str, **_kwargs: Any) -> AsyncGenerator[MagicMock]:
        resp = MagicMock()
        resp.status = HTTPStatus.OK
        if "vxreddit.com" in req_url:
            resp.text = AsyncMock(return_value=mock_html)
        else:
            resp.json = AsyncMock(return_value=[[[""]], None, "en"])
        yield resp

    chat_status_service = await test_container.get(ChatStatusService)
    command_service = await test_container.get(CommandService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)
    await command_service.enable_command(chat_id, CommandName.REDDIT)
    await command_service.disable_command(chat_id, CommandName.REDDIT_VIDEO)

    message = create_private_media_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    with patch.object(aiohttp.ClientSession, "get", side_effect=mock_get):
        await reddit_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_not_called()
    message.answer.assert_not_called()

    await command_service.enable_command(chat_id, CommandName.REDDIT_VIDEO)
    with patch.object(aiohttp.ClientSession, "get", side_effect=mock_get):
        await reddit_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()


@pytest.mark.vcr
async def test_handle_reddit_post_vcr(reddit_command: RichMediaCommand, test_container: AsyncContainer) -> None:
    """Test handling a real Reddit post via vxReddit using VCR."""
    chat_id = 456
    url = "https://www.reddit.com/r/DotA2/comments/1xbq3id/please_bring_back_blood_mist_aghs_for_seeker_and/"
    message = create_private_media_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    await reddit_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    media = kwargs["media"]
    assert len(media) == 1
    assert not media[0].media.startswith("https://v.redd.it")
    assert "Lycanthrope- (r/DotA2)" in media[0].caption
    assert "Please bring back blood mist aghs for seeker" in media[0].caption
