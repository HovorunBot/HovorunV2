"""Tests for the TikTokCommand class."""

from http import HTTPStatus
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User
from dishka import AsyncContainer

from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.interface.telegram.handlers.tiktok import TikTokCommand


@pytest.fixture
async def tiktok_command(init_container: AsyncContainer) -> TikTokCommand:
    """Fixture to provide a TikTokCommand instance."""
    return await init_container.get(TikTokCommand)


def create_mock_message(text: str | None, is_bot: bool = False, chat_id: int = 456) -> MagicMock:
    """Create a mock Telegram message."""
    message = MagicMock(spec=Message)
    message.text = text
    message.message_id = 12345
    message.from_user = MagicMock(spec=User)
    message.from_user.id = 123
    message.from_user.is_bot = is_bot
    message.from_user.full_name = "Mock User"
    message.chat = MagicMock(spec=Chat)
    message.chat.id = chat_id
    message.answer = AsyncMock()
    message.delete = AsyncMock()
    return message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://www.tiktok.com/@fueltothe_max/video/7397394517311212818", True),
        ("Check this: https://vm.tiktok.com/ZMrBy9vGj/ cool right?", True),
        ("https://tiktok.com/@user/video/1234567890", True),
        ("Just a random text", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(
    tiktok_command: TikTokCommand,
    text: str | None,
    expected: bool,
    init_container: AsyncContainer,  # noqa: ARG001
) -> None:
    """Test the is_triggered method."""
    message = create_mock_message(text)
    assert await tiktok_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_is_triggered_by_bot(tiktok_command: TikTokCommand, init_container: AsyncContainer) -> None:  # noqa: ARG001
    """Test that it's not triggered by bot messages."""
    text = "https://www.tiktok.com/@fueltothe_max/video/7397394517311212818"
    message = create_mock_message(text, is_bot=True)
    assert await tiktok_command.is_triggered(message) is False


@pytest.mark.asyncio
async def test_handle_tiktok_video(tiktok_command: TikTokCommand, init_container: AsyncContainer) -> None:
    """Test handling a TikTok video link with real service logic and mocked network."""
    chat_id = 456
    url = "https://www.tiktok.com/@user/video/1234567890"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Whitelist the chat first (real DB)
    whitelist_service = await init_container.get(WhitelistService)
    await whitelist_service.add_to_whitelist(chat_id)

    # Mock TikWM API response
    tiktok_response = {
        "code": 0,
        "msg": "success",
        "data": {
            "title": "Funny Video #funny #tiktok",
            "play": "https://example.com/video.mp4",
            "digg_count": 1000,
            "play_count": 5000,
            "author": {"nickname": "TikTok User", "unique_id": "tiktok_user"},
        },
    }

    # Mock Translation API response
    translation_response = [[["Funny Video", "Funny Video", None, None, 1]], None, "en"]

    def mocked_get(url_: str, **_kwargs: Any) -> MagicMock:  # noqa: ANN401
        mock_resp = MagicMock()
        mock_resp.status = HTTPStatus.OK
        if "tikwm.com" in str(url_):
            mock_resp.json = AsyncMock(return_value=tiktok_response)
        elif "translate.googleapis.com" in str(url_):
            mock_resp.json = AsyncMock(return_value=translation_response)
        else:
            mock_resp.status = HTTPStatus.NOT_FOUND
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        return mock_resp

    session = await init_container.get(aiohttp.ClientSession)
    with patch("aiohttp.ClientSession.get", side_effect=mocked_get):
        await tiktok_command.handle(message, cast("Bot", bot), session=session)

    # Verify interaction
    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert kwargs["chat_id"] == chat_id
    assert "TikTok User" in kwargs["media"][0].caption
    assert "Funny Video" in kwargs["media"][0].caption
