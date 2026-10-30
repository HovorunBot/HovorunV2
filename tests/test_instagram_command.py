"""Tests for Instagram rich media command."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.clients.instagram import InstagramService
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command
from tests.utils.sync_http import HTTPFixture, HTTPReply


@pytest.fixture
async def instagram_command(init_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Instagram RichMediaCommand instance."""
    return await get_media_command(init_container, CommandName.INSTAGRAM)


@pytest.mark.slow
@pytest.mark.vcr
async def test_handle_instagram_post(instagram_command: RichMediaCommand, init_container: AsyncContainer) -> None:
    """Test handling an Instagram post using real extraction and VCR."""
    chat_id = 789
    url = "https://www.instagram.com/p/DYPIeHDoZ-w/"
    message = create_private_media_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(init_container, chat_id)

    await instagram_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert kwargs["chat_id"] == chat_id
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "instagram.com" in caption


@pytest.mark.vcr
async def test_handle_instagram_reel(instagram_command: RichMediaCommand, init_container: AsyncContainer) -> None:
    """Test handling an Instagram reel using real extraction and VCR."""
    chat_id = 789
    url = "https://www.instagram.com/reels/DVMKxZHggx7/"
    message = create_private_media_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(init_container, chat_id)

    await instagram_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "instagram.com" in caption


@pytest.mark.browser
@pytest.mark.slow
async def test_instagram_browser_fallback_uses_real_dom_and_media(init_container: AsyncContainer) -> None:
    """Real browser extraction downloads loopback media without replacing an internal strategy/service."""
    service = await init_container.get(InstagramService)
    with HTTPFixture({"/image.jpg": HTTPReply(b"synthetic image")}) as media:
        document = (
            '<html><head><meta property="og:title" content="Synthetic fallback">'
            f'<meta property="og:image" content="{media.url("/image.jpg")}">'
            "</head><body><main>Synthetic fallback</main></body></html>"
        )
        with HTTPFixture({"/post": HTTPReply(document.encode(), headers={"Content-Type": "text/html"})}) as server:
            payload = await service.extract_fallback(server.url("/post"))
            assert payload is not None
            assert "Synthetic fallback" in payload.content
            assert payload.media_items[0].data == b"synthetic image"
            assert payload.media_items[0].url == media.url("/image.jpg")
