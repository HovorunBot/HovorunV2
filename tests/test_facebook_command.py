"""Tests for the FacebookCommand class."""

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User

from hovorunv2.interface.telegram.handlers.facebook import FacebookCommand

if TYPE_CHECKING:
    from hovorunv2.infrastructure.container import Container


@pytest.fixture
def facebook_command() -> FacebookCommand:
    """Fixture to provide a FacebookCommand instance."""
    return FacebookCommand()


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
        ("https://www.facebook.com/user/posts/12345", True),
        ("https://www.facebook.com/groups/id/posts/12345", True),
        ("https://www.facebook.com/permalink.php?story_fbid=123", True),
        ("https://www.facebook.com/photo.php?fbid=123", True),
        ("https://www.facebook.com/reel/123", True),
        ("https://www.facebook.com/watch/?v=123", True),
        ("https://www.facebook.com/share/p/1FuWE1yAik/", True),
        ("No link here", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(
    facebook_command: FacebookCommand,
    text: str | None,
    expected: bool,
    init_container: Container,  # noqa: ARG001
) -> None:
    """Test the is_triggered method with various inputs."""
    message = create_mock_message(text)
    assert await facebook_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_handle_facebook_photo_post(facebook_command: FacebookCommand, init_container: Container) -> None:
    """Test handling a Facebook photo post using OG tags."""
    chat_id = 789
    url = "https://www.facebook.com/photo.php?fbid=123"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Whitelist the chat
    await init_container.whitelist_service.add_to_whitelist(chat_id)

    # Mock HTML with OG tags
    html_content = """
    <html>
        <meta property="og:title" content="John Doe - Beautiful Day">
        <meta property="og:description" content="What a lovely day at the beach!">
        <meta property="og:image" content="https://example.com/beach.jpg">
        <meta property="og:site_name" content="Facebook">
        <meta property="og:type" content="article">
    </html>
    """

    # Mock Translation API response
    translation_response = [[["What a lovely day", "What a lovely day", None, None, 1]], None, "en"]

    def mocked_get(url_: str, **_kwargs: Any) -> MagicMock:  # noqa: ANN401
        mock_resp = MagicMock()
        mock_resp.status = HTTPStatus.OK
        if "translate.googleapis.com" in str(url_):
            mock_resp.json = AsyncMock(return_value=translation_response)
        else:
            mock_resp.text = AsyncMock(return_value=html_content)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        return mock_resp

    with patch("aiohttp.ClientSession.get", side_effect=mocked_get):
        await facebook_command.handle(message, cast("Bot", bot))

    # Verify interaction
    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert "John Doe" in kwargs["media"][0].caption
    assert "lovely day" in kwargs["media"][0].caption
    assert "Original post on Facebook" in kwargs["media"][0].caption
    assert kwargs["media"][0].media == "https://example.com/beach.jpg"


@pytest.mark.asyncio
async def test_handle_facebook_video_fallback(facebook_command: FacebookCommand, init_container: Container) -> None:
    """Test handling a Facebook video with yt-dlp fallback."""
    chat_id = 789
    url = "https://www.facebook.com/reel/123"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Whitelist the chat
    await init_container.whitelist_service.add_to_whitelist(chat_id)

    # Mock OG tags suggesting video
    html_content = """
    <html>
        <meta property="og:type" content="video">
        <meta property="og:title" content="Funny Video">
    </html>
    """

    # Mock yt-dlp extraction info
    ytdlp_info = {
        "title": "Funny Video",
        "url": "https://example.com/video.mp4",
        "uploader": "Comedy Central",
        "uploader_id": "comedycentral",
        "like_count": 1000,
        "view_count": 50000,
        "formats": [{"url": "https://example.com/video.mp4", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a"}],
    }

    def mocked_get(url_: str, **_kwargs: Any) -> MagicMock:  # noqa: ANN401,ARG001
        mock_resp = MagicMock()
        mock_resp.status = HTTPStatus.OK
        mock_resp.text = AsyncMock(return_value=html_content)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        return mock_resp

    with (
        patch("aiohttp.ClientSession.get", side_effect=mocked_get),
        patch("asyncio.to_thread", return_value=ytdlp_info),
    ):
        await facebook_command.handle(message, cast("Bot", bot))

    # Verify interaction
    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert "Original reel" in kwargs["media"][0].caption
    assert "Comedy Central" in kwargs["media"][0].caption
    assert "50K" in kwargs["media"][0].caption  # view_count
