"""Handler for bot join events."""

from aiogram import Bot, Router
from aiogram.types import ChatMemberUpdated

from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.reminder_service import ChatReminderService
from hovorunv2.domain.enums import ChatStatus
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.handlers.membership import MembershipEventHandler

logger = get_logger(__name__)


class BotJoinHandler:
    """Handles events when the bot is added to a chat."""

    def __init__(
        self,
        chat_status_service: ChatStatusService,
        reminder_service: ChatReminderService,
        membership_handler: MembershipEventHandler,
    ) -> None:
        """Initialize handler."""
        self._chat_status_service = chat_status_service
        self._reminder_service = reminder_service
        self._membership_handler = membership_handler

    async def handle_my_chat_member_update(self, event: ChatMemberUpdated, bot: Bot) -> None:
        """Handle status change of the bot in a chat."""
        await self._membership_handler.handle_bot(event, bot)
        chat_id = event.chat.id
        new_status = event.new_chat_member.status

        # Bot was removed from chat
        if new_status in ("kicked", "left"):
            logger.info("Bot removed from chat %d. Preserving access status and grants.", chat_id)
            return

        if new_status not in ("member", "administrator"):
            return

        # Bot was added to a chat
        current_status = await self._chat_status_service.get_status(chat_id)

        if current_status == ChatStatus.APPROVED:
            logger.info("Bot added to already approved chat %d", chat_id)
            return

        if current_status in (ChatStatus.BANNED, ChatStatus.REJECTED):
            logger.info("Bot added to %s chat %d. Preserving restrictions.", current_status, chat_id)
            return

        if current_status == ChatStatus.PENDING:
            logger.info("Bot added to chat %d with status %s. Staying silent.", chat_id, current_status)
            return

        logger.info("Bot added to unauthorized chat %d (status: %s). Sending prompt.", chat_id, current_status)
        await self._chat_status_service.set_status(chat_id, ChatStatus.UNAUTHORIZED)
        await self._reminder_service.send_unauthorized_prompt(bot, chat_id, force=True)

    def register(self, router: Router) -> None:
        """Register the handler with the router."""
        router.my_chat_member.register(self.handle_my_chat_member_update)
        self._membership_handler.register(router)
