"""Telegram bot message handlers."""

from aiogram import Router

from hovorunv2.interface.telegram.handlers import get_commands
from hovorunv2.interface.telegram.middlewares import (
    CommandConfigurationMiddleware,
    MessageCacheMiddleware,
    WhitelistMiddleware,
)

router = Router()

# Register middlewares
router.message.outer_middleware(MessageCacheMiddleware())
router.message.middleware(WhitelistMiddleware())
router.message.middleware(CommandConfigurationMiddleware())


def register_handlers() -> None:
    """Register all command handlers with the router."""
    commands = get_commands()

    for command in commands.values():
        flags = {
            "command_name": command.name,
            "bypass_whitelist": command.BYPASS_WHITELIST,
        }

        # Register the command handler with its custom trigger filter
        router.message.register(
            command.handle,
            command.is_triggered,
            flags=flags,
        )


# Initialize handlers
register_handlers()
