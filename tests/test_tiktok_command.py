"""Tests for TikTok rich media command."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.data.constants import ChatStatus, CommandName
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import create_mock_message, get_media_command


@pytest.fixture
async def tiktok_command(init_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the TikTok RichMediaCommand instance."""
    return await get_media_command(init_container, CommandName.TIKTOK)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://www.tiktok.com/@fueltothe_max/video/7397394517311212818", True),
        ("Check this: https://vm.tiktok.com/ZMrBy9vGj/ cool right?", True),
        ("https://tiktok.com/@user/video/1234567890", True),
        ("https://vt.tiktok.com/ZS9fuFD3q/", True),
        ("https://www.tiktok.com/t/ZS9fuFD3q/", True),
        ("Just a random text", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(
    tiktok_command: RichMediaCommand,
    text: str | None,
    expected: bool,
) -> None:
    """Test the is_triggered method."""
    message = create_mock_message(text)
    assert await tiktok_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_is_triggered_by_bot(tiktok_command: RichMediaCommand) -> None:
    """Test that it's not triggered by bot messages."""
    text = "https://www.tiktok.com/@fueltothe_max/video/7397394517311212818"
    message = create_mock_message(text, is_bot=True)
    assert await tiktok_command.is_triggered(message) is False


@pytest.mark.asyncio
@pytest.mark.vcr
async def test_handle_tiktok_video(tiktok_command: RichMediaCommand, init_container: AsyncContainer) -> None:
    """Test handling a TikTok video link using real extraction and VCR."""
    chat_id = 456
    url = "https://www.tiktok.com/@fueltothe_max/video/7397394517311212818"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

    await tiktok_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert kwargs["chat_id"] == chat_id
    assert "Mock User" in kwargs["media"][0].caption
    assert "tiktok.com" in kwargs["media"][0].caption
