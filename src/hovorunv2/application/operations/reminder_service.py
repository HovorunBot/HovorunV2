"""Application service for managing chat reminders and authorization prompts."""

import logging
from typing import Final

from aiogram import Bot

from hovorunv2.application.access.ports import UnauthorizedPromptSender
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.messaging.owner_notifier import OwnerNotifier
from hovorunv2.application.messaging.ports import CachePort
from hovorunv2.application.usage.budgets import RuntimeNamespace

logger = logging.getLogger(__name__)

UNAUTHORIZED_PROMPT_KEY_PREFIX: Final = "periodic:unauthorized_prompt"
DEFAULT_REMINDER_COOLDOWN_SECONDS: Final = 86400  # 24 hours
HOURLY_MAINTENANCE_CRON: Final = "0 * * * *"


class ChatReminderService:
    """Govern chat reminder scheduling, cooldowns, and maintenance.

    Orchestrates:
    - Dispatching unauthorized prompts with cooldown tracking in Valkey.
    - Preserving bot presence and restrictions in rejected chats.
    - Pinging bot owners about long-pending chat approvals.
    """

    def __init__(
        self,
        chat_service: ChatStore,
        cache_service: CachePort,
        owner_notifier: OwnerNotifier,
        prompt_sender: UnauthorizedPromptSender,
        namespace: RuntimeNamespace,
    ) -> None:
        """Initialize chat reminder service."""
        self._chat_service = chat_service
        self._cache_service = cache_service
        self._owner_notifier = owner_notifier
        self._prompt_sender = prompt_sender
        self._namespace = namespace

    async def send_unauthorized_prompt(
        self,
        bot: Bot,
        chat_id: int,
        *,
        force: bool = False,
    ) -> bool:
        """Send a prompt to an unauthorized chat when not in cooldown.

        Args:
            bot: Telegram bot instance used for delivery.
            chat_id: Telegram ID of the chat.
            force: Whether to bypass the cooldown.

        Returns:
            True when sent, False when throttled or delivery failed.

        """
        cooldown_key = f"{self._namespace.value}{UNAUTHORIZED_PROMPT_KEY_PREFIX}:{chat_id}"
        if not force and await self._cache_service.get(cooldown_key):
            logger.debug("Chat %d is in cooldown, skipping unauthorized reminder", chat_id)
            return False

        sent = await self._prompt_sender.send(bot, chat_id)
        if sent:
            await self._cache_service.set(cooldown_key, 1, expire=DEFAULT_REMINDER_COOLDOWN_SECONDS)
            logger.info("Sent unauthorized guidance prompt to chat %d", chat_id)
        return sent
