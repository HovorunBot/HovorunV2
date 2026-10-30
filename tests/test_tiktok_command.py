"""Tests for TikTok rich media command."""

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
async def tiktok_command(init_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the TikTok RichMediaCommand instance."""
    return await get_media_command(init_container, CommandName.TIKTOK)


@pytest.mark.vcr
async def test_handle_tiktok_video(tiktok_command: RichMediaCommand, init_container: AsyncContainer) -> None:
    """Test handling a TikTok video link using real extraction and VCR."""
    chat_id = 456
    url = "https://www.tiktok.com/@fueltothe_max/video/7397394517311212818"
    message = create_private_media_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(init_container, chat_id)

    await tiktok_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert kwargs["chat_id"] == chat_id
    assert "Mock User" in kwargs["media"][0].caption
    assert "tiktok.com" in kwargs["media"][0].caption
