"""Tests for the NotificationService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
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
async def test_notify_updates_fresh_install_silent(
    notification_service: NotificationService, init_container: AsyncContainer
) -> None:
    """Test that fresh install (last_notified is None) records version silently without messages."""
    system_service = await init_container.get(SystemDataService)
    chat_status_service = await init_container.get(ChatStatusService)

    # Ensure last_notified is None initially
    assert await system_service.get_last_notified_version() is None

    # Mock an approved group chat
    group_chat_id = -10012345
    await chat_status_service.set_status(group_chat_id, ChatStatus.APPROVED)

    bot = AsyncMock(spec=Bot)

    with (
        patch("hovorunv2.application.services.notification_service.get_current_version", return_value="0.1.0"),
        patch("hovorunv2.application.services.notification_service.get_changelog_updates") as mock_updates,
    ):
        await notification_service.notify_updates(bot)

        # Must not fetch changelog or send any message on fresh install
        mock_updates.assert_not_called()
        bot.send_message.assert_not_called()

        # Must update last_notified_version in DB
        assert await system_service.get_last_notified_version() == "0.1.0"


@pytest.mark.asyncio
async def test_notify_updates_group_chats_only(
    notification_service: NotificationService, init_container: AsyncContainer
) -> None:
    """Test that only group/supergroup chats (chat_id < 0) are notified upon upgrade."""
    system_service = await init_container.get(SystemDataService)
    chat_status_service = await init_container.get(ChatStatusService)

    await system_service.set_last_notified_version("0.1.0")

    # Set up one private chat (> 0) and one group chat (< 0)
    private_chat_id = 99999
    group_chat_id = -10088888
    await chat_status_service.set_status(private_chat_id, ChatStatus.APPROVED)
    await chat_status_service.set_status(group_chat_id, ChatStatus.APPROVED)

    bot = AsyncMock(spec=Bot)

    with (
        patch("hovorunv2.application.services.notification_service.get_current_version", return_value="0.2.0"),
        patch(
            "hovorunv2.application.services.notification_service.get_changelog_updates",
            return_value="✨ **Media**: New feature",
        ),
    ):
        await notification_service.notify_updates(bot)

        # Only group chat receives message
        bot.send_message.assert_called_once()
        _, kwargs = bot.send_message.call_args
        assert kwargs["chat_id"] == group_chat_id
        assert "v0.2.0" in kwargs["text"]
        assert "New feature" in kwargs["text"]
        assert await system_service.get_last_notified_version() == "0.2.0"


@pytest.mark.asyncio
async def test_notify_updates_no_new_version(
    notification_service: NotificationService, init_container: AsyncContainer
) -> None:
    """Test that no notification is sent if version hasn't increased."""
    system_service = await init_container.get(SystemDataService)
    chat_status_service = await init_container.get(ChatStatusService)

    await system_service.set_last_notified_version("0.2.0")
    await chat_status_service.set_status(-100111, ChatStatus.APPROVED)

    bot = AsyncMock(spec=Bot)

    # Identical version
    with patch("hovorunv2.application.services.notification_service.get_current_version", return_value="0.2.0"):
        await notification_service.notify_updates(bot)
        bot.send_message.assert_not_called()

    # Downgrade
    with patch("hovorunv2.application.services.notification_service.get_current_version", return_value="0.1.9"):
        await notification_service.notify_updates(bot)
        bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_notify_updates_retry_uses_html_and_recovers(
    notification_service: NotificationService, init_container: AsyncContainer
) -> None:
    """Test that retry after rate limit sends HTML formatted message and catches errors."""
    system_service = await init_container.get(SystemDataService)
    chat_status_service = await init_container.get(ChatStatusService)

    await system_service.set_last_notified_version("0.1.0")
    group_chat_id = -100999
    await chat_status_service.set_status(group_chat_id, ChatStatus.APPROVED)

    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock(
        side_effect=[
            TelegramRetryAfter(method=MagicMock(), message="Rate limit", retry_after=0),
            Exception("Retry failed"),
        ]
    )

    with (
        patch("hovorunv2.application.services.notification_service.get_current_version", return_value="0.2.0"),
        patch("hovorunv2.application.services.notification_service.get_changelog_updates", return_value="New features"),
        patch("asyncio.sleep", AsyncMock()),
    ):
        await notification_service.notify_updates(bot)

    assert bot.send_message.call_count == 2
    for call in bot.send_message.call_args_list:
        assert call.kwargs["parse_mode"] == "HTML"
        assert call.kwargs["disable_web_page_preview"] is True
