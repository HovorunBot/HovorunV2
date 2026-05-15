"""Handler for bot join events."""

from aiogram import Bot, Router
from aiogram.types import ChatMemberUpdated, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.callbacks import WhitelistAction, WhitelistCallback

logger = get_logger(__name__)


class BotJoinHandler:
    """Handles events when the bot is added to a chat."""

    def __init__(self, whitelist_service: WhitelistService) -> None:
        """Initialize handler."""
        self._whitelist_service = whitelist_service

    async def handle_my_chat_member_update(self, event: ChatMemberUpdated, bot: Bot) -> None:
        """Handle status change of the bot in a chat."""
        if event.new_chat_member.status not in ("member", "administrator"):
            return

        # Bot was added to a chat
        chat_id = event.chat.id
        if await self._whitelist_service.is_whitelisted(chat_id):
            logger.info("Bot added to already whitelisted chat %d", chat_id)
            return

        logger.info("Bot added to unverified chat %d. Sending verification prompt.", chat_id)

        builder = InlineKeyboardBuilder()
        builder.add(
            InlineKeyboardButton(
                text="🛡️ Request Approval",
                callback_data=WhitelistCallback(chat_id=chat_id, action=WhitelistAction.REQUEST).pack(),
            )
        )

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "👋 <b>Hello! I am Hovorun.</b>\n\n"
                "I need to be verified by my owners before I can start working in this chat.\n"
                "Please press the button below to request approval."
            ),
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )

    def register(self, router: Router) -> None:
        """Register the handler with the router."""
        router.my_chat_member.register(self.handle_my_chat_member_update)
