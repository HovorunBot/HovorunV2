"""Application service for managing chat reminders and authorization prompts."""

import asyncio
from typing import Any, Final

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.application.data.chat_service import ChatService
from hovorunv2.application.data.constants import ChatStatus
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.infrastructure.queue import TaskQueueService
from hovorunv2.interface.telegram.callbacks import AccessAction, AccessCallback

logger = get_logger(__name__)

UNAUTHORIZED_PROMPT_KEY_PREFIX: Final = "periodic:unauthorized_prompt"
DEFAULT_REMINDER_COOLDOWN_SECONDS: Final = 86400  # 24 hours
HOURLY_MAINTENANCE_CRON: Final = "0 * * * *"


def build_unauthorized_prompt(chat_id: int) -> tuple[str, InlineKeyboardBuilder]:
    """Generate guidance text and approval button for unauthorized chats."""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="🛡️ Request Approval",
            callback_data=AccessCallback(chat_id=chat_id, action=AccessAction.REQUEST).pack(),
        )
    )

    text = (
        "👋 <b>Hello! I am Hovorun — Your Media Companion</b>\n\n"
        "I enhance group conversations with fast media processing and automatic translation.\n\n"
        "⚠️ <b>Chat Authorization Required:</b>\n"
        "To keep performance reliable, I only operate in approved chats.\n"
        "A chat administrator can tap the button below or type <code>/allow_chat</code> "
        "to request access from the bot owners.\n\n"
        "💬 <i>Need help or have questions? Type <code>/feedback &lt;message&gt;</code> to contact developers.</i>"
    )
    return text, builder


async def scheduled_unauthorized_reminder_job(ctx: dict[str, Any], *, chat_id: int) -> None:
    """Worker task callback for sending an unauthorized chat reminder."""
    reminder_service: ChatReminderService = ctx["reminder_service"]
    bot: Bot = ctx["bot"]
    await reminder_service.send_unauthorized_prompt(bot, chat_id, force=False)


async def maintenance_cron_job(ctx: dict[str, Any]) -> None:
    """Worker cron callback executing hourly chat maintenance."""
    reminder_service: ChatReminderService = ctx["reminder_service"]
    bot: Bot = ctx["bot"]
    await reminder_service.run_maintenance_cycle(bot)


class ChatReminderService:
    """Business service governing chat reminder scheduling, cooldowns, and maintenance.

    Orchestrates:
    - Dispatching unauthorized prompts with cooldown tracking in Valkey.
    - Evicting bot from rejected chats.
    - Pinging bot owners about long-pending chat approvals.
    - Registering scheduled jobs with TaskQueueService.
    """

    def __init__(
        self,
        chat_service: ChatService,
        cache_service: CacheService,
        task_queue: TaskQueueService,
        settings: Settings,
    ) -> None:
        """Initialize chat reminder service."""
        self._chat_service = chat_service
        self._cache_service = cache_service
        self._task_queue = task_queue
        self._settings = settings

        # Register functions with background task queue
        self._task_queue.register_function(scheduled_unauthorized_reminder_job)
        self._task_queue.register_cron(maintenance_cron_job, cron=HOURLY_MAINTENANCE_CRON)

    async def send_unauthorized_prompt(
        self,
        bot: Bot,
        chat_id: int,
        *,
        force: bool = False,
    ) -> bool:
        """Send prompt to unauthorized chat if not currently in cooldown.

        Returns True if sent, False if throttled.
        """
        cooldown_key = f"{UNAUTHORIZED_PROMPT_KEY_PREFIX}:{chat_id}"
        if not force and await self._cache_service.get(cooldown_key):
            logger.debug("Chat %d is in cooldown, skipping unauthorized reminder", chat_id)
            return False

        text, builder = build_unauthorized_prompt(chat_id)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=builder.as_markup(),
            )
            await self._cache_service.set(cooldown_key, 1, expire=DEFAULT_REMINDER_COOLDOWN_SECONDS)
            logger.info("Sent unauthorized guidance prompt to chat %d", chat_id)
        except TelegramForbiddenError:
            logger.warning("Bot removed or blocked in chat %d", chat_id)
            return False
        except Exception:
            logger.exception("Failed to send unauthorized prompt to chat %d", chat_id)
            return False
        else:
            return True

    async def enqueue_unauthorized_reminder(self, chat_id: int) -> None:
        """Enqueue unauthorized reminder job to task queue."""
        await self._task_queue.enqueue("scheduled_unauthorized_reminder_job", chat_id=chat_id)

    async def run_maintenance_cycle(self, bot: Bot) -> None:
        """Execute full maintenance cycle across chats."""
        logger.info("Starting maintenance cycle")

        # 1. Leave rejected chats
        rejected_chats = await self._chat_service.get_all_by_status(ChatStatus.REJECTED, platform="telegram")
        for chat in rejected_chats:
            try:
                logger.info("Leaving rejected chat %d", chat.chat_id)
                await bot.leave_chat(chat.chat_id)
            except TelegramForbiddenError:
                pass
            except Exception:
                logger.exception("Failed to leave rejected chat %d", chat.chat_id)

        # 2. Check unauthorized group chats
        unauthorized_chats = await self._chat_service.get_all_by_status(ChatStatus.UNAUTHORIZED, platform="telegram")
        for chat in unauthorized_chats:
            if chat.chat_id >= 0:
                continue
            await self.send_unauthorized_prompt(bot, chat.chat_id, force=False)
            await asyncio.sleep(0.5)

        # 3. Check pending chats to remind owners
        pending_chats = await self._chat_service.get_all_by_status(ChatStatus.PENDING, platform="telegram")
        for chat in pending_chats:
            pending_key = f"periodic:pending_ping:{chat.chat_id}"
            if await self._cache_service.get(pending_key):
                continue

            for owner_id in self._settings.owners:
                try:
                    await bot.send_message(
                        chat_id=owner_id,
                        text=(
                            f"⏳ <b>Reminder: Pending Approval</b>\n\n"
                            f"Chat ID <code>{chat.chat_id}</code> is still awaiting your approval decision."
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    logger.exception("Failed to ping owner %d about pending chat %d", owner_id, chat.chat_id)

            await self._cache_service.set(pending_key, 1, expire=DEFAULT_REMINDER_COOLDOWN_SECONDS)
            await asyncio.sleep(0.5)

        logger.info("Maintenance cycle completed")
