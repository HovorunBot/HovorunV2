"""Telegram bot message handlers."""

from aiogram import Bot, Router, types

from controllers.commands import get_commands
from logger_conf import get_logger
from services.message import MessageService

logger = get_logger(__name__)
router = Router()


@router.message()
async def handle_message(message: types.Message, bot: Bot, message_service: MessageService) -> None:
    """Handle incoming telegram messages by checking registered commands."""
    logger.debug(
        "Received message from %s (ID: %d)",
        message.from_user.username if message.from_user else "Unknown",
        message.message_id,
    )
    message_service.cache_message(message)

    for command in get_commands():
        if await command.is_triggered(message):
            logger.info("Command %s triggered", command.__class__.__name__)
            await command.handle(message, bot)
            return
