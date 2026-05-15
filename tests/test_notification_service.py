"""Tests for the NotificationService."""

from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.data.constants import ChatStatus
from hovorunv2.application.data.system_service import SystemDataService
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.notification_service import NotificationService


@pytest.fixture
async def notification_service(init_container: AsyncContainer) -> NotificationService:
    """Fixture to provide a NotificationService instance."""
    return await init_container.get(NotificationService)


@pytest.mark.asyncio
async def test_notify_updates_fresh_install(
    notification_service: NotificationService, init_container: AsyncContainer
) -> None:
    """Test that fresh install (last_notified is None) triggers notification for initial version."""
    system_service = await init_container.get(SystemDataService)
    chat_status_service = await init_container.get(ChatStatusService)

    # Ensure last_notified is None
    assert await system_service.get_last_notified_version() is None

    # Mock a approved chat
    chat_id = 12345
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

    bot = AsyncMock(spec=Bot)

    # Mock changelog to return some updates for 0.1.0
    with (
        patch("hovorunv2.application.services.notification_service.get_current_version", return_value="0.1.0"),
        patch(
            "hovorunv2.application.services.notification_service.get_changelog_updates", return_value="Initial features"
        ) as mock_updates,
    ):
        await notification_service.notify_updates(bot)

        # Should check updates from 0.0.0 to 0.1.0
        mock_updates.assert_called_once_with("0.0.0", "0.1.0")

        # Should send message to approved chat
        bot.send_message.assert_called_once()
        _, kwargs = bot.send_message.call_args
        assert kwargs["chat_id"] == chat_id
        assert "v0.1.0" in kwargs["text"]
        assert "Initial features" in kwargs["text"]

        # Should update last_notified_version in DB
        assert await system_service.get_last_notified_version() == "0.1.0"


@pytest.mark.asyncio
async def test_notify_updates_no_new_version(
    notification_service: NotificationService, init_container: AsyncContainer
) -> None:
    """Test that no notification is sent if version hasn't changed."""
    system_service = await init_container.get(SystemDataService)
    await system_service.set_last_notified_version("0.1.0")

    bot = AsyncMock(spec=Bot)

    with patch("hovorunv2.application.services.notification_service.get_current_version", return_value="0.1.0"):
        await notification_service.notify_updates(bot)
        bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_notify_updates_upgrade(
    notification_service: NotificationService, init_container: AsyncContainer
) -> None:
    """Test notification on version upgrade."""
    system_service = await init_container.get(SystemDataService)
    chat_status_service = await init_container.get(ChatStatusService)
    await system_service.set_last_notified_version("0.1.0")
    await chat_status_service.set_status(555, ChatStatus.APPROVED)

    bot = AsyncMock(spec=Bot)

    with (
        patch("hovorunv2.application.services.notification_service.get_current_version", return_value="0.2.0"),
        patch("hovorunv2.application.services.notification_service.get_changelog_updates", return_value="New stuff"),
    ):
        await notification_service.notify_updates(bot)

        bot.send_message.assert_called_once()
        assert "v0.2.0" in bot.send_message.call_args[1]["text"]
        assert await system_service.get_last_notified_version() == "0.2.0"
