"""Tests for the NotificationService."""

from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.data.system_service import SystemDataService
from hovorunv2.application.services.notification_service import NotificationService
from hovorunv2.application.services.whitelist_service import WhitelistService


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
    whitelist_service = await init_container.get(WhitelistService)

    # Ensure last_notified is None
    assert await system_service.get_last_notified_version() is None

    # Mock a whitelisted chat
    chat_id = 12345
    await whitelist_service.add_to_whitelist(chat_id)

    bot = AsyncMock(spec=Bot)

    # Mock changelog to return some updates for 0.1.0
    with patch(
        "hovorunv2.application.services.notification_service.get_current_version", return_value="0.1.0"
    ), patch(
        "hovorunv2.application.services.notification_service.get_changelog_updates", return_value="Initial features"
    ) as mock_updates:
        await notification_service.notify_updates(bot)

        # Should check updates from 0.0.0 to 0.1.0
        mock_updates.assert_called_once_with("0.0.0", "0.1.0")

        # Should send message to whitelisted chat
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
    whitelist_service = await init_container.get(WhitelistService)
    await system_service.set_last_notified_version("0.1.0")
    await whitelist_service.add_to_whitelist(555)

    bot = AsyncMock(spec=Bot)

    with patch(
        "hovorunv2.application.services.notification_service.get_current_version", return_value="0.2.0"
    ), patch("hovorunv2.application.services.notification_service.get_changelog_updates", return_value="New stuff"):
        await notification_service.notify_updates(bot)

        bot.send_message.assert_called_once()
        assert "v0.2.0" in bot.send_message.call_args[1]["text"]
        assert await system_service.get_last_notified_version() == "0.2.0"
