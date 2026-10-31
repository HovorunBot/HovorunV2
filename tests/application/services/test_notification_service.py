"""Tests for the NotificationService."""

from functools import partial

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage
from dishka import AsyncContainer

import hovorunv2.application.operations.notification_service as notification_module
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.operations.notification_service import NotificationService
from hovorunv2.application.ports import SystemStore
from hovorunv2.domain.enums import ChatStatus


def _constant(value: str, *args: object) -> str:
    return value


def _unexpected_changelog(*args: object) -> str:
    msg = "Fresh installation must not read changelog"
    raise AssertionError(msg)


@pytest.fixture
async def notification_service(test_container: AsyncContainer) -> NotificationService:
    """Fixture to provide a NotificationService instance."""
    return await test_container.get(NotificationService)


async def test_notify_updates_fresh_install_silent(
    notification_service: NotificationService,
    test_container: AsyncContainer,
    recording_bot: Bot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that fresh install (last_notified is None) records version silently without messages."""
    system_service = await test_container.get(SystemStore)
    chat_status_service = await test_container.get(ChatStatusService)

    # Ensure last_notified is None initially
    assert await system_service.get_last_notified_version() is None

    # Mock an approved group chat
    group_chat_id = -10012345
    await chat_status_service.set_status(group_chat_id, ChatStatus.APPROVED)

    bot = recording_bot

    monkeypatch.setattr(notification_module, "get_current_version", partial(_constant, "0.1.0"))
    monkeypatch.setattr(notification_module, "get_changelog_updates", _unexpected_changelog)
    await notification_service.notify_updates(bot)

    # Must not fetch changelog or send any message on fresh install
    assert bot.session.calls == []
    assert bot.session.calls == []

    # Must update last_notified_version in DB
    assert await system_service.get_last_notified_version() == "0.1.0"


async def test_notify_updates_group_chats_only(
    notification_service: NotificationService,
    test_container: AsyncContainer,
    recording_bot: Bot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that only group/supergroup chats (chat_id < 0) are notified upon upgrade."""
    system_service = await test_container.get(SystemStore)
    chat_status_service = await test_container.get(ChatStatusService)

    await system_service.set_last_notified_version("0.1.0")

    # Set up one private chat (> 0) and one group chat (< 0)
    private_chat_id = 99999
    group_chat_id = -10088888
    await chat_status_service.set_status(private_chat_id, ChatStatus.APPROVED)
    await chat_status_service.set_status(group_chat_id, ChatStatus.APPROVED)

    bot = recording_bot

    monkeypatch.setattr(notification_module, "get_current_version", partial(_constant, "0.2.0"))
    monkeypatch.setattr(notification_module, "get_changelog_updates", partial(_constant, "✨ **Media**: New feature"))
    await notification_service.notify_updates(bot)

    # Only group chat receives message
    assert len(bot.session.calls) == 1
    kwargs = bot.session.calls[0].model_dump()
    assert kwargs["chat_id"] == group_chat_id
    assert "v0.2.0" in kwargs["text"]
    assert "New feature" in kwargs["text"]
    assert await system_service.get_last_notified_version() == "0.2.0"


async def test_notify_updates_no_new_version(
    notification_service: NotificationService,
    test_container: AsyncContainer,
    recording_bot: Bot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that no notification is sent if version hasn't increased."""
    system_service = await test_container.get(SystemStore)
    chat_status_service = await test_container.get(ChatStatusService)

    await system_service.set_last_notified_version("0.2.0")
    await chat_status_service.set_status(-100111, ChatStatus.APPROVED)

    bot = recording_bot

    # Identical version
    monkeypatch.setattr(notification_module, "get_current_version", partial(_constant, "0.2.0"))
    await notification_service.notify_updates(bot)
    assert bot.session.calls == []

    # Downgrade
    monkeypatch.setattr(notification_module, "get_current_version", partial(_constant, "0.1.9"))
    await notification_service.notify_updates(bot)
    assert bot.session.calls == []


async def test_notify_updates_retry_uses_html_and_recovers(
    notification_service: NotificationService,
    test_container: AsyncContainer,
    recording_bot: Bot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that retry after rate limit sends HTML formatted message and catches errors."""
    system_service = await test_container.get(SystemStore)
    chat_status_service = await test_container.get(ChatStatusService)

    await system_service.set_last_notified_version("0.1.0")
    group_chat_id = -100999
    await chat_status_service.set_status(group_chat_id, ChatStatus.APPROVED)

    bot = recording_bot
    bot.session.responses.extend(
        [
            TelegramRetryAfter(
                method=SendMessage(chat_id=group_chat_id, text="synthetic"), message="Rate limit", retry_after=0
            ),
            ValueError("Retry failed"),
        ]
    )

    monkeypatch.setattr(notification_module, "get_current_version", partial(_constant, "0.2.0"))
    monkeypatch.setattr(notification_module, "get_changelog_updates", partial(_constant, "New features"))
    await notification_service.notify_updates(bot)

    assert len(bot.session.calls) == 2
    for call in bot.session.calls:
        assert call.model_dump()["parse_mode"] == "HTML"
        assert call.model_dump()["disable_web_page_preview"] is True
