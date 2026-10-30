"""Tests for Feedback, ChatReminderService, and TaskQueueService components."""

import pytest
from aiogram import Bot
from aiogram.types import Message
from dishka import AsyncContainer

from hovorunv2.application.ports import CachePort, ChatStore
from hovorunv2.application.services.feedback_service import FeedbackService
from hovorunv2.application.services.owner_notifier import OwnerNotifier
from hovorunv2.application.services.reminder_service import (
    ChatReminderService,
    maintenance_cron_job,
    scheduled_unauthorized_reminder_job,
)
from hovorunv2.domain.enums import CommandName, Role
from hovorunv2.interface.telegram.handlers.feedback import FeedbackCommand
from tests.conftest import provision_role


@pytest.fixture
async def owner_notifier(test_container: AsyncContainer) -> OwnerNotifier:
    """Provision synthetic durable owners for notification tests."""
    await provision_role(test_container, 111, Role.OWNER)
    await provision_role(test_container, 222, Role.OWNER)
    return await test_container.get(OwnerNotifier)


async def test_feedback_service_submits_to_owners(owner_notifier: OwnerNotifier, recording_bot: Bot) -> None:
    """Test feedback service properly formats and delivers feedback to all owners."""
    service = FeedbackService(owner_notifier)
    bot = recording_bot

    delivered = await service.submit_feedback(
        bot,
        user_id=12345,
        user_name="Alice Smith",
        username="alicesmith",
        chat_id=-100987,
        chat_title="Developers Chat",
        chat_type="supergroup",
        message_text="Please add Bluesky media support!",
    )

    assert delivered is True
    assert len(bot.session.calls) == 2
    called_owner_ids = [call.chat_id for call in bot.session.calls]
    assert 111 in called_owner_ids
    assert 222 in called_owner_ids

    sent_text = bot.session.calls[0].text
    assert "Alice Smith" in sent_text
    assert "Developers Chat" in sent_text
    assert "Please add Bluesky media support!" in sent_text


async def test_feedback_command_validation_and_delegation(
    owner_notifier: OwnerNotifier,
    recording_bot: Bot,
) -> None:
    """Validate input and deliver through the real feedback service to durable owners."""
    command = FeedbackCommand(FeedbackService(owner_notifier))
    assert command.name == CommandName.FEEDBACK
    assert not command.policy.requires_approval
    empty_msg = Message.model_validate(
        {
            "message_id": 1,
            "date": 1,
            "chat": {"id": 2, "title": "Chat", "type": "group"},
            "from_user": {"id": 1, "first_name": "User", "username": "user", "is_bot": False},
            "text": "/feedback",
        }
    ).as_(recording_bot)
    await command.handle(empty_msg, recording_bot)
    assert len(recording_bot.session.calls) == 1
    assert "How to send feedback" in recording_bot.session.calls[0].text
    recording_bot.session.calls.clear()
    valid_msg = empty_msg.model_copy(update={"text": "/feedback Hello world"}).as_(recording_bot)
    await command.handle(valid_msg, recording_bot)
    assert len(recording_bot.session.calls) == 3
    deliveries = recording_bot.session.calls[:2]
    assert {call.chat_id for call in deliveries} == {111, 222}
    for call in deliveries:
        assert "User" in call.text
        assert "Chat" in call.text
        assert "Hello world" in call.text
    assert "Thank you!" in recording_bot.session.calls[-1].text


async def test_reminder_service_send_prompt_cooldown(
    owner_notifier: OwnerNotifier, test_container: AsyncContainer, recording_bot: Bot
) -> None:
    """Test ChatReminderService sets Valkey cooldown when sending unauthorized prompt."""
    reminder_service = await test_container.get(ChatReminderService)
    cache_service = await test_container.get(CachePort)
    await cache_service.delete("periodic:unauthorized_prompt:-100444")
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(-100444)
    bot = recording_bot

    sent = await reminder_service.send_unauthorized_prompt(bot, -100444, force=False)
    assert sent is True
    assert len(bot.session.calls) == 1
    assert await cache_service.get("periodic:unauthorized_prompt:-100444") == 1

    # Calling again while in cooldown returns False and does not send
    bot.session.calls.clear()
    sent_again = await reminder_service.send_unauthorized_prompt(bot, -100444, force=False)
    assert sent_again is False
    assert bot.session.calls == []


async def test_scheduled_job_callbacks(test_container: AsyncContainer, recording_bot: Bot) -> None:
    """Run worker callbacks through real reminder service and check durable cooldown."""
    reminder_service = await test_container.get(ChatReminderService)
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(-100999)
    cache = await test_container.get(CachePort)
    await cache.delete("periodic:unauthorized_prompt:-100999")
    ctx = {"reminder_service": reminder_service, "bot": recording_bot}
    await scheduled_unauthorized_reminder_job(ctx, chat_id=-100999)
    assert await cache.get("periodic:unauthorized_prompt:-100999") == 1
    assert len(recording_bot.session.calls) == 1
    await maintenance_cron_job(ctx)
    assert len(recording_bot.session.calls) == 1
