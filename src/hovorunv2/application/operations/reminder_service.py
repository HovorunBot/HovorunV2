"""Application service for managing chat reminders and authorization prompts."""

import asyncio
import logging
from typing import Final

from aiogram import Bot

from hovorunv2.application.access.ports import UnauthorizedPromptSender
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.messaging.owner_notifier import OwnerNotifier
from hovorunv2.application.messaging.ports import CachePort
from hovorunv2.domain.enums import ChatStatus

logger = logging.getLogger(__name__)

UNAUTHORIZED_PROMPT_KEY_PREFIX: Final = "periodic:unauthorized_prompt"
DEFAULT_REMINDER_COOLDOWN_SECONDS: Final = 86400  # 24 hours
HOURLY_MAINTENANCE_CRON: Final = "0 * * * *"


class ChatReminderService:
    """Business service governing chat reminder scheduling, cooldowns, and maintenance.

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
    ) -> None:
        """Initialize chat reminder service."""
        self._chat_service = chat_service
        self._cache_service = cache_service
        self._owner_notifier = owner_notifier
        self._prompt_sender = prompt_sender

    async def send_unauthorized_prompt(
        self,
        bot: Bot,
        chat_id: int,
        *,
        force: bool = False,
    ) -> bool:
        """Send prompt to unauthorized chat if not currently in cooldown.

        Returns:
            True if sent, False if throttled or delivery failed.

        """
        cooldown_key = f"{UNAUTHORIZED_PROMPT_KEY_PREFIX}:{chat_id}"
        if not force and await self._cache_service.get(cooldown_key):
            logger.debug("Chat %d is in cooldown, skipping unauthorized reminder", chat_id)
            return False

        sent = await self._prompt_sender.send(bot, chat_id)
        if sent:
            await self._cache_service.set(cooldown_key, 1, expire=DEFAULT_REMINDER_COOLDOWN_SECONDS)
            logger.info("Sent unauthorized guidance prompt to chat %d", chat_id)
        return sent

    async def run_maintenance_cycle(self, bot: Bot) -> None:
        """Execute full maintenance cycle across chats."""
        logger.info("Starting maintenance cycle")

        # 2. Check unauthorized group chats
        unauthorized_chats = await self._chat_service.get_all_by_status(ChatStatus.UNAUTHORIZED)
        for chat in unauthorized_chats:
            if chat.chat_id >= 0:
                continue
            await self.send_unauthorized_prompt(bot, chat.chat_id, force=False)
            await asyncio.sleep(0.5)

        # 3. Check pending chats to remind owners
        pending_chats = await self._chat_service.get_all_by_status(ChatStatus.PENDING)
        for chat in pending_chats:
            pending_key = f"periodic:pending_ping:{chat.chat_id}"
            if await self._cache_service.get(pending_key):
                continue

            await self._owner_notifier.broadcast(
                bot,
                (
                    f"⏳ <b>Reminder: Pending Approval</b>\n\n"
                    f"Chat ID <code>{chat.chat_id}</code> is still awaiting your approval decision."
                ),
            )

            await self._cache_service.set(pending_key, 1, expire=DEFAULT_REMINDER_COOLDOWN_SECONDS)
            await asyncio.sleep(0.5)

        logger.info("Maintenance cycle completed")
