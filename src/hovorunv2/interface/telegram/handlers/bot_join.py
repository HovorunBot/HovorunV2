"""Handler for bot join events."""

from aiogram import Bot, Router
from aiogram.types import ChatMemberUpdated, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.application.data.constants import ChatStatus
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.callbacks import AccessAction, AccessCallback

logger = get_logger(__name__)


class BotJoinHandler:
    """Handles events when the bot is added to a chat."""

    def __init__(self, chat_status_service: ChatStatusService) -> None:
        """Initialize handler."""
        self._chat_status_service = chat_status_service

    async def handle_my_chat_member_update(self, event: ChatMemberUpdated, bot: Bot) -> None:
        """Handle status change of the bot in a chat."""
        chat_id = event.chat.id
        new_status = event.new_chat_member.status

        # Bot was removed from chat
        if new_status in ("kicked", "left"):
            logger.info("Bot removed from chat %d. Setting status to INACTIVE.", chat_id)
            await self._chat_status_service.set_status(chat_id, ChatStatus.INACTIVE)
            return

        if new_status not in ("member", "administrator"):
            return

        # Bot was added to a chat
        current_status = await self._chat_status_service.get_status(chat_id)

        if current_status == ChatStatus.APPROVED:
            logger.info("Bot added to already approved chat %d", chat_id)
            return

        if current_status == ChatStatus.BANNED:
            logger.warning("Bot added to BANNED chat %d. Leaving immediately.", chat_id)
            await bot.leave_chat(chat_id)
            return

        if current_status in (ChatStatus.PENDING, ChatStatus.REJECTED):
            logger.info("Bot added to chat %d with status %s. Staying silent.", chat_id, current_status)
            return

        logger.info("Bot added to unauthorized chat %d (status: %s). Sending prompt.", chat_id, current_status)

        builder = InlineKeyboardBuilder()
        builder.add(
            InlineKeyboardButton(
                text="🛡️ Request Approval",
                callback_data=AccessCallback(chat_id=chat_id, action=AccessAction.REQUEST).pack(),
            )
        )

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "👋 <b>Hello! I am Hovorun.</b>\n\n"
                "I need to be approved by my owners before I can start working in this chat.\n"
                "Please press the button below to request approval."
            ),
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )

    def register(self, router: Router) -> None:
        """Register the handler with the router."""
        router.my_chat_member.register(self.handle_my_chat_member_update)
