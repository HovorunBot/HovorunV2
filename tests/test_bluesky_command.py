"""Tests for Bluesky rich media command."""

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
async def bluesky_command(init_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Bluesky RichMediaCommand instance."""
    return await get_media_command(init_container, CommandName.BLUESKY)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://bsky.app/profile/danabra.mov/post/3lj6v5szvj22a", True),
        ("Check this: https://www.bsky.app/profile/user.bsky.social/post/rkey123", True),
        ("No link here", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(
    bluesky_command: RichMediaCommand,
    text: str | None,
    expected: bool,
) -> None:
    """Test the is_triggered method with various inputs."""
    message = create_mock_message(text)
    assert await bluesky_command.is_triggered(message) == expected


@pytest.mark.asyncio
@pytest.mark.vcr
async def test_handle_bluesky_post(bluesky_command: RichMediaCommand, init_container: AsyncContainer) -> None:
    """Test handling a Bluesky post using real extraction and VCR."""
    chat_id = 789
    url = "https://bsky.app/profile/josiahcohen.bsky.social/post/3mlmib6xdbc2a"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

    await bluesky_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "bsky.app" in caption


@pytest.mark.asyncio
@pytest.mark.vcr
async def test_handle_bluesky_quote_with_media(
    bluesky_command: RichMediaCommand, init_container: AsyncContainer
) -> None:
    """Test handling a Bluesky quote post with media using real extraction and VCR."""
    chat_id = 789
    url = "https://bsky.app/profile/hikkifox.bsky.social/post/3mlnxm3on6s2x"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

    await bluesky_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "bsky.app" in caption
