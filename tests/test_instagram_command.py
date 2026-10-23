"""Tests for Instagram rich media command."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.domain.enums import ChatStatus, CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import create_mock_message, get_media_command


@pytest.fixture
async def instagram_command(init_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Instagram RichMediaCommand instance."""
    return await get_media_command(init_container, CommandName.INSTAGRAM)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Check this post: https://www.instagram.com/p/C-2v0r0nQ1A/", True),
        ("Check this reel: https://www.instagram.com/reels/C-2v0r0nQ1A/", True),
        ("Check this tv: https://www.instagram.com/tv/C-2v0r0nQ1A/", True),
        ("No link here", False),
        ("Invalid link: https://instagram.com/user/", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(
    instagram_command: RichMediaCommand,
    text: str | None,
    expected: bool,
) -> None:
    """Test the is_triggered method with various inputs."""
    message = create_mock_message(text)
    assert await instagram_command.is_triggered(message) == expected


@pytest.mark.asyncio
@pytest.mark.vcr
async def test_handle_instagram_post(instagram_command: RichMediaCommand, init_container: AsyncContainer) -> None:
    """Test handling an Instagram post using real extraction and VCR."""
    chat_id = 789
    url = "https://www.instagram.com/p/DYPIeHDoZ-w/"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

    await instagram_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert kwargs["chat_id"] == chat_id
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "instagram.com" in caption


@pytest.mark.asyncio
@pytest.mark.vcr
async def test_handle_instagram_reel(instagram_command: RichMediaCommand, init_container: AsyncContainer) -> None:
    """Test handling an Instagram reel using real extraction and VCR."""
    chat_id = 789
    url = "https://www.instagram.com/reels/DVMKxZHggx7/"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

    await instagram_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "instagram.com" in caption


@pytest.mark.asyncio
async def test_handle_instagram_browser_fallback(
    instagram_command: RichMediaCommand, init_container: AsyncContainer
) -> None:
    """Test handling an Instagram post using browser fallback when other services fail."""
    chat_id = 789
    url = "https://www.instagram.com/p/C-2v0r0nQ1A/"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

    session = await init_container.get(aiohttp.ClientSession)

    payload = RichMediaPayload(
        author_name="Instagram User",
        author_handle="instagram",
        author_url="https://www.instagram.com/",
        content="Fallback Title\n\nFallback Description",
        footer_text="📷 Instagram (Browser Fallback)",
        original_url=url,
        media_items=[MediaItem(url="https://example.com/fallback.jpg", is_video=False)],
    )

    with (
        patch("instaloader.Post.from_shortcode", side_effect=Exception("Instaloader failed")),
        patch("hovorunv2.application.media.extractor.MediaExtractor.extract_payload", return_value=None),
        patch(
            "hovorunv2.infrastructure.browser.BrowserService.extract_and_download",
            return_value=(payload, [b"fake_image_bytes"]),
        ),
    ):
        await instagram_command.handle(message, cast("Bot", bot), session=session)

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Fallback Title" in caption
    assert "Fallback Description" in caption
    assert "📷 Instagram" in caption
