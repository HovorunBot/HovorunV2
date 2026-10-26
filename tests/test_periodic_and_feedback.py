"""Tests for Feedback, ChatReminderService, and TaskQueueService components."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import types
from dishka import AsyncContainer

from hovorunv2.application.ports import ChatStore
from hovorunv2.application.services.feedback_service import FeedbackService
from hovorunv2.application.services.owner_notifier import OwnerNotifier
from hovorunv2.application.services.reminder_service import (
    ChatReminderService,
    maintenance_cron_job,
    scheduled_unauthorized_reminder_job,
)
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, CommandName, Role
from hovorunv2.infrastructure.queue import TaskQueueService
from hovorunv2.interface.telegram.handlers.feedback import FeedbackCommand
from hovorunv2.interface.telegram.prompts import TelegramUnauthorizedPromptSender
from tests.conftest import provision_role


@pytest.fixture
async def owner_notifier(test_container: AsyncContainer) -> OwnerNotifier:
    """Provision synthetic durable owners for notification tests."""
    await provision_role(test_container, 111, Role.OWNER)
    await provision_role(test_container, 222, Role.OWNER)
    return await test_container.get(OwnerNotifier)


@pytest.mark.asyncio
async def test_feedback_service_submits_to_owners(owner_notifier: OwnerNotifier) -> None:
    """Test feedback service properly formats and delivers feedback to all owners."""
    service = FeedbackService(owner_notifier)
    bot = MagicMock()
    bot.send_message = AsyncMock()

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
    assert bot.send_message.call_count == 2
    called_owner_ids = [call.kwargs["chat_id"] for call in bot.send_message.call_args_list]
    assert 111 in called_owner_ids
    assert 222 in called_owner_ids

    sent_text = bot.send_message.call_args_list[0].kwargs["text"]
    assert "Alice Smith" in sent_text
    assert "Developers Chat" in sent_text
    assert "Please add Bluesky media support!" in sent_text


@pytest.mark.asyncio
async def test_feedback_command_validation_and_delegation() -> None:
    """Test feedback command requires message argument and delegates to service."""
    feedback_service = MagicMock(spec=FeedbackService)
    feedback_service.submit_feedback = AsyncMock(return_value=True)

    command = FeedbackCommand(feedback_service)
    assert command.name == CommandName.FEEDBACK
    assert not command.policy.requires_approval

    bot = MagicMock()

    # 1. Empty message text
    empty_msg = MagicMock(spec=types.Message, text="/feedback")
    empty_msg.reply = AsyncMock()
    await command.handle(empty_msg, bot)
    empty_msg.reply.assert_called_once()
    assert "How to send feedback" in empty_msg.reply.call_args[0][0]
    feedback_service.submit_feedback.assert_not_called()

    # 2. Valid message text
    valid_msg = MagicMock(spec=types.Message, text="/feedback Hello world")
    valid_msg.from_user = MagicMock(id=1, full_name="User", username="user")
    valid_msg.chat = MagicMock(id=2, title="Chat", type="group")
    valid_msg.reply = AsyncMock()

    await command.handle(valid_msg, bot)
    feedback_service.submit_feedback.assert_called_once_with(
        bot,
        user_id=1,
        user_name="User",
        username="user",
        chat_id=2,
        chat_title="Chat",
        chat_type="group",
        message_text="Hello world",
    )
    valid_msg.reply.assert_called_once()
    assert "Thank you!" in valid_msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_reminder_service_send_prompt_cooldown(
    owner_notifier: OwnerNotifier, test_container: AsyncContainer
) -> None:
    """Test ChatReminderService sets Valkey cooldown when sending unauthorized prompt."""
    chat_service = MagicMock()
    cache_service = MagicMock()
    cache_service.get = AsyncMock(return_value=None)
    cache_service.set = AsyncMock()
    task_queue = MagicMock(spec=TaskQueueService)
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(-100444)
    prompt_sender = TelegramUnauthorizedPromptSender(chats)

    reminder_service = ChatReminderService(chat_service, cache_service, task_queue, owner_notifier, prompt_sender)

    bot = MagicMock()
    bot.send_message = AsyncMock()

    sent = await reminder_service.send_unauthorized_prompt(bot, -100444, force=False)
    assert sent is True
    bot.send_message.assert_called_once()
    cache_service.set.assert_called_once_with("periodic:unauthorized_prompt:-100444", 1, expire=86400)

    # Calling again while in cooldown returns False and does not send
    cache_service.get.return_value = 1
    bot.send_message.reset_mock()
    sent_again = await reminder_service.send_unauthorized_prompt(bot, -100444, force=False)
    assert sent_again is False
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_reminder_service_maintenance_cycle(
    owner_notifier: OwnerNotifier, test_container: AsyncContainer
) -> None:
    """Test maintenance cycle handles rejected chats and pending chats."""
    rejected_chat = MagicMock(spec=ChatDB, chat_id=-100111)
    pending_chat = MagicMock(spec=ChatDB, chat_id=-100222)

    chat_service = MagicMock()

    async def mock_get_all(status: ChatStatus, platform: str = "telegram") -> list[ChatDB]:
        if status == ChatStatus.REJECTED:
            return [rejected_chat]
        if status == ChatStatus.PENDING:
            return [pending_chat]
        return []

    chat_service.get_all_by_status = AsyncMock(side_effect=mock_get_all)

    cache_service = MagicMock()
    cache_service.get = AsyncMock(return_value=None)
    cache_service.set = AsyncMock()
    task_queue = MagicMock(spec=TaskQueueService)
    prompt_sender = await test_container.get(TelegramUnauthorizedPromptSender)

    reminder_service = ChatReminderService(chat_service, cache_service, task_queue, owner_notifier, prompt_sender)

    bot = MagicMock()
    bot.leave_chat = AsyncMock()
    bot.send_message = AsyncMock()

    await reminder_service.run_maintenance_cycle(bot)

    bot.leave_chat.assert_called_once_with(-100111)
    assert bot.send_message.call_count == 2  # Notified both durable owners
    assert "Pending Approval" in bot.send_message.call_args[1]["text"]


@pytest.mark.asyncio
async def test_scheduled_job_callbacks() -> None:
    """Test the standalone worker callback functions."""
    reminder_service = MagicMock(spec=ChatReminderService)
    reminder_service.send_unauthorized_prompt = AsyncMock()
    reminder_service.run_maintenance_cycle = AsyncMock()

    bot = MagicMock()
    ctx = {"reminder_service": reminder_service, "bot": bot}

    await scheduled_unauthorized_reminder_job(ctx, chat_id=-100999)
    reminder_service.send_unauthorized_prompt.assert_called_once_with(bot, -100999, force=False)

    await maintenance_cron_job(ctx)
    reminder_service.run_maintenance_cycle.assert_called_once_with(bot)
