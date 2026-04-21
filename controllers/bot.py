"""Telegram bot message handlers."""

from aiogram import Bot, Router, types

from controllers.commands import get_commands
from controllers.commands.whitelist import AllowBotCommand
from database import DatabaseService
from logger_conf import get_logger
from message_service import MessageService

logger = get_logger(__name__)
router = Router()


@router.message()
async def handle_message(
    message: types.Message, bot: Bot, message_service: MessageService, database_service: DatabaseService
) -> None:
    """Handle incoming telegram messages by checking registered commands."""
    logger.debug(
        "Received message from %s (ID: %d) in chat %d",
        message.from_user.username if message.from_user else "Unknown",
        message.message_id,
        message.chat.id,
    )
    message_service.cache_message(message)

    is_whitelisted = database_service.is_chat_whitelisted(message.chat.id)
    commands = get_commands()

    # Whitelist command (AllowBotCommand) should always be processed if triggered
    # Other commands should only be processed if the chat is whitelisted
    allowed_commands = [commands[AllowBotCommand.__name__]] if not is_whitelisted else commands.values()
    logger.info(command.__class__.__name__ for command in allowed_commands)

    for command in allowed_commands:
        if await command.is_triggered(message):
            logger.info("Command %s triggered", command.__class__.__name__)
            await command.handle(message, bot)
            return

    if not is_whitelisted:
        logger.debug("Ignoring message in non-whitelisted chat %d", message.chat.id)
