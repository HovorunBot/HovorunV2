"""Tests for Facebook rich media command."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command


@pytest.fixture
async def facebook_command(test_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Facebook RichMediaCommand instance."""
    return await get_media_command(test_container, CommandName.FACEBOOK)


@pytest.mark.vcr
async def test_handle_facebook_photo_post(facebook_command: RichMediaCommand, test_container: AsyncContainer) -> None:
    """Test handling a Facebook photo post using real extraction and VCR."""
    chat_id = 789
    url = "https://www.facebook.com/share/p/1B37pk4bUf/"
    message = create_private_media_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    await facebook_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "facebook.com" in caption


@pytest.mark.vcr
async def test_handle_facebook_video_fallback(
    facebook_command: RichMediaCommand, test_container: AsyncContainer
) -> None:
    """Test handling a Facebook video using real extraction and VCR (yt-dlp fallback)."""
    chat_id = 789
    url = "https://www.facebook.com/share/v/1NBDyTuecs/"
    message = create_private_media_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    await facebook_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "facebook.com" in caption
