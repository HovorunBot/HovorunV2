"""Telegram bot message handlers."""

from aiogram import Bot, Router, types

from hovorunv2.infrastructure.container import container
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.commands import get_commands
from hovorunv2.interface.telegram.commands.debug import DebugCommand
from hovorunv2.interface.telegram.commands.whitelist import AllowBotCommand

logger = get_logger(__name__)
router = Router()


@router.message()
async def handle_message(message: types.Message, bot: Bot) -> None:
    """Handle incoming telegram messages by checking registered commands."""
    logger.debug(
        "Received message from %s (ID: %d) in chat %d",
        message.from_user.username if message.from_user else "Unknown",
        message.message_id,
        message.chat.id,
    )

    if not container.message_service or not container.whitelist_service:
        logger.error("Container not initialized")
        return

    container.message_service.cache_message(message)

    is_whitelisted = await container.whitelist_service.is_whitelisted(message.chat.id)
    commands = get_commands()

    # Whitelist command (AllowBotCommand) should always be processed if triggered
    # Other commands should only be processed if the chat is whitelisted
    allowed_commands = (
        [commands[AllowBotCommand.__name__], commands[DebugCommand.__name__]]
        if not is_whitelisted
        else commands.values()
    )

    for command in allowed_commands:
        if await command.is_triggered(message):
            logger.info("Command %s triggered", command.__class__.__name__)
            await command.handle(message, bot)
            return

    if not is_whitelisted:
        logger.debug("Ignoring message in non-whitelisted chat %d", message.chat.id)
