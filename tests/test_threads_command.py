"""Tests for Threads rich media command."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import create_mock_message, get_media_command

EXPECTED_MEDIA_COUNT: int = 1


@pytest.fixture
async def threads_command(init_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Threads RichMediaCommand instance."""
    return await get_media_command(init_container, CommandName.THREADS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://www.threads.net/@zuck/post/CuW6-7Ky5jG", True),
        ("https://www.threads.com/@zuck/post/CuW6-7Ky5jG", True),
        ("https://threads.net/@user/post/123456", True),
        ("https://threads.com/@user/post/123456", True),
        ("http://threads.net/t/C_xyz123", True),
        ("http://threads.com/t/C_xyz123", True),
        ("https://www.threads.net/t/CuW6-7Ky5jG", True),
        ("https://www.threads.com/t/CuW6-7Ky5jG", True),
        ("No link here", False),
        ("Invalid link: https://threads.net/user/12345", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(
    threads_command: RichMediaCommand,
    text: str | None,
    expected: bool,
) -> None:
    """Test the is_triggered method with various inputs."""
    message = create_mock_message(text)
    assert await threads_command.is_triggered(message) == expected


@pytest.mark.asyncio
@pytest.mark.vcr
async def test_handle_threads_post(threads_command: RichMediaCommand, init_container: AsyncContainer) -> None:
    """Test handling a Threads post using real extraction and VCR."""
    chat_id = 456
    url = "https://www.threads.net/@maksymtarkivskyy/post/DYOeEaaD-vR"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)

    await threads_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert kwargs["chat_id"] == chat_id
    assert "Mock User" in kwargs["media"][0].caption
    assert "threads.net" in kwargs["media"][0].caption
    assert len(kwargs["media"]) >= EXPECTED_MEDIA_COUNT
