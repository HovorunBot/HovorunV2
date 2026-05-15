"""Tests for the VisionCommand class."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Message, PhotoSize

from hovorunv2.application.services.vision_service import VisionService
from hovorunv2.interface.telegram.handlers.vision import VisionCommand


@pytest.fixture
async def vision_service() -> MagicMock:
    """Mock vision service."""
    service = MagicMock(spec=VisionService)
    service.describe_image = AsyncMock(return_value="A beautiful landscape.")
    return service


@pytest.fixture
def vision_command(vision_service: MagicMock) -> VisionCommand:
    """Create vision command with mock service."""
    return VisionCommand(vision_service)


@pytest.mark.asyncio
async def test_vision_triggered(vision_command: VisionCommand) -> None:
    """Test vision command triggering."""
    message = MagicMock(spec=Message)
    message.text = "/vision"
    message.caption = None
    assert await vision_command.is_triggered(message) is True

    # Caption test
    message.text = None
    message.caption = "/vision"
    assert await vision_command.is_triggered(message) is True

    message.text = "/other"
    message.caption = None
    assert await vision_command.is_triggered(message) is False


@pytest.mark.asyncio
async def test_handle_vision_no_photo(vision_command: VisionCommand) -> None:
    """Test vision command without photo."""
    message = MagicMock(spec=Message)
    message.photo = None
    message.document = None
    message.reply_to_message = None
    message.reply = AsyncMock()

    bot = MagicMock(spec=Bot)

    await vision_command.handle(message, bot)

    message.reply.assert_called_once()
    assert "Please send an image" in message.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_vision_with_photo(vision_command: VisionCommand, vision_service: MagicMock) -> None:
    """Test vision command with attached photo."""
    photo = MagicMock(spec=PhotoSize)
    photo.file_id = "file123"

    message = MagicMock(spec=Message)
    message.photo = [photo]
    message.document = None
    message.reply = AsyncMock()
    # Mock reply message for status update
    status_msg = MagicMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    message.reply.return_value = status_msg

    bot = MagicMock(spec=Bot)
    bot.download = AsyncMock()

    await vision_command.handle(message, bot)

    bot.download.assert_called_once()
    vision_service.describe_image.assert_called_once()
    status_msg.edit_text.assert_called_with("A beautiful landscape.")
