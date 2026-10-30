"""Tests for Threads rich media command."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command

EXPECTED_MEDIA_COUNT: int = 1


@pytest.fixture
async def threads_command(test_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Threads RichMediaCommand instance."""
    return await get_media_command(test_container, CommandName.THREADS)


@pytest.mark.slow
@pytest.mark.vcr
async def test_handle_threads_post(threads_command: RichMediaCommand, test_container: AsyncContainer) -> None:
    """Test handling a Threads post using real extraction and VCR."""
    chat_id = 456
    url = "https://www.threads.net/@maksymtarkivskyy/post/DYOeEaaD-vR"
    message = create_private_media_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    await threads_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert kwargs["chat_id"] == chat_id
    assert "Mock User" in kwargs["media"][0].caption
    assert "threads.net" in kwargs["media"][0].caption
    assert len(kwargs["media"]) >= EXPECTED_MEDIA_COUNT
